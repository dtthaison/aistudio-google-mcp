"""Decode Build (apps) responses.

ListApplets (fixtures ListApplets/2-4) ``[[entry, ...]]``; two entry shapes:

| shape | layout |
|---|---|
| applet (Build store) | ``[null, [id, updated, created, [title, description, prompt, ...]]]`` |
| drive (older apps) | ``[[[[title, description, prompt, ...]], null, [owner, flags, null, |
|   | [modified, modifier]], drive_id]]`` |

ListRecentApplets ``[[[item, ...]]]``; item ``[opened, _, ref, title, description]`` where ref
has one filled slot: ``[8][0]`` app id, ``[1][0]`` gallery slug, ``[2][0]`` prompt/Drive id.

ListBundledApplets ``[[[group, [app, ...]], ...]]``; app
``[slug, title, description, apis, _, _, tags, video_url, image_url]``.

ListAppletTrajectories ``[[[id, started, updated, prompt], ...]]``. Timestamps ``[seconds, nanos]``.
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_str, as_strs, as_timestamp
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.apps import App, AppRun, GalleryApp, RecentItem
from aistudio.exceptions import SchemaDriftError


def _list_at(response: Any, *path: int, method: str, expected: str) -> list[Any]:
    if response is None or response == []:
        return []
    value = safe_index(response, *path, method=method, expected=expected)
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaDriftError(method, path, expected)
    return value


def decode_apps(response: Any) -> list[App]:
    entries = _list_at(response, 0, method="ListApplets", expected="the app list")
    return [_app(entry, i) for i, entry in enumerate(entries)]


def _app(entry: Any, i: int) -> App:
    applet = opt_index(entry, 1)
    applet_id = as_str(opt_index(applet, 0))
    if applet_id:
        meta = opt_index(applet, 3)
        return App(
            id=applet_id,
            title=as_str(opt_index(meta, 0)),
            description=as_str(opt_index(meta, 1)) or None,
            prompt=as_str(opt_index(meta, 2)),
            created=as_timestamp(opt_index(applet, 2)),
            updated=as_timestamp(opt_index(applet, 1)),
            storage="applet",
        )
    drive = opt_index(entry, 0)
    drive_id = as_str(opt_index(drive, 3))
    if drive_id:
        meta = opt_index(drive, 0, 0)
        return App(
            id=drive_id,
            title=as_str(opt_index(meta, 0)),
            description=as_str(opt_index(meta, 1)) or None,
            prompt=as_str(opt_index(meta, 2)),
            created=None,
            updated=as_timestamp(opt_index(drive, 2, 3, 0)),
            storage="drive",
        )
    raise SchemaDriftError("ListApplets", (0, i), "an app id")


def decode_recent(response: Any) -> list[RecentItem]:
    items = _list_at(response, 0, 0, method="ListRecentApplets", expected="the recent list")
    return [_recent(item) for item in items]


def _recent(item: Any) -> RecentItem:
    ref = opt_index(item, 2)
    kind, ref_id = "unknown", None
    for candidate, path in (("app", (8, 0)), ("gallery", (1, 0)), ("prompt", (2, 0))):
        found = as_str(opt_index(ref, *path))
        if found:
            kind, ref_id = candidate, found
            break
    return RecentItem(
        kind=kind,
        id=ref_id,
        title=as_str(opt_index(item, 3)),
        description=as_str(opt_index(item, 4)) or None,
        opened=as_timestamp(opt_index(item, 0)),
    )


def decode_gallery(response: Any) -> list[GalleryApp]:
    groups = _list_at(response, 0, method="ListBundledApplets", expected="the gallery groups")
    apps: list[GalleryApp] = []
    for group in groups:
        name = as_str(opt_index(group, 0))
        entries = opt_index(group, 1)
        for entry in entries if isinstance(entries, list) else []:
            slug = as_str(opt_index(entry, 0))
            if not slug:
                continue
            apps.append(GalleryApp(
                slug=slug,
                title=as_str(opt_index(entry, 1)),
                description=as_str(opt_index(entry, 2)) or None,
                group=name,
                apis=as_strs(opt_index(entry, 3)),
                tags=as_strs(opt_index(entry, 6)),
                video_url=as_str(opt_index(entry, 7)),
                image_url=as_str(opt_index(entry, 8)),
            ))
    return apps


def decode_runs(response: Any) -> list[AppRun]:
    runs = _list_at(response, 0, method="ListAppletTrajectories", expected="the run list")
    out: list[AppRun] = []
    for run in runs:
        run_id = as_str(opt_index(run, 0))
        if run_id:
            out.append(AppRun(
                id=run_id,
                prompt=as_str(opt_index(run, 3)),
                started=as_timestamp(opt_index(run, 1)),
                updated=as_timestamp(opt_index(run, 2)),
            ))
    return out


def decode_snapshot_stamp(response: Any) -> tuple[str, int] | None:
    """GetApplet ``[0][6]``: the latest code snapshot time ``[seconds_str, nanos]``."""
    stamp = opt_index(response, 0, 6)
    seconds = opt_index(stamp, 0)
    if not isinstance(seconds, (str, int)) or isinstance(seconds, bool):
        return None
    nanos = opt_index(stamp, 1)
    return str(seconds), nanos if isinstance(nanos, int) and not isinstance(nanos, bool) else 0


def decode_snapshot_files(response: Any) -> list[tuple[str, str]]:
    """GetCodeAssistantSnapshot ``[["model", summary, _, ts, [[path, sha256], ...]]]``."""
    entries = opt_index(response, 0, 4)
    files: list[tuple[str, str]] = []
    for i, entry in enumerate(entries if isinstance(entries, list) else []):
        path, digest = opt_index(entry, 0), opt_index(entry, 1)
        if not isinstance(path, str) or not isinstance(digest, str):
            # Never let an export "succeed" with files silently missing.
            raise SchemaDriftError("GetCodeAssistantSnapshot", (0, 4, i), "a [path, sha256] pair")
        files.append((path, digest))
    return files
