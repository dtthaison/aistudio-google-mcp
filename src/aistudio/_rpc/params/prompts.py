"""``ListPrompts`` request builder (fixtures ListPrompts/1-4)."""

from __future__ import annotations

from typing import Any


def list_prompts(page_size: int = 100) -> list[Any]:
    """``[page_size]``. No page token was observed; M1 libraries fit in one page."""
    return [page_size]
