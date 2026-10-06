"""Positional response decoders. Shared coercions for optional fields."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_strs(value: Any) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def as_timestamp(value: Any) -> str | None:
    """``[seconds, nanos]`` (seconds as str or int) as ISO-8601 UTC, or None."""
    seconds = value[0] if isinstance(value, list) and value else None
    try:
        moment = datetime.fromtimestamp(int(seconds), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
