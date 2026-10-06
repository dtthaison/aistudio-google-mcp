"""Decode the starred-model list (field [6]) of a user preferences message."""

from __future__ import annotations

from typing import Any

from aistudio._rpc.params.preferences import Starred
from aistudio._rpc.rows import as_str
from aistudio._rpc.wire import opt_index


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def decode_starred(response: Any) -> list[Starred]:
    entries = opt_index(response, 6)
    starred: list[Starred] = []
    for entry in entries if isinstance(entries, list) else []:
        name = as_str(opt_index(entry, 0))
        if name:
            stamp = opt_index(entry, 1)
            starred.append((name.removeprefix("models/"),
                            (_int(opt_index(stamp, 0)), _int(opt_index(stamp, 1)))))
    return starred
