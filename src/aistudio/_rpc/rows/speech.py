"""Decode text-to-speech ``GenerateContent`` responses (fixtures GenerateContent/1, /3).

The response streams chunks ``resp[0][i]``; each carries one audio part at
``chunk[0][0][0][0][2] = [mime, base64 PCM]`` with mime ``audio/l16; rate=24000; channels=1``.
The decoder collects every ``[mime, data]`` pair whose mime starts with ``audio/`` in document
order, so it does not depend on the exact nesting depth.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from aistudio._types.media import SpeechAudio
from aistudio.exceptions import SchemaDriftError

METHOD = "GenerateContent"
_PARAM = re.compile(r"(\w+)=(\d+)")


def decode_speech(response: Any) -> SpeechAudio:
    parts: list[tuple[str, str]] = []
    _collect(response, parts)
    if not parts:
        raise SchemaDriftError(METHOD, (), "audio parts")
    mime = parts[0][0]
    if not mime.startswith("audio/l16"):
        raise SchemaDriftError(METHOD, (), "audio/l16 PCM")
    try:
        pcm = b"".join(base64.b64decode(data, validate=True) for _, data in parts)
    except ValueError as exc:
        raise SchemaDriftError(METHOD, (), "base64 audio data") from exc
    params = dict(_PARAM.findall(mime))
    return SpeechAudio(
        mime_type=mime,
        rate=int(params.get("rate", 24000)),
        channels=int(params.get("channels", 1)),
        pcm=pcm,
    )


def _collect(node: Any, out: list[tuple[str, str]]) -> None:
    if not isinstance(node, list):
        return
    if (len(node) >= 2 and isinstance(node[0], str) and node[0].startswith("audio/")
            and isinstance(node[1], str)):
        out.append((node[0], node[1]))
        return
    for child in node:
        _collect(child, out)
