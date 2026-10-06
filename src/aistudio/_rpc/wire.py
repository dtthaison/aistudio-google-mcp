"""Encoding and decoding of MakerSuite ``application/json+protobuf`` bodies (spec §5.2).

Google's JSPB serialisation writes sparse arrays with elided slots such as
``[,[13,"msg"]]``. That is valid JavaScript but not valid JSON, so ``decode``
rewrites elisions to ``null`` (and drops a JavaScript trailing comma) before
parsing, without touching string contents.
"""

from __future__ import annotations

import json
from typing import Any

from aistudio.exceptions import SchemaDriftError

XSSI_PREFIX = ")]}'"


def encode(body: Any) -> str:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def normalize_elisions(text: str) -> str:
    out: list[str] = []
    prev = ""  # last significant character emitted outside a string
    last_comma = -1  # index in ``out`` of the most recent comma outside a string
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                prev = '"'
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch == ",":
            if prev in ("[", ","):
                out.append("null")
            last_comma = len(out)
            out.append(",")
            prev = ","
            continue
        if ch == "]" and prev == ",":
            # JavaScript ignores one trailing comma: ``[1,]`` has length 1.
            del out[last_comma]
        out.append(ch)
        if not ch.isspace():
            prev = ch
    return "".join(out)


def decode(text: str) -> Any:
    body = text.lstrip()
    if body.startswith(XSSI_PREFIX):
        body = body[len(XSSI_PREFIX):]
    body = body.strip()
    if not body:
        return None
    return json.loads(normalize_elisions(body))


def parse_error(decoded: Any) -> tuple[int, str] | None:
    """Return ``(grpc_status, message)`` from an error body, or None.

    Accepts ``[null, [code, "msg", ...]]`` and ``[code, "msg", ...]`` (spec H3).
    Only meaningful for non-2xx responses.
    """
    if not isinstance(decoded, list):
        return None
    candidates = [decoded]
    if len(decoded) >= 2 and decoded[0] is None and isinstance(decoded[1], list):
        candidates.insert(0, decoded[1])
    for c in candidates:
        if (
            len(c) >= 2
            and isinstance(c[0], int)
            and not isinstance(c[0], bool)
            and isinstance(c[1], str)
        ):
            return c[0], c[1]
    return None


def safe_index(value: Any, *path: int, method: str, expected: str) -> Any:
    """Follow ``path`` into nested lists, raising SchemaDriftError if it does not exist."""
    current = value
    for depth, index in enumerate(path):
        if not isinstance(current, list) or not -len(current) <= index < len(current):
            raise SchemaDriftError(method, tuple(path[: depth + 1]), expected)
        current = current[index]
    return current
