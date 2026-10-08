"""Media generation use cases: speech, images and music are page-driven (spec §5.4, §6.4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.models import catalog_with, normalize_model_id
from aistudio._browser.media_pages import ImagePage, MusicPage
from aistudio._browser.speech_page import SpeechPage
from aistudio._browser.ui import open_browser
from aistudio._media.save import check_folder as _check_folder
from aistudio._media.save import free_name as _free_name
from aistudio._media.save import write_file as _write
from aistudio._media.wav import pcm_to_wav
from aistudio._rpc import wire
from aistudio._rpc.rows.media import decode_media, extension_for
from aistudio._rpc.rows.speech import decode_speech
from aistudio._transport.http import error_for_response
from aistudio.client import AIStudioClient
from aistudio.exceptions import (
    AIStudioError,
    BotGuardError,
    BrowserError,
    NotFoundError,
    QuotaExceededError,
    SchemaDriftError,
    ValidationError,
)
from aistudio.paths import Profile

DEFAULT_SPEECH_MODEL = "gemini-3.8-flash-tts"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"  # cheapest image model, verified live
DEFAULT_MUSIC_MODEL = "lyria-3-clip-preview"  # cheapest Lyria model, verified live
_AUDIO_HINT = 'see "aistudio models list --category audio"'
_IMAGE_HINT = 'see "aistudio models list --category images"'
_MUSIC_HINT = 'see "aistudio models list --category music"'
_TIER_HINT = "this model may need a paid tier or may be unavailable to your account"
# Catalog commands this CLI actually offers; hints never name anything else.
_OFFERED_COMMANDS = {"generate speech", "generate image", "generate music"}


def default_output(now: datetime) -> Path:
    return Path(f"aistudio-speech-{now:%Y%m%d-%H%M%S}.wav")


async def check_model(client: AIStudioClient, profile: Profile, model: str, *, command: str,
                      what: str, hint: str) -> str:
    """The catalog must map the model to ``command`` (spec §6.2 category validation)."""
    model_id = normalize_model_id(model)
    models = await catalog_with(client, profile, lambda ms: any(m.id == model_id for m in ms))
    found = {m.id: m for m in models}.get(model_id)
    if found is None:
        raise NotFoundError(f"no model {model_id!r} in the catalog", hint=hint)
    if found.command != command:
        usable = found.command in _OFFERED_COMMANDS
        raise ValidationError(f"{model_id} is not {what}",
                              hint=f'use "aistudio {found.command}"' if usable else hint)
    return model_id


async def check_speech_model(client: AIStudioClient, profile: Profile, model: str) -> str:
    return await check_model(client, profile, model, command="generate speech",
                             what="a text-to-speech model", hint=_AUDIO_HINT)


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
    on_start: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if not text.strip():
        raise ValidationError("nothing to speak: the text is empty")
    path = out or default_output(now())
    if path.exists() and not overwrite:
        raise ValidationError(f"{path} already exists", hint="pass --force or choose another -o")
    _check_folder(path.parent if str(path.parent) else Path("."))
    async with client_factory(profile) as client:
        model_id = await check_speech_model(client, profile, model)
    if on_start:
        on_start()
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
    saved = _write(path, pcm_to_wav(audio.pcm, rate=audio.rate, channels=audio.channels),
                   overwrite=overwrite)
    if saved != path:
        warnings.append(f"{path} appeared while generating; saved to {saved} instead")
    return {
        "files": [{"path": str(saved), "mime_type": "audio/wav",
                   "seconds": round(audio.seconds, 2)}],
        "model": model_id,
        "voice": wire.opt_index(sent, 3, 15, 0, 0, 0) or chosen_voice,
        "style": sent_style,
        **({"warnings": warnings} if warnings else {}),
    }


async def generate_image(profile: Profile, prompt: str, *, model: str = DEFAULT_IMAGE_MODEL,
                         **options: Any) -> dict[str, Any]:
    return await _generate_media(profile, prompt, model=model, page_class=ImagePage,
                                 part_kind="image", name="image", command="generate image",
                                 what="an image model", hint=_IMAGE_HINT, **options)


async def generate_music(profile: Profile, prompt: str, *, model: str = DEFAULT_MUSIC_MODEL,
                         **options: Any) -> dict[str, Any]:
    return await _generate_media(profile, prompt, model=model, page_class=MusicPage,
                                 part_kind="audio", name="music", command="generate music",
                                 what="a music model", hint=_MUSIC_HINT, **options)


async def _generate_media(
    profile: Profile,
    prompt: str,
    *,
    model: str,
    page_class: Callable[[Any], Any],
    part_kind: str,
    name: str,
    command: str,
    what: str,
    hint: str,
    out: Path | None = None,
    overwrite: bool = False,
    visible: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], datetime] = datetime.now,
    on_start: Callable[[], None] | None = None,
) -> dict[str, Any]:
    prompt = prompt.strip()  # e.g. a --prompt-file's trailing newline
    if not prompt:
        raise ValidationError(f"nothing to generate: the {name} prompt is empty")
    if out is not None and out.exists() and not overwrite:
        raise ValidationError(f"{out} already exists", hint="pass --force or choose another -o")
    _check_folder(out.parent if out is not None and str(out.parent) else Path("."))
    async with client_factory(profile) as client:
        model_id = await check_model(client, profile, model, command=command, what=what,
                                     hint=hint)
    if on_start:
        on_start()
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_class(ui)
        await page.open(model_id)
        await page.set_prompt(prompt)
        captured = await page.run()
    if captured.status >= 400:
        raise _refused(captured.status, captured.text, name)
    # The run is paid for by now: a mismatch is reported, never a reason to drop the result.
    wrong = _mismatches(_sent(captured.request_body), model_id=model_id, text=prompt, voice=None)
    warnings = ([f"the request AI Studio sent did not match ({'; '.join(wrong)}); the result was "
                 "kept, check it"] if wrong else [])
    try:
        body = wire.decode(captured.text)
    except ValueError as exc:  # truncated or non-JSON body
        raise SchemaDriftError("GenerateContent", (), f"a JSON {name} response") from exc
    media = decode_media(body, part_kind)
    files: list[dict[str, Any]] = []
    first: Path | None = None
    for part in media.parts:
        ext = extension_for(part.mime_type)
        if first is None:
            path = out or _free_name(Path(f"aistudio-{name}-{now():%Y%m%d-%H%M%S}{ext}"))
            if out is not None and _normal_suffix(out.suffix) != ext:
                warnings.append(f"{out} holds {part.mime_type} data; its extension says otherwise")
            saved = _write(path, part.data, overwrite=overwrite)
            if saved != path:
                warnings.append(f"{path} appeared while generating; saved to {saved} instead")
            first = saved
        else:  # extra images never clobber anything, so nothing fails after a paid run
            saved = _write(_free_name(first.with_suffix(ext), start=2), part.data,
                           overwrite=False)
        files.append({"path": str(saved), "mime_type": part.mime_type, "bytes": len(part.data)})
    return {
        "files": files,
        "model": model_id,
        **({"text": media.text} if media.text else {}),
        **({"warnings": warnings} if warnings else {}),
    }


def _refused(status: int, text: str, name: str) -> AIStudioError:
    err = error_for_response(status, text)
    if status == 403:
        return BotGuardError(f"Google refused the {name} request: {err.message}",
                             hint="re-run with --visible; a paid model may also need a "
                                  "paid tier")
    if err.hint is None and isinstance(err, (ValidationError, QuotaExceededError)):
        err.hint = _TIER_HINT
    return err


def _normal_suffix(suffix: str) -> str:
    lowered = suffix.lower()
    return ".jpg" if lowered == ".jpeg" else lowered


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


def _mismatches(sent: Any, *, model_id: str, text: str, voice: str | None) -> list[str]:
    expected = {"model": f"models/{model_id}", "text": text, "voice": voice}
    actual = {
        "model": wire.opt_index(sent, 0),
        "text": wire.opt_index(sent, 1, 0, 0, 0, 1),
        "voice": wire.opt_index(sent, 3, 15, 0, 0, 0),
    }
    return [f"{k}: expected {_short(v)}, sent {_short(actual[k])}"
            for k, v in expected.items() if v is not None and actual[k] != v]


def _confirm(sent: Any, *, model_id: str, text: str, voice: str | None) -> None:
    """The audio must be for what we asked: model, text and voice (a style miss only warns)."""
    wrong = _mismatches(sent, model_id=model_id, text=text, voice=voice)
    if wrong:
        raise BrowserError(f"the request AI Studio sent did not match ({'; '.join(wrong)})",
                           hint="re-run; nothing was written")


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
