"""Build (apps) read use cases (spec §6.7)."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from aistudio._app.diff import diff_files, summarize
from aistudio.client import AIStudioClient
from aistudio.exceptions import NotFoundError, ServerError, ValidationError

T = TypeVar("T")


def pick(items: Sequence[T], key: Callable[[T], str], wanted: str, *, noun: str,
         hint: str) -> T:
    """Exact match first, then ignoring case, then a unique prefix (as typed, then ignoring case;
    spec §2.1)."""
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
    sensitive = [item for item in items if key(item).startswith(wanted)]
    if not exact and len(sensitive) == 1:
        return sensitive[0]
    matches = exact or [item for item in items if key(item).casefold().startswith(folded)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(sorted(key(m) for m in matches)[:10])
        raise ValidationError(f"{wanted!r} matches several {noun}s: {names}",
                              hint="type more of the id")
    raise NotFoundError(f"no {noun} {wanted!r}", hint=hint)


async def list_apps(client: AIStudioClient, *, limit: int = 1000) -> dict[str, Any]:
    apps, skipped = await client.apps.list_checked(limit)
    apps = apps[:limit]
    warnings = ([f"skipped {skipped} app entr{'y' if skipped == 1 else 'ies'} AI Studio sent "
                 "in an unknown shape"] if skipped else [])
    return {"apps": [a.to_dict() for a in apps], "count": len(apps),
            **({"warnings": warnings} if warnings else {})}


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


async def _download_verified(client: AIStudioClient, app_id: str, rel: PurePosixPath | str,
                             digest: str, limit: asyncio.Semaphore, *, hint: str) -> bytes:
    async with limit:
        data = await client.apps.download_file(app_id, digest)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ServerError(f"{rel}: downloaded content does not match its SHA-256",
                          retriable=True, hint=hint)
    return data


async def export_app(client: AIStudioClient, app_ref: str, dest: Path, *,
                     force: bool = False,
                     progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Download a Build app's latest code into ``dest``, verified by SHA-256 (findings).

    ``progress(0, total)`` is called before the downloads, then ``progress(done, total)`` after
    each file downloads and verifies.
    """
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
    done: list[PurePosixPath] = []

    async def fetch(rel: PurePosixPath, digest: str) -> bytes:
        data = await _download_verified(client, app.id, rel, digest, limit,
                                        hint="re-run the export; nothing was written")
        done.append(rel)
        if progress is not None:
            progress(len(done), len(targets))
        return data

    if progress is not None:
        progress(0, len(targets))  # the count first: a slow link never looks stuck at once
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


_SKIPPED_FOLDERS = frozenset({".git", "node_modules"})


def _read_folder(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root`` (``path -> content``), ignoring links, ``.git`` and
    ``node_modules``."""
    files: dict[str, bytes] = {}
    for current, folders, names in os.walk(root, followlinks=False):
        folders[:] = sorted(f for f in folders if f not in _SKIPPED_FOLDERS
                            and not Path(current, f).is_symlink())
        for name in sorted(names):
            path = Path(current, name)
            if path.is_symlink() or not path.is_file():
                continue
            try:
                files[path.relative_to(root).as_posix()] = path.read_bytes()
            except OSError as exc:
                raise ValidationError(f"could not read {path}: {exc.strerror or exc}",
                                      hint="check the folder's permissions") from exc
    return files


async def diff_app(client: AIStudioClient, app_ref: str, *, turn: int | None = None,
                   against: Path | None = None) -> dict[str, Any]:
    """What a builder turn changed in an app's files, or how its code differs from a folder.

    Build keeps the files as they stood at the end of every turn (findings), so by default turn
    ``turn`` (the last one when None) is compared with the turn before it; turn 1 with nothing.
    With ``against`` the app's current code is compared with that local folder (for example an
    earlier ``app export``): the folder is the "before".
    """
    if turn is not None and against is not None:
        raise ValidationError("--turn and --against cannot be combined",
                              hint="--turn compares Build's own turns; --against a folder")
    if against is not None and not against.is_dir():
        raise ValidationError(f"{against} is not a folder", hint="give a folder, e.g. an export")
    app = pick(await client.apps.list(), lambda a: a.id, app_ref, noun="app",
               hint='see "aistudio app list"')
    if app.storage != "applet":
        raise ValidationError(f"{app.title or app.id!r} is stored in Google Drive (an older Build "
                              "app); aistudio can only diff Build-store apps",
                              hint="open it at https://aistudio.google.com/apps")
    known: dict[str, bytes] = {}  # digest -> content we already hold (the local folder)
    meta: dict[str, Any]
    if against is None:
        turns = await client.apps.turn_snapshots(app.id)
        if not turns:
            raise NotFoundError(f"app {app.id} has no saved turns to compare",
                                hint="compare with a folder instead: --against <dir>")
        number = len(turns) if turn is None else turn
        if not 1 <= number <= len(turns):
            noun = "turn" if len(turns) == 1 else "turns"
            raise ValidationError(f"{app.title or app.id!r} has {len(turns)} {noun}; "
                                  f"{number} is out of range",
                                  hint=f"use --turn 1..{len(turns)}")
        new_files = dict(turns[number - 1].files)
        old_files = dict(turns[number - 2].files) if number > 1 else {}
        meta = {"mode": "turn", "turn": number, "turns": len(turns),
                "prompt": turns[number - 1].prompt, "saved": turns[number - 1].saved,
                "against": None}
    else:
        files = await client.apps.snapshot_files(app.id)
        if not files:
            raise NotFoundError(f"app {app.id} has no saved code yet",
                                hint=f'build it first: aistudio app prompt {app.id[:8]} "…"')
        new_files = dict(files)
        local = _read_folder(against)
        old_files = {}
        for path, data in local.items():
            digest = hashlib.sha256(data).hexdigest()
            old_files[path] = digest
            known[digest] = data
        meta = {"mode": "folder", "turn": None, "turns": None, "prompt": None, "saved": None,
                "against": str(against)}
    for path in new_files if against is not None else {*old_files, *new_files}:
        _safe_relative(path)  # paths of the app only ever label output, but keep them tame
    changed = sorted(p for p in old_files.keys() | new_files.keys()
                     if old_files.get(p) != new_files.get(p))
    names: dict[str, str] = {}  # digest -> a path it belongs to, for error messages
    for path in changed:
        for side in (old_files, new_files):
            wanted = side.get(path)
            if wanted is not None and wanted not in known:
                names.setdefault(wanted, path)
    limit = asyncio.Semaphore(_PARALLEL_DOWNLOADS)
    digests = sorted(names)
    blobs = await asyncio.gather(*(
        _download_verified(client, app.id, names[digest], digest, limit,
                           hint="run the command again") for digest in digests))
    content = {**known, **dict(zip(digests, blobs, strict=True))}
    changes = diff_files(
        {p: content[old_files[p]] for p in changed if p in old_files},
        {p: content[new_files[p]] for p in changed if p in new_files})
    return {"app_id": app.id, "title": app.title, **meta,
            "summary": summarize(changes), "files": [c.to_dict() for c in changes]}
