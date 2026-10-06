"""Positional response decoders. Shared coercions for optional fields."""

from __future__ import annotations

from typing import Any


def as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
