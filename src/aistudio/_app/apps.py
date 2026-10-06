"""Build (apps) read use cases (spec §6.7)."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from aistudio.client import AIStudioClient
from aistudio.exceptions import NotFoundError, ServerError, ValidationError

T = TypeVar("T")


def pick(items: Sequence[T], key: Callable[[T], str], wanted: str, *, noun: str,
         hint: str) -> T:
    """Exact match first, then a unique prefix (spec §2.1)."""
    wanted = wanted.strip()
    if not wanted:
        raise ValidationError(f"give a {noun} id or a unique prefix", hint=hint)
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


_PARALLEL_DOWNLOADS = 6


def _safe_relative(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if (not path or "\\" in path or rel.is_absolute() or any(p in ("..", "") for p in rel.parts)
            or rel == PurePosixPath(".")):
        raise ValidationError(f"unsafe file path in the app snapshot: {path!r}",
                              hint="nothing was downloaded or written")
    return rel


async def export_app(client: AIStudioClient, app_ref: str, dest: Path, *,
                     force: bool = False) -> dict[str, Any]:
    """Download a Build app's latest code into ``dest``, verified by SHA-256 (findings)."""
    app = pick(await client.apps.list(), lambda a: a.id, app_ref, noun="app",
               hint='see "aistudio app list"')
    if app.storage != "applet":
        raise ValidationError(f"{app.title or app.id!r} is stored in Google Drive (an older Build "
                              "app); aistudio can only export Build-store apps",
                              hint="open it at https://aistudio.google.com/apps")
    files = await client.apps.snapshot_files(app.id)
    if not files:
        raise NotFoundError(f"app {app.id} has no saved code yet",
                            hint=f'build it first: aistudio app prompt {app.id[:8]} "…"')
    targets = [(_safe_relative(path), digest) for path, digest in files]
    if dest.exists() and any(dest.iterdir()) and not force:
        raise ValidationError(f"{dest} is not empty",
                              hint="choose an empty folder or pass --force to overwrite files")
    limit = asyncio.Semaphore(_PARALLEL_DOWNLOADS)

    async def fetch(rel: PurePosixPath, digest: str) -> bytes:
        async with limit:
            data = await client.apps.download_file(app.id, digest)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ServerError(f"{rel}: downloaded content does not match its SHA-256",
                              retriable=True, hint="re-run the export; nothing was written")
        return data

    contents = await asyncio.gather(*(fetch(rel, digest) for rel, digest in targets))
    for (rel, _), data in zip(targets, contents, strict=True):
        target = dest.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {
        "app_id": app.id,
        "title": app.title,
        "dir": str(dest),
        "count": len(targets),
        "bytes": sum(len(d) for d in contents),
        "files": [{"path": str(rel), "bytes": len(data)}
                  for (rel, _), data in zip(targets, contents, strict=True)],
    }
