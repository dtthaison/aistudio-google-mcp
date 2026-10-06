"""Build (apps) request builders (fixtures ListApplets/2-4, ListRecentApplets,
ListBundledApplets, ListAppletTrajectories)."""

from __future__ import annotations

from typing import Any


def list_apps(page_size: int = 1000) -> list[Any]:
    return [[page_size], [[]], 0]


def list_recent_apps() -> list[Any]:
    return []


def list_gallery() -> list[Any]:
    return []


def list_app_runs(app_id: str) -> list[Any]:
    return [app_id]
