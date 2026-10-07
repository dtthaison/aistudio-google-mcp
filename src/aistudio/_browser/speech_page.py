"""Drive AI Studio's speech page (spec §5.4 page driving, §6.4 generate speech).

Controls are located by accessible role and name as mapped in docs/wire/findings.md
("TTS end-to-end drive"). BotGuard refuses headless generation; a minimized window is used.
"""

from __future__ import annotations

import re

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, Ui
from aistudio.exceptions import BrowserError, ValidationError

SPEECH_URL = "https://aistudio.google.com/generate-speech"
GENERATE = "GenerateContent"
CREATE_DIALOG = re.compile(r"Create new dialog")
TEXT_BOX = "Speech block text"
MODEL_BUTTON = re.compile(r"gemini-[\w.-]*tts")
MODEL_SEARCH = "Search"
SPEAKER = re.compile(r"^Speaker 1 - ")
VOICE_SEARCH = "Search voices"
STYLE_BUTTON = "Style"
STYLE_BOX = "Describe the voice style"
VIEW_ALL = re.compile(r"^View all \d+")
BACK = re.compile(r"back", re.I)
FAVORITE = re.compile(r"^(?:Favorite|Remove from favorites) (\S+)$")


def voice_display_name(voice: str) -> str:
    name = voice.strip()
    return name[:1].upper() + name[1:].lower()


class SpeechPage:
    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    async def open(self) -> None:
        await self._ui.goto(SPEECH_URL)
        await self._ui.click("button", CREATE_DIALOG)

    async def select_model(self, model_id: str) -> None:
        current = await self._ui.labels("button", MODEL_BUTTON)
        if current and model_id in current[0].split():
            return
        await self._ui.click("button", MODEL_BUTTON)
        await self._ui.fill("textbox", MODEL_SEARCH, model_id)
        await self._ui.pause(1500)
        option = re.compile(re.escape(model_id) + r"(?![\w.-])")
        if not await self._ui.visible("button", option):
            await self._ui.key("Escape")
            raise ValidationError(f"the speech page has no model {model_id!r}",
                                  hint='see "aistudio models list --category audio"')
        await self._ui.click("button", option)
        await self._ui.pause(1000)

    async def select_voice(self, voice: str) -> str:
        name = voice_display_name(voice)
        if await self._speaker_is(name):
            return name
        await self._ui.click("button", SPEAKER)
        await self._ui.fill("textbox", VOICE_SEARCH, name)
        await self._ui.pause(1500)
        if not await self._ui.visible("button", name, exact=True):
            await self._ui.key("Escape")
            raise ValidationError(f"no voice named {name!r}", hint='see "aistudio voices list"')
        await self._ui.click("button", name, exact=True)
        await self._ui.pause(1000)
        if await self._ui.visible("textbox", VOICE_SEARCH):
            await self._ui.key("Escape")  # the panel stays open after a choice
        if not await self._speaker_is(name):
            raise BrowserError(f"choosing voice {name!r} did not take effect",
                               hint="re-run; if it persists, the AI Studio UI changed")
        return name

    async def set_style(self, style: str) -> None:
        await self._ui.click("button", STYLE_BUTTON, exact=True)
        await self._ui.fill("textbox", STYLE_BOX, style)

    async def set_text(self, text: str) -> None:
        await self._ui.fill("textbox", TEXT_BOX, text)

    async def run(self, *, timeout_s: float = 180.0) -> CapturedResponse:
        async with self._ui.responses() as log:
            await self._ui.press("textbox", TEXT_BOX, RUN_SHORTCUT)
            response = await log.next(GENERATE, timeout_s=timeout_s)
        if response is None:
            raise BrowserError(f"no speech was generated within {timeout_s:.0f}s",
                               hint="re-run; if it persists, add --visible")
        return response

    async def list_voices(self) -> list[str]:
        await self._ui.click("button", SPEAKER)
        names = await self._voice_names()
        groups = len(await self._ui.labels("button", VIEW_ALL))
        for index in range(groups):
            await self._ui.click("button", VIEW_ALL, nth=index)
            await self._ui.pause(1000)
            names += await self._voice_names()
            await self._ui.click("button", BACK)
            await self._ui.pause(500)
        await self._ui.key("Escape")
        return sorted(set(names))

    async def _speaker_is(self, name: str) -> bool:
        labels = await self._ui.labels("button", SPEAKER)
        return bool(labels) and labels[0].split(" - ", 1)[-1].split()[0] == name

    async def _voice_names(self) -> list[str]:
        names = []
        for label in await self._ui.labels("button", FAVORITE):
            match = FAVORITE.match(label)
            if match:
                names.append(match.group(1))
        return names
