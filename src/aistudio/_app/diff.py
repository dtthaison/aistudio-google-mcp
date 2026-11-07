"""Compare two sets of app files (``app diff``). Pure: bytes in, unified diffs out."""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str  # "added" | "removed" | "changed"
    binary: bool  # not UTF-8 text: counted, but no line diff
    added: int  # lines
    removed: int
    diff: str  # unified diff; "" for binary files and empty files

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _lines(text: str) -> list[str]:
    """Lines with their endings; a last line without one is marked, as ``diff`` does."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def _change(path: str, old: bytes | None, new: bytes | None) -> FileChange | None:
    status = "added" if old is None else "removed" if new is None else "changed"
    if old == new:
        return None
    old_text = "" if old is None else _text(old)
    new_text = "" if new is None else _text(new)
    if old_text is None or new_text is None:
        return FileChange(path, status, True, 0, 0, "")
    lines = list(difflib.unified_diff(
        _lines(old_text), _lines(new_text),
        fromfile="/dev/null" if old is None else f"a/{path}",
        tofile="/dev/null" if new is None else f"b/{path}"))
    added = sum(1 for line in lines[2:] if line.startswith("+"))
    removed = sum(1 for line in lines[2:] if line.startswith("-"))
    return FileChange(path, status, False, added, removed, "".join(
        line if line.endswith("\n") else line + "\n" for line in lines))


def diff_files(old: Mapping[str, bytes], new: Mapping[str, bytes]) -> list[FileChange]:
    """What changed from ``old`` to ``new`` (both ``path -> content``), by path; unchanged files
    are left out."""
    paths = sorted(old.keys() | new.keys())
    changes = (_change(path, old.get(path), new.get(path)) for path in paths)
    return [change for change in changes if change is not None]


def summarize(changes: list[FileChange]) -> dict[str, int]:
    return {
        "added": sum(c.status == "added" for c in changes),
        "removed": sum(c.status == "removed" for c in changes),
        "changed": sum(c.status == "changed" for c in changes),
        "lines_added": sum(c.added for c in changes),
        "lines_removed": sum(c.removed for c in changes),
    }
