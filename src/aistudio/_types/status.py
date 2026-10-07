"""Service status types (AI Studio / Gemini API incidents)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class IncidentUpdate:
    state: str  # name inferred from the code; unknown codes read "state:N"
    time: str | None  # ISO-8601 UTC
    message: str | None
    state_code: int | None = field(default=None, compare=False)  # AI Studio's raw code


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
        return self.state != "resolved"  # an unknown state counts as active (conservative)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["products"] = list(self.products)
        data["updates"] = [asdict(u) for u in self.updates]
        data["active"] = self.active
        data["state_code"] = self.updates[-1].state_code if self.updates else None
        return data
