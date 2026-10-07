"""Decode image and music ``GenerateContent`` responses (docs/wire/findings.md, 2026-10-07).

The response streams chunks ``resp[0][i]``. A media part is ``[mime, base64]`` at
``part[2]``, e.g. ``["image/jpeg", …]`` or ``["audio/mpeg", …]``; a text part keeps its text
at ``part[1]`` (Lyria sends its lyrics, or "<instrumental>", this way). The last chunk carries
the finish reason. Image parts may also hold a large opaque signature, which is ignored.
"""

from __future__ import annotations

import base64
from typing import Any

from aistudio._rpc.rows import as_int
from aistudio._rpc.wire import opt_index
from aistudio._types.media import GeneratedMedia, MediaPart
from aistudio.exceptions import SchemaDriftError, ServerError

METHOD = "GenerateContent"
_FINISHED = 1
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
    if not found:
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
    finish = finish_reason(response)
    if finish is None:
        raise SchemaDriftError(METHOD, (0, -1, 0, 0, 1), "a finish reason")
    if finish != _FINISHED:
        raise ServerError(f"generation stopped early (finish reason {finish})",
                          hint="try a different prompt: safety filters can stop a generation")
    return GeneratedMedia(parts=tuple(parts), text="".join(_texts(response)))


def _collect(node: Any, prefix: str, out: list[tuple[str, str]]) -> None:
    if not isinstance(node, list):
        return
    if (len(node) >= 2 and isinstance(node[0], str) and node[0].startswith(prefix)
            and isinstance(node[1], str)):
        out.append((node[0], node[1]))
        return
    for child in node:
        _collect(child, prefix, out)


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
