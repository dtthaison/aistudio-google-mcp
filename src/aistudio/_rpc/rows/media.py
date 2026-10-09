"""Decode image and music ``GenerateContent`` responses (docs/wire/findings.md, 2026-10-07).

The response streams chunks ``resp[0][i]``. A media part is ``[mime, base64]`` at
``part[2]``, e.g. ``["image/jpeg", …]`` or ``["audio/mpeg", …]``; a text part keeps its text
at ``part[1]`` (Lyria sends its lyrics, or "<instrumental>", this way). The last chunk carries
the finish reason. Image parts may also hold a large opaque signature, which is ignored.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from aistudio._rpc.rows import as_int
from aistudio._rpc.wire import opt_index
from aistudio._types.media import GeneratedMedia, MediaPart
from aistudio.exceptions import SchemaDriftError, ServerError

METHOD = "GenerateContent"
_FINISHED = 1
_NOUNS = {"image": "an image", "audio": "audio"}
_REPHRASE = {"image": 'rephrase the prompt, e.g. start with "Generate an image of …"',
             "audio": "rephrase the prompt, e.g. describe the style, mood and instruments"}
_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/ogg": ".ogg"}


def extension_for(mime: str) -> str:
    return _EXTENSIONS.get(mime.split(";", 1)[0].strip().lower(), ".bin")


def finish_reason(response: Any) -> int | None:
    """The last chunk carrying ``chunk[0][0][1]`` (1 = STOP)."""
    chunks = opt_index(response, 0)
    reason = None
    for chunk in chunks if isinstance(chunks, list) else []:
        value = as_int(opt_index(chunk, 0, 0, 1))
        if value is not None:
            reason = value
    return reason


def decode_media(response: Any, kind: str) -> GeneratedMedia:
    """Collect every ``kind/*`` part in order; audio parts of one mime are joined."""
    found: list[tuple[str, str]] = []
    _collect(response, f"{kind}/", found)
    finish = finish_reason(response)
    if not found:  # a refusal: say why rather than blame the response shape
        if finish is not None and finish != _FINISHED:
            raise _stopped(finish)
        text = " ".join("".join(_texts(response)).split())
        if text:
            raise ServerError(f"the model replied with text instead of {_NOUNS[kind]}: "
                              f"{text[:300]}", hint=_REPHRASE[kind])
        raise SchemaDriftError(METHOD, (), f"{kind} parts")
    parts: list[MediaPart] = []
    for mime, data in found:
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise SchemaDriftError(METHOD, (), f"base64 {kind} data") from exc
        if not raw:
            raise SchemaDriftError(METHOD, (), f"non-empty {kind} data")
        if kind == "audio" and parts and parts[-1].mime_type == mime:
            parts[-1] = MediaPart(mime, parts[-1].data + raw)
        else:
            parts.append(MediaPart(mime, raw))
    if finish is None:
        raise SchemaDriftError(METHOD, (0, -1, 0, 0, 1), "a finish reason")
    if finish != _FINISHED:
        raise _stopped(finish)
    return GeneratedMedia(parts=tuple(parts), text="".join(_texts(response)))


def _stopped(finish: int) -> ServerError:
    return ServerError(f"generation stopped early (finish reason {finish})",
                       hint="try a different prompt: safety filters can stop a generation")


def _collect(node: Any, prefix: str, out: list[tuple[str, str]]) -> None:
    if not isinstance(node, list):
        return
    if (len(node) >= 2 and isinstance(node[0], str) and node[0].startswith(prefix)
            and isinstance(node[1], str)):
        out.append((node[0], node[1]))
        return
    for child in node:
        _collect(child, prefix, out)


_TRANSCRIPT_SLOT = 22  # the transcribe model's part: ``[None × 22, [text, speaker?, words?]]``


@dataclass(frozen=True)
class Segment:
    text: str
    speaker: str | None  # "spk:0" when speaker labels are on
    start: float | None  # seconds, when word timestamps are on
    end: float | None
    words: tuple[tuple[str, float | None, float | None], ...] = ()


@dataclass(frozen=True)
class Transcript:
    text: str
    segments: tuple[Segment, ...]
    finish: int | None
    plain: bool = False  # text only in slot 1: a chat-style reply, not a transcript


def decode_transcript(response: Any) -> Transcript:
    """A transcription reply (live): each part's slot 22 holds ``[text, "spk:N"?, [[word,
    start, end], …]?]`` with times as ``[seconds?, nanos?]``. Never raises: the caller words the
    errors (empty, cut off, stopped)."""
    segments: list[Segment] = []
    transcribed = False
    for part in _parts(response):
        slot = opt_index(part, _TRANSCRIPT_SLOT)
        transcribed = transcribed or isinstance(slot, list)
        text = opt_index(slot, 0) if isinstance(slot, list) else opt_index(part, 1)
        if not isinstance(text, str) or not text.strip():
            continue
        speaker = opt_index(slot, 1)
        words = tuple((w[0], _seconds(opt_index(w, 1)), _seconds(opt_index(w, 2)))
                      for w in (opt_index(slot, 2) or []) if isinstance(opt_index(w, 0), str))
        segments.append(Segment(text.strip(), speaker if isinstance(speaker, str) else None,
                                words[0][1] if words else None, words[-1][2] if words else None,
                                words))
    return Transcript(" ".join(s.text for s in segments), tuple(segments),
                      finish_reason(response), plain=bool(segments) and not transcribed)


def _seconds(value: Any) -> float | None:
    """A ``[seconds, nanos]`` duration; either may be missing, seconds may be a string."""
    if not isinstance(value, list):
        return None
    seconds, nanos = opt_index(value, 0), opt_index(value, 1)
    try:
        return round(float(seconds or 0) + float(nanos or 0) / 1e9, 3)
    except (TypeError, ValueError):
        return None


def _parts(response: Any) -> list[Any]:
    out: list[Any] = []
    chunks = opt_index(response, 0)
    for chunk in chunks if isinstance(chunks, list) else []:
        candidates = opt_index(chunk, 0)
        for candidate in candidates if isinstance(candidates, list) else []:
            parts = opt_index(candidate, 0, 0)
            out.extend(parts if isinstance(parts, list) else [])
    return out


def _texts(response: Any) -> list[str]:
    """Text parts: ``chunk[0][c][0][0][p][1]`` for every candidate c and part p."""
    out: list[str] = []
    chunks = opt_index(response, 0)
    for chunk in chunks if isinstance(chunks, list) else []:
        candidates = opt_index(chunk, 0)
        for candidate in candidates if isinstance(candidates, list) else []:
            parts = opt_index(candidate, 0, 0)
            for part in parts if isinstance(parts, list) else []:
                text = opt_index(part, 1)
                if isinstance(text, str) and text:
                    out.append(text)
    return out
