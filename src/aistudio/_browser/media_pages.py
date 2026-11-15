"""Drive AI Studio's image and music pages (spec §5.4 page driving, §6.4 media).

Mapped by the free probes in docs/wire/findings.md ("Image and music generation"). A Gemini
image model opens the chat page with an image prompt box, and its Run button runs it. Input
images (edits) and audio (transcription) go through the page's file input; each shows a
"Remove media" chip (findings, "AI Studio transcription and image editing"). A Lyria model's
chat URL redirects to the music page. Its prompt sits behind a "Describe the music…" button,
and it runs from "Generate" (⌘↵ does nothing there). Both send ``GenerateContent``.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from aistudio._browser.ui import CapturedResponse, ResponseLog, Ui
from aistudio.exceptions import BrowserError, ValidationError

CHAT_URL = "https://aistudio.google.com/prompts/new_chat?model={model}"
GENERATE_METHOD = "GenerateContent"
IMAGE_PROMPT = "Enter a prompt"
MUSIC_EDITOR = re.compile(r"^Describe the music")
MUSIC_PROMPT = "Prompt input"
GENERATE = "Generate"
FILE_INPUT = "input[type=file]"
REMOVE_MEDIA = "Remove media"  # one chip per attached file
# Live 2026-11-15: setting a file opens "Start creating with media in Google AI Studio", which
# asks the person to confirm their rights to the upload (Cancel / Acknowledge). Only
# --i-have-rights answers it. The button is named by its aria-label; its text is "Acknowledge".
RIGHTS_AGREE = re.compile(r"^(Agree to the copyright acknowledgement|Acknowledge)$")
RIGHTS_HINT = ("pass --i-have-rights if you have the necessary rights to the file, or answer "
               "AI Studio once yourself with --visible; nothing was generated")
TRANSCRIBE = "Transcribe"
# Sent with these types (live: as video/* the page took neither; as audio both transcribed)
AUDIO_TYPES = {".mp4": "audio/mp4", ".webm": "audio/webm"}
# Relabelled files go as one in-memory upload, and Playwright refuses buffers over 50 MB
RELABEL_MAX_BYTES = 50 * 1024 * 1024
VOCABULARY = "Custom vocabulary"
SWITCHES = {"timestamps": "Word timestamps", "speakers": "Speaker labels",
            "smart": "Smart transcription"}
ATTACH_TIMEOUT_S = 60.0
ACK_RESEND_S = 10.0  # no chip this long after acknowledging: set the files again, once
RUN = re.compile(r"^Run\b")  # the image page's Run ("Run keyboard_command_key …")
UPLOAD_S_PER_MB = 5.0  # the action button stays disabled until the Drive upload ends (live)


def _url(model_id: str) -> str:
    return CHAT_URL.format(model=quote(model_id, safe="-._"))


async def _await_generation(log: ResponseLog, kind: str, timeout_s: float) -> CapturedResponse:
    response = await log.next(GENERATE_METHOD, timeout_s=timeout_s)
    if response is None:
        raise BrowserError(f"no {kind} was generated within {timeout_s:.0f}s",
                           hint="it may still finish and be charged: check AI Studio before "
                                "re-running (add --visible to watch it)")
    return response


async def _attach(ui: Ui, paths: list[str], now: Callable[[], float],
                  ready: tuple[str, Any], types: dict[str, str] | None = None, *,
                  rights: bool = False) -> None:
    """Give the page's file input these files, wait for each chip, then for the page's action
    button: the chip shows at once, but the button stays disabled until the Drive upload ends
    (live), and pressing earlier would send the request without the files.

    AI Studio's rights dialog is acknowledged only when the person passed --i-have-rights
    (``rights``); without it the command stops at once instead of waiting out the chip."""
    await ui.set_files(FILE_INPUT, paths, types)
    deadline = now() + ATTACH_TIMEOUT_S
    acknowledged_at: float | None = None
    resent = False
    while (chips := len(await ui.labels("button", REMOVE_MEDIA))) < len(paths):
        if await ui.visible("button", RIGHTS_AGREE):
            if not rights:
                raise ValidationError("AI Studio asks you to confirm you have the rights to "
                                      "the files you upload", hint=RIGHTS_HINT)
            await ui.click("button", RIGHTS_AGREE)
            acknowledged_at = now()
            await ui.pause(500)
            continue
        # The dialog may have dropped the file (not yet seen live): set it once more.
        if (acknowledged_at is not None and not resent and chips == 0
                and now() - acknowledged_at >= ACK_RESEND_S):
            await ui.set_files(FILE_INPUT, paths, types)
            resent = True
        if now() >= deadline:
            raise BrowserError(f"AI Studio did not take {len(paths)} file(s); nothing was "
                               "generated",
                               hint="check the files open on this computer; re-run with --visible")
        await ui.pause(1000)
    megabytes = sum(os.path.getsize(p) for p in paths if os.path.exists(p)) / 1e6
    await ui.wait_enabled(*ready, timeout_s=min(900.0, 60.0 + UPLOAD_S_PER_MB * megabytes))


class ImagePage:
    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ui = ui
        self._now = now

    async def open(self, model_id: str) -> None:
        await self._ui.goto(_url(model_id))

    async def attach(self, paths: list[str], *, rights: bool = False) -> None:
        await _attach(self._ui, paths, self._now, ("button", RUN), rights=rights)

    async def set_prompt(self, text: str) -> None:
        await self._ui.fill("textbox", IMAGE_PROMPT, text)

    async def run(self, *, timeout_s: float = 180.0) -> CapturedResponse:
        async with self._ui.responses() as log:
            # Not the shortcut: it is an account setting ("Run ⌘↵" or "Run ↵"), and on an
            # Enter account ⌘↵ sends nothing (live). Keyboard activation also beats overlays.
            await self._ui.activate("button", RUN)
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

    async def attach(self, paths: list[str], *, rights: bool = False) -> None:
        await _attach(self._ui, paths, self._now, ("button", TRANSCRIBE), AUDIO_TYPES,
                      rights=rights)

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
