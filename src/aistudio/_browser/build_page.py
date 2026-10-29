"""Drive AI Studio Build to create or update an app (spec §6.7, §5.4 page driving).

The page sends CreateApplet (create only), then CodeAssistantOffline (BotGuard-protected; returns
the run id), then follows the run through StreamCodeAssistantOfflineGeneration, whose response
completes with the final model message when the build is done (docs/wire/findings.md).

Sometimes the builder makes two options at once instead (findings, "The two options"): no
CodeAssistantOffline reply comes, and the app's editor lists the options in
GetPendingArenaInteraction. Each option streams as its own run; once both are built, "Skip
comparison" keeps the option the page shows first (ResolveArenaInteraction).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from aistudio._browser.ui import CapturedResponse, ResponseLog, Ui
from aistudio._rpc import wire
from aistudio._rpc.rows import as_str
from aistudio._rpc.rows.build import (
    decode_arena_options,
    decode_build_stream,
    decode_created_app,
    decode_started_run,
)
from aistudio._transport.http import error_for_response
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import AIStudioError, AuthError, BotGuardError, BrowserError

APPS_URL = "https://aistudio.google.com/apps"
# /apps has a contenteditable text field (live 2026-10-29); the app editor keeps a textarea
PROMPT_BOX = "[role=textbox][contenteditable], textarea"
SEND_BUTTON = "Send"  # the submit control of /apps and of the app editor
START_TIMEOUT_S = 60.0
CONTROL_WAIT_S = 30.0
CREATE = "CreateApplet"
START = "CodeAssistantOffline"
STREAM = "StreamCodeAssistantOfflineGeneration"
OPTIONS = "GetPendingArenaInteraction"
CHOOSE = "ResolveArenaInteraction"
# Keeps the option shown first; the A/B letters are shuffled on every page load (live)
SKIP_COMPARISON = "Skip comparison"


def app_url(app_id: str) -> str:
    return f"{APPS_URL}/{app_id}"


def _request_field(request_body: str, index: int) -> str | None:
    try:
        return as_str(wire.opt_index(wire.decode(request_body), index))
    except ValueError:
        return None


def _requested_run(request_body: str) -> str | None:
    """The run id a StreamCodeAssistantOfflineGeneration request asked for (``[run_id, ...]``)."""
    return _request_field(request_body, 0)


@dataclass(frozen=True)
class BuildOutcome:
    app_id: str
    run_id: str | None  # the kept option's run when the builder made options
    progress: BuildProgress | None  # None: --timeout passed before the build finished
    options: tuple[str, ...] = ()  # option app ids, when the builder made several at once
    kept: str | None = None  # the option app that was kept


class BuildPage:
    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ui = ui
        self._now = now

    async def create(self, idea: str, *, timeout_s: float) -> BuildOutcome:
        deadline = self._now() + timeout_s  # --timeout covers the start waits too
        async with self._ui.responses() as log:
            await self._ui.goto(APPS_URL)
            await self._submit(idea, deadline=deadline)
            created = await self._expect(log, CREATE, deadline)
            app_id = decode_created_app(wire.decode(created.text))
            try:
                started = await self._reply(log, START, deadline)
                if started is not None:
                    return await self._follow(log, deadline, started)
                outcome = await self._choose(log, app_id, deadline)
                if outcome is None:
                    raise BrowserError(f"AI Studio did not send {START} or build options",
                                       hint="re-run; if it persists, the AI Studio UI changed")
                return outcome
            except AIStudioError as exc:
                # The app shell already exists; never leave the user with an unnamed orphan.
                note = f"the app was already created at {app_url(app_id)}"
                exc.hint = f"{note}; {exc.hint}" if exc.hint else note
                raise

    async def prompt(self, app_id: str, text: str, *, timeout_s: float) -> BuildOutcome:
        deadline = self._now() + timeout_s
        async with self._ui.responses() as log:
            await self._ui.goto(app_url(app_id))
            # The editor disables Send for ~1s while it loads the app (docs/wire/findings.md).
            await self._submit(text, deadline=deadline)
            return await self._follow(log, deadline)

    async def keep_first_option(self, app_id: str, *, timeout_s: float) -> BuildOutcome:
        """Wait for the options an app's builder made at once, then keep the first."""
        deadline = self._now() + timeout_s
        async with self._ui.responses() as log:
            outcome = await self._choose(log, app_id, deadline)
        if outcome is None:
            raise BrowserError(f"{app_url(app_id)} has no options waiting for a choice")
        return outcome

    def _left(self, deadline: float, cap: float) -> float:
        """Seconds a start wait may take: at most ``cap``, never past --timeout."""
        remaining = deadline - self._now()
        if remaining <= 0:
            raise BrowserError("AI Studio did not start the build within --timeout",
                               hint="re-run with a larger --timeout")
        return min(cap, remaining)

    async def _submit(self, text: str, *, deadline: float) -> None:
        """Fill the box and press Send. Not ⌘↵: the run shortcut is an account setting, and on
        an Enter account ⌘↵ only asks for prompt suggestions (live)."""
        await self._ui.fill_first(PROMPT_BOX, text, timeout_s=self._left(deadline, CONTROL_WAIT_S))
        await self._ui.wait_enabled("button", SEND_BUTTON,
                                    timeout_s=self._left(deadline, CONTROL_WAIT_S))
        await self._ui.activate("button", SEND_BUTTON, exact=True)

    async def _expect(self, log: ResponseLog, method: str, deadline: float) -> CapturedResponse:
        wait = self._left(deadline, START_TIMEOUT_S)
        response = await self._reply(log, method, deadline)
        if response is None:
            raise BrowserError(
                f"AI Studio did not send {method} within {wait:.0f}s",
                hint="re-run; if it persists, the AI Studio UI changed",
            )
        return response

    async def _reply(self, log: ResponseLog, method: str,
                     deadline: float) -> CapturedResponse | None:
        """The next ``method`` reply within a start wait; None if none came; errors raised."""
        response = await log.next(method, timeout_s=self._left(deadline, START_TIMEOUT_S))
        if response is None:
            return None
        if response.status >= 400:
            err = error_for_response(response.status, response.text)
            if method == START and response.status == 403 and isinstance(err, AuthError):
                raise BotGuardError(f"Google refused the build request: {err.message}",
                                    hint="re-run; if it keeps failing, add --visible")
            raise err
        return response

    async def _follow(self, log: ResponseLog, deadline: float,
                      started: CapturedResponse | None = None) -> BuildOutcome:
        started = started or await self._expect(log, START, deadline)
        run_id, app_id = decode_started_run(wire.decode(started.text))
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

    async def _choose(self, log: ResponseLog, app_id: str, deadline: float) -> BuildOutcome | None:
        """Open the editor, follow every option's run, and keep the first once all are built.
        None when the app has no options waiting."""
        await self._ui.goto(app_url(app_id))
        options: tuple[tuple[str, str], ...] = ()
        while not options:  # /apps may have asked too early and got []
            listed = await self._reply(log, OPTIONS, deadline)
            if listed is None:
                return None
            try:
                options = decode_arena_options(wire.decode(listed.text))
            except ValueError:
                continue
        runs = {run: option for option, run in options}
        built: dict[str, BuildProgress] = {}
        while len(built) < len(runs) and (remaining := deadline - self._now()) > 0:
            response = await log.next(STREAM, timeout_s=remaining)
            if response is None:
                break
            if response.status >= 400:
                if _requested_run(response.request_body) in runs:
                    raise error_for_response(response.status, response.text)
                continue
            try:
                progress = decode_build_stream(wire.decode(response.text))
            except ValueError:
                continue
            if progress.run_id in runs and progress.done:
                built[progress.run_id] = progress
        option_ids = tuple(option for option, _ in options)
        if len(built) < len(runs):  # never choose while an option is unfinished
            return BuildOutcome(app_id, None, None, option_ids)
        await self._ui.wait_enabled("button", SKIP_COMPARISON,
                                    timeout_s=self._left(deadline, CONTROL_WAIT_S))
        await self._ui.activate("button", SKIP_COMPARISON, exact=True)
        chosen = await self._expect(log, CHOOSE, deadline)
        kept = _request_field(chosen.request_body, 2)
        run = next((r for r, option in runs.items() if option == kept), None)
        return BuildOutcome(app_id, run, built.get(run) if run else None, option_ids, kept)
