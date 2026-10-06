"""Drive AI Studio Build to create or update an app (spec §6.7, §5.4 page driving).

The page sends CreateApplet (create only), then CodeAssistantOffline (BotGuard-protected; returns
the run id), then follows the run through StreamCodeAssistantOfflineGeneration, whose response
completes with the final model message when the build is done (docs/wire/findings.md).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, ResponseLog, Ui
from aistudio._rpc import wire
from aistudio._rpc.rows import as_str
from aistudio._rpc.rows.build import decode_build_stream, decode_created_app, decode_started_run
from aistudio._transport.http import error_for_response
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import AIStudioError, AuthError, BotGuardError, BrowserError

APPS_URL = "https://aistudio.google.com/apps"
PROMPT_BOX = "textarea"
SEND_BUTTON = "Send"  # the app editor's submit control
START_TIMEOUT_S = 60.0
CREATE = "CreateApplet"
START = "CodeAssistantOffline"
STREAM = "StreamCodeAssistantOfflineGeneration"


def app_url(app_id: str) -> str:
    return f"{APPS_URL}/{app_id}"


def _requested_run(request_body: str) -> str | None:
    """The run id a StreamCodeAssistantOfflineGeneration request asked for (``[run_id, ...]``)."""
    try:
        return as_str(wire.opt_index(wire.decode(request_body), 0))
    except ValueError:
        return None


@dataclass(frozen=True)
class BuildOutcome:
    app_id: str
    run_id: str
    progress: BuildProgress | None  # None: --timeout passed before the build finished


class BuildPage:
    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ui = ui
        self._now = now

    async def create(self, idea: str, *, timeout_s: float) -> BuildOutcome:
        async with self._ui.responses() as log:
            await self._ui.goto(APPS_URL)
            await self._submit(idea)
            app_id = decode_created_app(wire.decode((await self._expect(log, CREATE)).text))
            try:
                return await self._follow(log, timeout_s)
            except AIStudioError as exc:
                # The app shell already exists; never leave the user with an unnamed orphan.
                note = f"the app was already created at {app_url(app_id)}"
                exc.hint = f"{note}; {exc.hint}" if exc.hint else note
                raise

    async def prompt(self, app_id: str, text: str, *, timeout_s: float) -> BuildOutcome:
        async with self._ui.responses() as log:
            await self._ui.goto(app_url(app_id))
            # The editor disables Send for ~1s while it loads the app; ⌘↵ pressed earlier is
            # silently ignored (docs/wire/findings.md). The /apps create page has no Send button.
            await self._submit(text, wait_for=SEND_BUTTON)
            return await self._follow(log, timeout_s)

    async def _submit(self, text: str, *, wait_for: str | None = None) -> None:
        await self._ui.fill_first(PROMPT_BOX, text)
        if wait_for:
            await self._ui.wait_enabled("button", wait_for)
        await self._ui.press_first(PROMPT_BOX, RUN_SHORTCUT)

    async def _expect(self, log: ResponseLog, method: str) -> CapturedResponse:
        response = await log.next(method, timeout_s=START_TIMEOUT_S)
        if response is None:
            raise BrowserError(
                f"AI Studio did not send {method} within {START_TIMEOUT_S:.0f}s",
                hint="re-run; if it persists, the AI Studio UI changed",
            )
        if response.status >= 400:
            err = error_for_response(response.status, response.text)
            if method == START and response.status == 403 and isinstance(err, AuthError):
                raise BotGuardError(f"Google refused the build request: {err.message}",
                                    hint="re-run; if it keeps failing, add --visible")
            raise err
        return response

    async def _follow(self, log: ResponseLog, timeout_s: float) -> BuildOutcome:
        run_id, app_id = decode_started_run(wire.decode((await self._expect(log, START)).text))
        deadline = self._now() + timeout_s
        while (remaining := deadline - self._now()) > 0:
            response = await log.next(STREAM, timeout_s=remaining)
            if response is None:
                break
            if response.status >= 400:
                if _requested_run(response.request_body) == run_id:
                    raise error_for_response(response.status, response.text)
                continue
            try:
                progress = decode_build_stream(wire.decode(response.text))
            except ValueError:  # an aborted or non-JSON stream body
                continue
            if progress.run_id == run_id and progress.done:
                return BuildOutcome(app_id, run_id, progress)
        return BuildOutcome(app_id, run_id, None)
