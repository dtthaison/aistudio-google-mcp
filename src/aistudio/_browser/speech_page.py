"""Drive AI Studio's speech page (spec §5.4 page driving, §6.4 generate speech).

Controls are located by accessible role and name as mapped in docs/wire/findings.md
("TTS end-to-end drive"). BotGuard refuses headless generation; a minimized window is used.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, Name, Ui
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
BACK = re.compile(r"^Back to library$")  # the voice-group view's back button
POLLS = 8  # x 250 ms: the panel's search filters asynchronously
LOAD_POLLS = 20  # x 250 ms: voice names render ~1 s after the panel opens (live probe)
FAVORITE = re.compile(r"^(?:Favorite|Remove from favorites) (\S+)$")
STYLE_SAVE_MS = 3000  # AI Studio saves the style box after a delay; a Run pressed sooner sends none


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
        if await self._model_is(model_id):
            return
        await self._ui.click("button", MODEL_BUTTON)
        await self._ui.fill("textbox", MODEL_SEARCH, model_id)
        option = re.compile(re.escape(model_id) + r"(?![\w.-])")
        if not await self._eventually_visible("button", option):
            await self._ui.key("Escape")
            raise ValidationError(f"the speech page has no model {model_id!r}",
                                  hint='see "aistudio models list --category audio"')
        await self._ui.click("button", option)
        if not await self._becomes(lambda: self._model_is(model_id)):
            raise BrowserError(f"choosing model {model_id!r} did not take effect",
                               hint="re-run; if it persists, the AI Studio UI changed")

    async def select_voice(self, voice: str) -> str:
        name = voice_display_name(voice)
        # The Speaker label renders after the page opens. Read too early, the current voice looks
        # unset, and its card ("Fola (Current)") then never matches the search.
        await self._loaded("button", SPEAKER)
        if await self._speaker_is(name):
            return name
        await self._ui.click("button", SPEAKER)
        await self._ui.fill("textbox", VOICE_SEARCH, name)
        if not await self._eventually_visible("button", name, exact=True):
            await self._ui.key("Escape")
            raise ValidationError(f"no voice named {name!r}", hint='see "aistudio voices list"')
        # Keyboard, not a click: in a minimized window the sliding voice panel can still be
        # off-screen, and a click on its card lands on the panel's backdrop (live probe).
        await self._ui.activate("button", name, exact=True)
        await self._ui.pause(500)
        if await self._ui.visible("textbox", VOICE_SEARCH):
            await self._ui.key("Escape")  # the panel stays open after a choice
        # The open panel covers the Speaker button; it re-renders shortly after Escape.
        if not await self._becomes(lambda: self._speaker_is(name)):
            raise BrowserError(f"choosing voice {name!r} did not take effect",
                               hint="re-run; if it persists, the AI Studio UI changed")
        return name

    async def set_style(self, style: str) -> None:
        await self._ui.click("button", STYLE_BUTTON, exact=True)
        await self._ui.fill("textbox", STYLE_BOX, style)
        await self._ui.pause(STYLE_SAVE_MS)

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
        names = await self._voice_names_loaded()
        groups = len(await self._ui.labels("button", VIEW_ALL))
        for index in range(groups):
            await self._ui.click("button", VIEW_ALL, nth=index)
            names += await self._voice_names_loaded()
            await self._ui.click("button", BACK)
            if not await self._loaded("button", VIEW_ALL):
                raise BrowserError("the voice panel did not return to its group list",
                                   hint="re-run; if it persists, the AI Studio UI changed")
        await self._ui.key("Escape")
        return sorted(set(names))

    async def _loaded(self, role: str, name: Name) -> list[str]:
        """Poll until controls matching ``name`` render (the panel loads asynchronously)."""
        for _ in range(LOAD_POLLS):
            labels = await self._ui.labels(role, name)
            if labels:
                return labels
            await self._ui.pause(250)
        return []

    async def _voice_names_loaded(self) -> list[str]:
        labels = await self._loaded("button", FAVORITE)
        if not labels:
            raise BrowserError("the voice panel showed no voices",
                               hint="re-run; if it persists, the AI Studio UI changed")
        return _names(labels)

    async def _eventually_visible(self, role: str, name: Name, *, exact: bool = False) -> bool:
        for _ in range(POLLS):
            await self._ui.pause(250)
            if await self._ui.visible(role, name, exact=exact):
                return True
        return False

    async def _becomes(self, check: Callable[[], Awaitable[bool]]) -> bool:
        for _ in range(LOAD_POLLS):
            if await check():
                return True
            await self._ui.pause(250)
        return False

    async def _model_is(self, model_id: str) -> bool:
        current = await self._ui.labels("button", MODEL_BUTTON)
        return bool(current) and model_id in current[0].split()

    async def _speaker_is(self, name: str) -> bool:
        labels = await self._ui.labels("button", SPEAKER)
        return bool(labels) and labels[0].split(" - ", 1)[-1].split()[0] == name



def _names(labels: list[str]) -> list[str]:
    return [m.group(1) for m in map(FAVORITE.match, labels) if m]
