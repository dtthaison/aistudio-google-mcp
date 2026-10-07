"""Media generation use cases. Text-to-speech is page-driven (spec §5.4, §6.4)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.models import load_catalog, normalize_model_id
from aistudio._browser.speech_page import SpeechPage
from aistudio._browser.ui import open_browser
from aistudio._media.wav import pcm_to_wav
from aistudio._rpc import wire
from aistudio._rpc.rows.speech import decode_speech
from aistudio._transport.http import error_for_response
from aistudio.client import AIStudioClient
from aistudio.exceptions import (
    BotGuardError,
    BrowserError,
    NotFoundError,
    SchemaDriftError,
    ValidationError,
)
from aistudio.paths import Profile

DEFAULT_SPEECH_MODEL = "gemini-3.8-flash-tts"
_AUDIO_HINT = 'see "aistudio models list --category audio"'
# Catalog commands this CLI actually offers; hints never name anything else.
_OFFERED_COMMANDS = {"generate speech"}


def default_output(now: datetime) -> Path:
    return Path(f"aistudio-speech-{now:%Y%m%d-%H%M%S}.wav")


async def check_speech_model(client: AIStudioClient, profile: Profile, model: str) -> str:
    """The catalog must list the model as text-to-speech (spec §6.2 category validation)."""
    model_id = normalize_model_id(model)
    found = {m.id: m for m in await load_catalog(client, profile)}.get(model_id)
    if found is None:
        raise NotFoundError(f"no model {model_id!r} in the catalog", hint=_AUDIO_HINT)
    if found.command != "generate speech":
        usable = found.command in _OFFERED_COMMANDS
        hint = f'use "aistudio {found.command}"' if usable else _AUDIO_HINT
        raise ValidationError(f"{model_id} is not a text-to-speech model", hint=hint)
    return model_id


async def generate_speech(
    profile: Profile,
    text: str,
    *,
    model: str = DEFAULT_SPEECH_MODEL,
    voice: str | None = None,
    style: str | None = None,
    out: Path | None = None,
    overwrite: bool = False,
    visible: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, Any]:
    if not text.strip():
        raise ValidationError("nothing to speak: the text is empty")
    path = out or default_output(now())
    if path.exists() and not overwrite:
        raise ValidationError(f"{path} already exists", hint="pass --force or choose another -o")
    folder = path.parent if str(path.parent) else Path(".")
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        raise ValidationError(f"{folder} does not exist or is not writable",
                              hint="create the folder first; nothing was generated")
    async with client_factory(profile) as client:
        model_id = await check_speech_model(client, profile, model)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = SpeechPage(ui)
        await page.open()
        await page.select_model(model_id)
        chosen_voice = await page.select_voice(voice) if voice else None
        if style:
            await page.set_style(style)
        await page.set_text(text)
        captured = await page.run()
    if captured.status >= 400:
        err = error_for_response(captured.status, captured.text)
        if captured.status == 403:
            raise BotGuardError(f"Google refused the speech request: {err.message}",
                                hint="re-run with --visible; a paid model may also need a "
                                     "paid tier")
        raise err
    sent = _sent(captured.request_body)
    _confirm(sent, model_id=model_id, text=text, voice=chosen_voice)
    sent_style = wire.opt_index(sent, 1, 0, 0, 0, 40, 1) or None
    warnings: list[str] = []
    if style:
        # Where the style travels may differ by model; look for it anywhere in the request.
        if _contains(sent, style):
            sent_style = style
        else:
            warnings.append(f"AI Studio did not include the style {style!r} in the request")
    try:
        audio = decode_speech(wire.decode(captured.text))
    except ValueError as exc:  # truncated or non-JSON body
        raise SchemaDriftError("GenerateContent", (), "a JSON speech response") from exc
    _write(path, pcm_to_wav(audio.pcm, rate=audio.rate, channels=audio.channels),
           overwrite=overwrite)
    return {
        "files": [{"path": str(path), "mime_type": "audio/wav",
                   "seconds": round(audio.seconds, 2)}],
        "model": model_id,
        "voice": wire.opt_index(sent, 3, 15, 0, 0, 0) or chosen_voice,
        "style": sent_style,
        **({"warnings": warnings} if warnings else {}),
    }


def _sent(request_body: str) -> Any:
    try:
        return wire.decode(request_body) if request_body else None
    except ValueError:
        return None


def _contains(node: Any, text: str) -> bool:
    if isinstance(node, str):
        return node == text
    if isinstance(node, list):
        return any(_contains(child, text) for child in node)
    return False


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "…"


def _confirm(sent: Any, *, model_id: str, text: str, voice: str | None) -> None:
    """The audio must be for what we asked: model, text and voice (a style miss only warns)."""
    expected = {"model": f"models/{model_id}", "text": text, "voice": voice}
    actual = {
        "model": wire.opt_index(sent, 0),
        "text": wire.opt_index(sent, 1, 0, 0, 0, 1),
        "voice": wire.opt_index(sent, 3, 15, 0, 0, 0),
    }
    wrong = [f"{k}: expected {_short(v)}, sent {_short(actual[k])}"
             for k, v in expected.items() if v is not None and actual[k] != v]
    if wrong:
        raise BrowserError(f"the request AI Studio sent did not match ({'; '.join(wrong)})",
                           hint="re-run; nothing was written")


def _write(path: Path, data: bytes, *, overwrite: bool) -> None:
    """Write without ever clobbering a file that appeared meanwhile, unless --force."""
    folder = path.parent if str(path.parent) else Path(".")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if overwrite:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # atomic and fails if the file exists
    except FileExistsError as exc:
        raise ValidationError(f"{path} appeared while generating; not overwritten",
                              hint="pass --force or choose another -o") from exc
    finally:
        Path(tmp).unlink(missing_ok=True)


async def list_voices(
    profile: Profile, *, search: str | None = None, browser: Callable[..., Any] = open_browser
) -> dict[str, Any]:
    """Read the speech page's voice panel; reading works headless (no generation)."""
    async with browser(profile, window="headless") as ui:
        page = SpeechPage(ui)
        await page.open()
        voices = await page.list_voices()
    if search:
        needle = search.lower()
        voices = [v for v in voices if needle in v.lower()]
    return {"voices": voices, "count": len(voices)}
