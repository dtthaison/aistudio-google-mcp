"""Service status types (AI Studio / Gemini API incidents)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IncidentUpdate:
    state: str
    time: str | None  # ISO-8601 UTC
    message: str | None


@dataclass(frozen=True)
class Incident:
    id: str
    title: str | None
    products: tuple[str, ...]
    state: str  # state of the latest update
    started: str | None
    updated: str | None
    message: str | None  # latest update's message
    updates: tuple[IncidentUpdate, ...] = ()

    @property
    def active(self) -> bool:
        return self.state != "resolved"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["products"] = list(self.products)
        data["updates"] = [asdict(u) for u in self.updates]
        data["active"] = self.active
        return data
