"""Prompt library types (spec §6.3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# promptType values observed in M1 -> CLI names.
PROMPT_TYPES = {"CHUNKED_PROMPT": "chat", "VEO_PROMPT": "video"}


@dataclass(frozen=True)
class PromptSummary:
    id: str  # without the "prompts/" prefix
    title: str | None
    type: str  # "chat", "video", or the raw promptType lower-cased, or "unknown"
    description: str | None
    modified: str | None  # ISO-8601 UTC, e.g. "2026-10-06T05:10:02Z"
    has_images: bool | None
    parent_app_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
