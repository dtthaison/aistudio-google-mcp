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


@dataclass(frozen=True)
class PromptTurn:
    """One message: consecutive same-role chunks merged (attachments, text, thinking)."""

    role: str  # "user" or "model"
    text: str
    thoughts: str | None  # the model's thinking summary, when AI Studio saved one
    attachments: list[dict[str, Any]]  # {"source": "drive"|"inline", "id", "mime"}: no bytes
    at: str | None  # ISO-8601 UTC of the first chunk

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptDetail:
    id: str
    title: str | None
    description: str | None
    type: str
    model: str | None  # without the "models/" prefix
    system_instruction: str | None
    settings: dict[str, Any]
    turns: list[PromptTurn]
    modified: str | None
    parent_app_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
