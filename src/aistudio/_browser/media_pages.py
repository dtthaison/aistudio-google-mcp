"""Drive AI Studio's image and music pages (spec §5.4 page driving, §6.4 media).

Mapped by the free probes in docs/wire/findings.md ("Image and music generation"). A Gemini
image model opens the chat page with an image prompt box, and ⌘↵ runs it. A Lyria model's
chat URL redirects to the music page. Its prompt sits behind a "Describe the music…" button,
and it runs from "Generate" (⌘↵ does nothing there). Both send ``GenerateContent``.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, ResponseLog, Ui
from aistudio.exceptions import BrowserError

CHAT_URL = "https://aistudio.google.com/prompts/new_chat?model={model}"
GENERATE_METHOD = "GenerateContent"
IMAGE_PROMPT = "Enter a prompt"
MUSIC_EDITOR = re.compile(r"^Describe the music")
MUSIC_PROMPT = "Prompt input"
GENERATE = "Generate"


def _url(model_id: str) -> str:
    return CHAT_URL.format(model=quote(model_id, safe="-._"))


async def _await_generation(log: ResponseLog, kind: str, timeout_s: float) -> CapturedResponse:
    response = await log.next(GENERATE_METHOD, timeout_s=timeout_s)
    if response is None:
        raise BrowserError(f"no {kind} was generated within {timeout_s:.0f}s",
                           hint="it may still finish and be charged: check AI Studio before "
                                "re-running (add --visible to watch it)")
    return response


class ImagePage:
    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    async def open(self, model_id: str) -> None:
        await self._ui.goto(_url(model_id))

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
