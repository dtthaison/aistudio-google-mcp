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
    """Exact match first, then ignoring case, then a unique prefix ignoring case (spec §2.1)."""
    wanted = wanted.strip()
    if not wanted:
        raise ValidationError(f"give a {noun} id or a unique prefix", hint=hint)
    for item in items:
        if key(item) == wanted:
            return item
    folded = wanted.casefold()
    exact = [item for item in items if key(item).casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    matches = exact or [item for item in items if key(item).casefold().startswith(folded)]
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
_MAX_NAME_BYTES = 255
_NOTHING_WRITTEN = "nothing was downloaded or written"


def _safe_relative(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if (not path or any(c in path for c in "\\\x00:") or rel.is_absolute()
            or any(p in ("..", "") for p in rel.parts) or rel == PurePosixPath(".")
            or any(len(p.encode()) > _MAX_NAME_BYTES for p in rel.parts)):
        raise ValidationError(f"unsafe file path in the app snapshot: {path!r}",
                              hint=_NOTHING_WRITTEN)
    return rel


def _preflight(targets: list[tuple[PurePosixPath, str]], dest: Path) -> None:
    """Refuse anything that could write outside ``dest`` or fail half-way through writing."""
    if dest.exists() and (dest.is_symlink() or not dest.is_dir()):
        raise ValidationError(f"{dest} is not a folder", hint=_NOTHING_WRITTEN)
    by_key: dict[str, str] = {}
    folders: set[str] = set()
    for rel, _ in targets:
        key = str(rel).casefold()  # macOS and Windows file systems ignore case
        if key in by_key:
            raise ValidationError(f"the app snapshot has clashing paths {by_key[key]!r} and "
                                  f"{str(rel)!r}", hint=_NOTHING_WRITTEN)
        by_key[key] = str(rel)
        folders.update(str(parent).casefold() for parent in rel.parents if parent.parts)
    for key in by_key.keys() & folders:
        raise ValidationError(f"{by_key[key]!r} is both a file and a folder in the app snapshot",
                              hint=_NOTHING_WRITTEN)
    root = dest.resolve()
    for rel, _ in targets:
        current = dest
        for part in rel.parts[:-1]:
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise ValidationError(f"{current} is a link or a file, not a folder",
                                      hint=f"use an empty folder; {_NOTHING_WRITTEN}")
        target = dest.joinpath(*rel.parts)
        if target.is_symlink() or target.is_dir() or not target.resolve().is_relative_to(root):
            raise ValidationError(f"refusing to write {target}",
                                  hint=f"use an empty folder; {_NOTHING_WRITTEN}")


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
    if dest.exists() and dest.is_dir() and any(dest.iterdir()) and not force:
        raise ValidationError(f"{dest} is not empty",
                              hint="choose an empty folder or pass --force to overwrite files")
    _preflight(targets, dest)
    limit = asyncio.Semaphore(_PARALLEL_DOWNLOADS)

    async def fetch(rel: PurePosixPath, digest: str) -> bytes:
        async with limit:
            data = await client.apps.download_file(app.id, digest)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ServerError(f"{rel}: downloaded content does not match its SHA-256",
                              retriable=True, hint="re-run the export; nothing was written")
        return data

    contents = await asyncio.gather(*(fetch(rel, digest) for rel, digest in targets))
    try:
        for (rel, _), data in zip(targets, contents, strict=True):
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except OSError as exc:  # e.g. disk full; checks above rule out path problems
        raise ValidationError(f"could not write the export: {exc}",
                              hint=f"some files in {dest} may have been written") from exc
    return {
        "app_id": app.id,
        "title": app.title,
        "dir": str(dest),
        "count": len(targets),
        "bytes": sum(len(d) for d in contents),
        "files": [{"path": str(rel), "bytes": len(data)}
                  for (rel, _), data in zip(targets, contents, strict=True)],
    }
