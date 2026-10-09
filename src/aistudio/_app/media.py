"""Media generation use cases: speech, images and music are page-driven (spec §5.4, §6.4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.models import catalog_with, normalize_model_id
from aistudio._browser.media_pages import ImagePage, MusicPage, TranscribePage
from aistudio._browser.speech_page import SpeechPage
from aistudio._browser.ui import open_browser
from aistudio._media.save import check_folder as _check_folder
from aistudio._media.save import free_name as _free_name
from aistudio._media.save import write_file as _write
from aistudio._media.wav import pcm_to_wav
from aistudio._rpc import wire
from aistudio._rpc.rows.media import Transcript, decode_media, decode_transcript, extension_for
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
    ServerError,
    ValidationError,
)
from aistudio.paths import Profile

DEFAULT_SPEECH_MODEL = "gemini-3.8-flash-tts"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"  # cheapest image model, verified live
DEFAULT_MUSIC_MODEL = "lyria-3-clip-preview"  # cheapest Lyria model, verified live
DEFAULT_TRANSCRIBE_MODEL = "gemini-3.5-transcribe"  # AI Studio's speech-to-text model
# Inputs go through the page's file input (findings): what it accepts, and a size cap.
IMAGE_INPUTS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".gif"}
AUDIO_INPUTS = {".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".mpeg", ".mpg", ".oga", ".ogg",
                ".opus", ".wav"}
MAX_INPUT_BYTES = 100 * 1024 * 1024
_AUDIO_HINT = 'see "aistudio models list --category audio"'
_IMAGE_HINT = 'see "aistudio models list --category images"'
_MUSIC_HINT = 'see "aistudio models list --category music"'
_TIER_HINT = "this model may need a paid tier or may be unavailable to your account"
# Catalog commands this CLI actually offers; hints never name anything else.
_OFFERED_COMMANDS = {"generate speech", "generate image", "generate music", "transcribe"}


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


def _inputs(values: list[Path | str], suffixes: set[str], what: str) -> list[Path]:
    """Local files to upload, checked before Chrome opens."""
    out = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValidationError(f"{path} is not a file")
        if path.suffix.lower() not in suffixes:
            raise ValidationError(f"{path} is not {what} ({', '.join(sorted(suffixes))})")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValidationError(f"{path} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
        out.append(path)
    return out


async def transcribe(
    profile: Profile,
    audio: Path | str,
    *,
    model: str = DEFAULT_TRANSCRIBE_MODEL,
    timestamps: bool | None = None,
    diarize: bool | None = None,
    smart: bool | None = None,
    vocabulary: str = "",
    out: Path | None = None,
    overwrite: bool = False,
    visible: bool = False,
    timeout_s: float = 600.0,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    on_start: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Speech to text with AI Studio's transcribe model (page-driven; uses the user's quota)."""
    (path,) = _inputs([audio], AUDIO_INPUTS, "an audio file")
    vocabulary = " ".join(vocabulary.split())
    # AI Studio refuses these together (live): "custom_vocabulary is incompatible with word
    # timestamps" and "… with diarization". The page turns both on by default.
    if vocabulary and (timestamps or diarize):
        raise ValidationError("--vocabulary cannot be combined with --timestamps or --diarize",
                              hint="drop one of them; nothing was sent")
    if vocabulary:
        timestamps = diarize = False
    if out is not None:  # nothing is written without -o
        if out.exists() and not overwrite:
            raise ValidationError(f"{out} already exists", hint="pass --force or choose another -o")
        _check_folder(out.parent if str(out.parent) else Path("."))
    async with client_factory(profile) as client:
        model_id = await check_model(client, profile, model, command="transcribe",
                                     what="a transcription model", hint=_AUDIO_HINT)
    if on_start:
        on_start()
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = TranscribePage(ui)
        await page.open(model_id)
        await page.attach([str(path)])
        await page.options(timestamps=timestamps, speakers=diarize, smart=smart)
        await page.vocabulary(vocabulary)
        captured = await page.run(timeout_s=timeout_s)
    if captured.status >= 400:
        error = _refused(captured.status, captured.text, "transcription")
        if isinstance(error, ValidationError):  # an option AI Studio refused, not a tier issue
            error.hint = "change the options; nothing was transcribed"
        raise error
    try:
        transcript = decode_transcript(wire.decode(captured.text))
    except ValueError as exc:  # truncated or non-JSON body
        raise SchemaDriftError("GenerateContent", (), "a JSON transcription") from exc
    warnings = _transcript_problems(transcript)
    wrong = _mismatches(_sent(captured.request_body), model_id=model_id, text=None, voice=None,
                        files=1)
    if wrong:  # the transcript is kept; the person should know the request looked different
        warnings.append(f"the request AI Studio sent did not match ({'; '.join(wrong)}); check "
                        "the transcript")
    segments = [_segment(s) for s in transcript.segments]
    result: dict[str, Any] = {
        "text": transcript.text, "model": model_id, "audio": str(path),
        **({"segments": segments} if any(s["speaker"] or s["start"] is not None
                                         for s in segments) else {})}
    if out is not None:
        saved = _write(out, _transcript_file(transcript).encode("utf-8"), overwrite=overwrite)
        result["file"] = {"path": str(saved), "bytes": saved.stat().st_size}
        if saved != out:
            warnings.append(f"{out} appeared while transcribing; saved to {saved} instead")
    return {**result, **({"warnings": warnings} if warnings else {})}


