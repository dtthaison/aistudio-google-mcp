"""Create and update AI Studio Build apps (spec §6.7). Page-driven in a minimized window."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from aistudio._app.apps import pick
from aistudio._browser.build_page import BuildOutcome, BuildPage, app_url
from aistudio._browser.ui import open_browser
from aistudio.client import AIStudioClient
from aistudio.exceptions import ValidationError
from aistudio.paths import Profile

DEFAULT_TIMEOUT_S = 600


async def create_app(
    profile: Profile,
    idea: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    visible: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not idea.strip():
        raise ValidationError("nothing to build: the idea is empty")
    started = now()
    async with client_factory(profile) as client:
        await client.apps.list(1)  # fails fast with AUTH_ERROR before any browser opens
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        outcome = await BuildPage(ui).create(idea, timeout_s=timeout_s)
    return await _result(profile, outcome, started, now, client_factory)


async def prompt_app(
    profile: Profile,
    app_ref: str,
    text: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    visible: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not text.strip():
        raise ValidationError("nothing to change: the prompt is empty")
    started = now()
    async with client_factory(profile) as client:
        app = pick(await client.apps.list(), lambda a: a.id, app_ref, noun="app",
                   hint='see "aistudio app list"')
    if app.storage != "applet":
        raise ValidationError(
            f"{app.title or app.id!r} is stored in Google Drive (an older Build app); "
            "aistudio can only update Build-store apps",
            hint="open it at https://aistudio.google.com/apps",
        )
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        outcome = await BuildPage(ui).prompt(app.id, text, timeout_s=timeout_s)
    return await _result(profile, outcome, started, now, client_factory)


async def _result(
    profile: Profile,
    outcome: BuildOutcome,
    started: float,
    now: Callable[[], float],
    client_factory: Callable[[Profile], AIStudioClient],
) -> dict[str, Any]:
    async with client_factory(profile) as client:
        title = next((a.title for a in await client.apps.list() if a.id == outcome.app_id), None)
    progress = outcome.progress
    return {
        "app_id": outcome.app_id,
        "run_id": outcome.run_id,
        "url": app_url(outcome.app_id),
        "title": title,
        "status": "done" if progress else "timeout",
        "steps": progress.steps if progress else None,
        "files": list(progress.files) if progress else [],
        "seconds": round(now() - started),
    }
