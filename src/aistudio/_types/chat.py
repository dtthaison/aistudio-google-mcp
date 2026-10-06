"""Chat-area result types (spec §6.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenCount:
    model: str
    total_tokens: int
    by_modality: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "total_tokens": self.total_tokens,
                "by_modality": dict(self.by_modality)}
