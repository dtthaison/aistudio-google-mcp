"""Build (apps) types (spec §6.7)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class App:
    id: str
    title: str | None
    description: str | None
    prompt: str | None  # the idea the app was built from
    created: str | None  # ISO-8601 UTC
    updated: str | None
    storage: str  # "applet" (Build app store) or "drive" (older apps saved in Drive)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AppRun:
    id: str
    prompt: str | None
    started: str | None
    updated: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecentItem:
    kind: str  # "app" | "gallery" | "prompt" | "unknown"
    id: str | None
    title: str | None
    description: str | None
    opened: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GalleryApp:
    slug: str
    title: str | None
    description: str | None
    group: str | None  # "showcase" | "template"
    apis: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    video_url: str | None = None
    image_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["apis"] = list(self.apis)
        data["tags"] = list(self.tags)
        return data