_MAX_TOKENS = 2  # finish reason: the reply reached the model's output limit


def _transcript_problems(transcript: Transcript) -> list[str]:
    """Errors worded for transcription (no prompt to change); a cut-off reply is kept (paid)."""
    if transcript.finish is not None and transcript.finish not in (1, _MAX_TOKENS):
        raise ServerError(f"the transcription stopped (reason {transcript.finish})",
                          hint="check the audio; nothing usable came back")
    if not transcript.text:
        raise ServerError("no speech was transcribed",
                          hint="check that the audio has speech in it")
    if transcript.finish == _MAX_TOKENS:
        return ["the transcript was cut off at the model's output limit; split the audio into "
                "shorter files"]
    return []


def _segment(segment: Any) -> dict[str, Any]:
    return {"speaker": segment.speaker, "start": segment.start, "end": segment.end,
            "text": segment.text, **({"words": [list(w) for w in segment.words]}
                                     if segment.words else {})}


def _transcript_file(transcript: Transcript) -> str:
    """Plain text, or one line per segment with times and speaker when the reply has them."""
    if not any(s.speaker or s.start is not None for s in transcript.segments):
        return transcript.text
    lines = []
    for s in transcript.segments:
        when = (f"[{_clock(s.start)} - {_clock(s.end)}] " if s.start is not None else "")
        who = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"{when}{who}{s.text}")
    return "\n".join(lines) + "\n"


def _clock(seconds: float | None) -> str:
    seconds = seconds or 0.0
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


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
    images: list[Path | str] | None = None,
) -> dict[str, Any]:
    prompt = prompt.strip()  # e.g. a --prompt-file's trailing newline
    if not prompt:
        raise ValidationError(f"nothing to generate: the {name} prompt is empty")
    inputs = _inputs(images or [], IMAGE_INPUTS, "an image")
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
        if inputs:  # edits: every input is attached (uploaded to Drive) before the prompt runs
            await page.attach([str(path) for path in inputs])
        await page.set_prompt(prompt)
        captured = await page.run()
    if captured.status >= 400:
        raise _refused(captured.status, captured.text, name)
    # The run is paid for by now: a mismatch is reported, never a reason to drop the result.
    wrong = _mismatches(_sent(captured.request_body), model_id=model_id, text=prompt, voice=None,
                        files=len(inputs) if inputs else None)
    warnings = ([f"the request AI Studio sent did not match ({'; '.join(wrong)}); the result was "
                 "kept, check it"] if wrong else [])
    try:
        body = wire.decode(captured.text)
    except ValueError as exc:  # truncated or non-JSON body
        raise SchemaDriftError("GenerateContent", (), f"a JSON {name} response") from exc
    media = decode_media(body, part_kind)
    files: list[dict[str, Any]] = []
    first: Path | None = None
    for index, part in enumerate(media.parts):
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
            saved = _write(_free_name(first.with_suffix(ext), start=index + 1), part.data,
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


def _mismatches(sent: Any, *, model_id: str, text: str, voice: str | None,
                files: int | None = None) -> list[str]:
    parts = wire.opt_index(sent, 1, 0, 0)
    parts = parts if isinstance(parts, list) else []
    expected = {"model": f"models/{model_id}", "text": text, "voice": voice, "files": files}
    actual = {
        "model": wire.opt_index(sent, 0),
        # the text part; input files come before it
        "text": next((p[1] for p in parts if isinstance(wire.opt_index(p, 1), str)), None),
        "voice": wire.opt_index(sent, 3, 15, 0, 0, 0),
        "files": sum(1 for p in parts if _is_file(p)),
    }
    return [f"{k}: expected {_short(v)}, sent {_short(actual[k])}"
            for k, v in expected.items() if v is not None and actual[k] != v]


def _is_file(part: Any) -> bool:
    """A Drive file id sits in slot 5; a small file goes inline as [mime, base64] in slot 2."""
    inline = wire.opt_index(part, 2)
    return isinstance(wire.opt_index(part, 5), list) or (
        isinstance(inline, list) and isinstance(wire.opt_index(inline, 0), str))


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
