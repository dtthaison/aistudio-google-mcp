"""Model catalog types (spec §6.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# The picker's filter chips, in display order ("All" is no filter).
CATEGORY_ORDER = (
    "featured", "gemini", "gemma", "images", "video", "audio", "music", "live", "agents",
)


@dataclass(frozen=True)
class PricingRow:
    direction: str  # "input" | "output"
    label: str | None  # the unit is part of the label, e.g. "(*per second of output)"
    price_usd: float | None
    up_to_tokens: int | None = None  # tiered prices: this row applies up to N prompt tokens


@dataclass(frozen=True)
class Model:
    id: str  # without the "models/" prefix
    display_name: str | None
    description: str | None
    categories: tuple[str, ...]
    command: str | None
    version: str | None = None
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    methods: tuple[str, ...] = ()
    pricing: tuple[PricingRow, ...] = ()
    pricing_summary: str | None = None
    pricing_url: str | None = None
    docs_url: str | None = None
    release_date: str | None = None  # YYYY-MM-DD in the local timezone, as the picker shows
    knowledge_cutoff: str | None = None  # YYYY-MM

    @property
    def unsupported_category(self) -> bool:
        return self.command is None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["categories"] = list(self.categories)
        data["methods"] = list(self.methods)
        data["pricing"] = [asdict(p) for p in self.pricing]
        data["unsupported_category"] = self.unsupported_category
        return data


def command_for(model_id: str, categories: tuple[str, ...]) -> str | None:
    """The CLI command that runs a model of these categories (first match wins)."""
    cats = set(categories)
    if "agents" in cats:
        return "agent run"
    if "live" in cats:
        return "live"
    if "video" in cats:
        return "generate video"
    if "music" in cats:
        return "generate music"
    if "images" in cats:
        return "generate image"
    if "audio" in cats:
        return "transcribe" if "transcribe" in model_id else "generate speech"
    if cats & {"gemini", "gemma"}:
        return "ask"
    return None
