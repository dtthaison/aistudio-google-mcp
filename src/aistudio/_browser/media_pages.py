"""Drive AI Studio's image and music pages (spec §5.4 page driving, §6.4 media).

Mapped by the free probes in docs/wire/findings.md ("Image and music generation"). A Gemini
image model opens the chat page with an image prompt box, and ⌘↵ runs it. Input images (edits)
and audio (transcription) go through the page's file input; each shows a "Remove media" chip
(findings, "AI Studio transcription and image editing"). A Lyria model's
chat URL redirects to the music page. Its prompt sits behind a "Describe the music…" button,
and it runs from "Generate" (⌘↵ does nothing there). Both send ``GenerateContent``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import quote

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, ResponseLog, Ui
from aistudio.exceptions import BrowserError

CHAT_URL = "https://aistudio.google.com/prompts/new_chat?model={model}"
GENERATE_METHOD = "GenerateContent"
IMAGE_PROMPT = "Enter a prompt"
MUSIC_EDITOR = re.compile(r"^Describe the music")
MUSIC_PROMPT = "Prompt input"
GENERATE = "Generate"
FILE_INPUT = "input[type=file]"
REMOVE_MEDIA = "Remove media"  # one chip per attached file
TRANSCRIBE = "Transcribe"
VOCABULARY = "Custom vocabulary"
SWITCHES = {"timestamps": "Word timestamps", "speakers": "Speaker labels",
            "smart": "Smart transcription"}
ATTACH_TIMEOUT_S = 60.0


def _url(model_id: str) -> str:
    return CHAT_URL.format(model=quote(model_id, safe="-._"))


async def _await_generation(log: ResponseLog, kind: str, timeout_s: float) -> CapturedResponse:
    response = await log.next(GENERATE_METHOD, timeout_s=timeout_s)
    if response is None:
        raise BrowserError(f"no {kind} was generated within {timeout_s:.0f}s",
                           hint="it may still finish and be charged: check AI Studio before "
                                "re-running (add --visible to watch it)")
    return response


async def _attach(ui: Ui, paths: list[str], now: Callable[[], float]) -> None:
    """Give the page's file input these files and wait until each shows its chip, so Run never
    goes out without them (they upload to the user's Drive first)."""
    await ui.set_files(FILE_INPUT, paths)
    deadline = now() + ATTACH_TIMEOUT_S
    while now() < deadline:
        if len(await ui.labels("button", REMOVE_MEDIA)) >= len(paths):
            return
        await ui.pause(1000)
    raise BrowserError(f"AI Studio did not take {len(paths)} file(s); nothing was generated",
                       hint="check the files open on this computer; re-run with --visible")


class ImagePage:
    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ui = ui
        self._now = now

    async def open(self, model_id: str) -> None:
        await self._ui.goto(_url(model_id))

    async def attach(self, paths: list[str]) -> None:
        await _attach(self._ui, paths, self._now)

    async def set_prompt(self, text: str) -> None:
        await self._ui.fill("textbox", IMAGE_PROMPT, text)

    async def run(self, *, timeout_s: float = 180.0) -> CapturedResponse:
        async with self._ui.responses() as log:
            await self._ui.press("textbox", IMAGE_PROMPT, RUN_SHORTCUT)
            return await _await_generation(log, "image", timeout_s)


class MusicPage:
    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    async def open(self, model_id: str) -> None:
        await self._ui.goto(_url(model_id))  # redirects to the music page
        await self._ui.click("button", MUSIC_EDITOR)  # reveals the prompt box

    async def set_prompt(self, text: str) -> None:
        await self._ui.fill("textbox", MUSIC_PROMPT, text)

    async def run(self, *, timeout_s: float = 300.0) -> CapturedResponse:
        async with self._ui.responses() as log:
            await self._ui.click("button", GENERATE, exact=True)
            return await _await_generation(log, "music", timeout_s)


class TranscribePage:
    """The transcribe model's page: upload audio, set the run settings, press "Transcribe"."""

    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ui = ui
        self._now = now

    async def open(self, model_id: str) -> None:
        await self._ui.goto(_url(model_id))

    async def attach(self, paths: list[str]) -> None:
        await _attach(self._ui, paths, self._now)

    async def options(self, **wanted: bool | None) -> None:
        """Set each run-settings switch that was asked for; press it only when it differs."""
        for key, value in wanted.items():
            if value is None:
                continue
            label = SWITCHES[key]
            if await self._ui.checked("switch", label) != value:
                await self._ui.activate("switch", label, exact=True)
                await self._ui.pause(300)

    async def vocabulary(self, text: str) -> None:
        if text:
            await self._ui.fill("textbox", VOCABULARY, text)

    async def run(self, *, timeout_s: float = 300.0) -> CapturedResponse:
        async with self._ui.responses() as log:
            await self._ui.click("button", TRANSCRIBE, exact=True)
            return await _await_generation(log, "transcription", timeout_s)
