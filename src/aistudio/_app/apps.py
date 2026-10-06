"""Build (apps) read use cases (spec §6.7)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from aistudio.client import AIStudioClient
from aistudio.exceptions import NotFoundError, ValidationError

T = TypeVar("T")


def pick(items: Sequence[T], key: Callable[[T], str], wanted: str, *, noun: str,
         hint: str) -> T:
    """Exact match first, then a unique prefix (spec §2.1)."""
    wanted = wanted.strip()
    for item in items:
        if key(item) == wanted:
            return item
    matches = [item for item in items if key(item).startswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(sorted(key(m) for m in matches)[:10])
        raise ValidationError(f"{wanted!r} matches several {noun}s: {names}",
                              hint="type more of the id")
    raise NotFoundError(f"no {noun} {wanted!r}", hint=hint)


async def list_apps(client: AIStudioClient, *, limit: int = 1000) -> dict[str, Any]:
    apps = await client.apps.list(limit)
    return {"apps": [a.to_dict() for a in apps], "count": len(apps)}


async def get_app(client: AIStudioClient, app_id: str) -> dict[str, Any]:
    app = pick(await client.apps.list(), lambda a: a.id, app_id, noun="app",
               hint='see "aistudio app list"')
    runs = await client.apps.runs(app.id) if app.storage == "applet" else []
    return {**app.to_dict(), "runs": [r.to_dict() for r in runs]}


async def recent_apps(client: AIStudioClient) -> dict[str, Any]:
    items = await client.apps.recent()
    return {"recent": [i.to_dict() for i in items], "count": len(items)}


async def gallery_list(
    client: AIStudioClient,
    *,
    group: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    apps = await client.gallery.list()
    if group:
        apps = [a for a in apps if a.group == group]
    if tag:
        apps = [a for a in apps if tag in a.tags]
    if search:
        needle = search.lower()
        apps = [a for a in apps if needle in a.slug.lower()
                or needle in (a.title or "").lower() or needle in (a.description or "").lower()]
    return {"apps": [a.to_dict() for a in apps], "count": len(apps)}


async def gallery_get(client: AIStudioClient, slug: str) -> dict[str, Any]:
    app = pick(await client.gallery.list(), lambda a: a.slug, slug, noun="gallery app",
               hint='see "aistudio gallery list"')
    return app.to_dict()
