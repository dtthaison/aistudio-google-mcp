"""Decode Flow media records, credits, and the agent's quote and plan (findings "Google Flow")."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from aistudio._rpc.wire import opt_index

_VIDEO = "https://flow-content.google/video/"
_IMAGE = "https://flow-content.google/image/"
_SECONDS = re.compile(r"(\d+)-second", re.I)
_MODEL = re.compile(r"\busing ((?:Veo|Omni|Nano Banana)[\w .\-]*?)"
                    r"(?=[.,;!?](?:\s|$)|\s+(?:with|in|at|and)\b|$)", re.I)


@dataclass(frozen=True)
class FlowMedia:
    media_id: str
    project_id: str | None
    prompt: str | None
    model_key: str | None
    status: int | None  # 2 running, 3 done
    created: int | None  # epoch seconds, from Flow's job record
    seconds: int | None
    size: int | None
    video_url: str | None
    image_url: str | None


@dataclass(frozen=True)
class Quote:
    count: int
    kind: str  # "video" | "image" | "mixed"
    credits: int


@dataclass(frozen=True)
class Plan:
    model: str | None  # as the agent names it, e.g. "Veo 3.1 - Lite"
    seconds: int | None


@dataclass(frozen=True)
class FlowProject:
    id: str
    title: str | None
    created: int | None


@dataclass(frozen=True)
class FlowCharacter:
    id: str
    name: str
    media_id: str | None  # the character's image; None until it has one
    created: int | None


@dataclass(frozen=True)
class SceneClip:
    media_id: str
    title: str | None
    start: int  # whole seconds into the clip
    end: int


@dataclass(frozen=True)
class FlowScene:
    id: str
    title: str | None
    created: int | None
    clips: list[SceneClip]


@dataclass(frozen=True)
class ProjectContents:
    titles: dict[str, str]  # media_id -> its workflow title (what the grid shows)
    media: list[FlowMedia]
    workflows: dict[str, str] = field(default_factory=dict)  # media_id -> workflow id (editor)
    characters: list[FlowCharacter] = field(default_factory=list)
    scenes: list[FlowScene] = field(default_factory=list)


def decode_projects(payload: Any) -> list[FlowProject]:
    out = []
    for row in opt_index(payload, 0) or []:
        pid, meta = opt_index(row, 0), opt_index(row, 1)
        if isinstance(pid, str) and isinstance(meta, list):
            created = opt_index(meta, 2, 0)
            out.append(FlowProject(pid, _str(opt_index(meta, 0)),
                                   created if isinstance(created, int) else None))
    return out


def decode_contents(payload: Any) -> ProjectContents:
    titles: dict[str, str] = {}
    workflows: dict[str, str] = {}
    for workflow in opt_index(payload, 1) or []:
        title, media_id = opt_index(workflow, 3, 0), opt_index(workflow, 3, 4)
        if isinstance(title, str) and isinstance(media_id, str):
            titles[media_id] = title
            if isinstance(opt_index(workflow, 0), str):
                workflows[media_id] = workflow[0]
    media = [m for m in (_record(r) for r in opt_index(payload, 2) or []) if m is not None]
    return ProjectContents(titles, media, workflows, _characters(opt_index(payload, 5)),
                           _scenes(opt_index(payload, 4), opt_index(payload, 6)))


def _characters(rows: Any) -> list[FlowCharacter]:
    """``[project_id, id, None, [1, name, refs], image_media_id, [w, h], [created], …]``."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        cid, name = _str(opt_index(row, 1)), opt_index(row, 3, 1)
        if cid and isinstance(name, str):  # "" stays: an unnamed character can be renamed
            out.append(FlowCharacter(cid, name, _str(opt_index(row, 4)),
                                     _int(opt_index(row, 6, 0))))
    return out


def _scenes(rows: Any, clip_rows: Any) -> list[FlowScene]:
    """Scenes ``[id, title, None, [created], …]``; clips ``[[workflow …, media_id …], scene_id,
    [position, [seconds], [start], [end], …]]`` (position None for the first)."""
    clips: dict[str, list[tuple[int, SceneClip]]] = {}
    for row in clip_rows if isinstance(clip_rows, list) else []:
        media_id, scene_id = _str(opt_index(row, 0, 3, 4)), _str(opt_index(row, 1))
        if media_id and scene_id:
            place = opt_index(row, 2)
            clip = SceneClip(media_id, _str(opt_index(row, 0, 3, 0)),
                             _seconds(opt_index(place, 2)), _seconds(opt_index(place, 3)))
            clips.setdefault(scene_id, []).append((_int(opt_index(place, 0)) or 0, clip))
    out = []
    for row in rows if isinstance(rows, list) else []:
        sid = _str(opt_index(row, 0))
        if sid:
            ordered = [clip for _, clip in sorted(clips.get(sid, []), key=lambda c: c[0])]
            out.append(FlowScene(sid, _str(opt_index(row, 1)), _int(opt_index(row, 3, 0)),
                                 ordered))
    return out


def _seconds(value: Any) -> int:
    """A ``[seconds]`` duration (``[]`` is 0)."""
    return _int(opt_index(value, 0)) or 0


def stitch_job(payload: Any) -> str | None:
    """``GjQLTd`` reply: ``[None, [["projects/…/jobs/<uuid>"]]]``."""
    job = opt_index(payload, 1, 0, 0)
    return job if isinstance(job, str) and job.startswith("projects/") else None


def stitch_state(payload: Any) -> tuple[int | None, str | None]:
    """``IxdSwd`` reply: ``[2, "", "", 0]`` running, ``[3, "", "", 3, <base64 MP4>]`` done."""
    data = opt_index(payload, 4)
    return _int(opt_index(payload, 0)), data if isinstance(data, str) and data else None


def sent_characters(body: str) -> list[str]:
    """Character ids in a StreamChat request:
    ``[session, [[[[prompt]]], media, None, [[character_id], …]], …]`` inside ``f.req``."""
    return _sent_ids(body, 3)


def sent_media(body: str) -> list[str]:
    """Attached media ids in a StreamChat request: slot 1, ``[[media_id, …], …]``."""
    return _sent_ids(body, 1)


def _sent_ids(body: str, slot: int) -> list[str]:
    raw = parse_qs(body).get("f.req", [body])[0]
    try:
        inner = opt_index(json.loads(raw), 1)
        message = json.loads(inner) if isinstance(inner, str) else None
    except ValueError:
        return []
    return [c[0] for c in opt_index(message, 1, slot) or []
            if isinstance(c, list) and c and isinstance(c[0], str)]


def linked_image(payload: Any) -> str | None:
    """``Sc7aEb`` reply: the character's new image record first, ``[[media_id, …], …]``."""
    return _str(opt_index(payload, 0, 0))


def character_name(payload: Any) -> str | None:
    """``C4BZMd``/``rzMKMb`` reply: ``[[project_id, id, None, [1, name, …], …]]``."""
    return _str(opt_index(payload, 0, 3, 1))


def character_id(payload: Any) -> str | None:
    return _str(opt_index(payload, 0, 1))


def extend_started(payload: Any) -> tuple[int | None, list[FlowMedia]]:
    credits = opt_index(payload, 1)
    media = [m for m in (_record(r) for r in opt_index(payload, 3) or []) if m is not None]
    return (credits if isinstance(credits, int) and not isinstance(credits, bool) else None,
            media)


def media_records(method: str, payload: Any) -> list[FlowMedia]:
    """``jwpduf``: ``[None, credits, [record, …]]``; ``as29s``/``bOKtO``: one record (or a list)."""
    if method == "jwpduf":
        rows = opt_index(payload, 2)
    elif method == "ogiZ0b":  # a character image: [[record, …], …]
        rows = opt_index(payload, 0)
    elif method in ("as29s", "bOKtO"):
        rows = [payload] if isinstance(opt_index(payload, 0), str) else (
            opt_index(payload, 0) if isinstance(opt_index(payload, 0), list) else [])
    else:
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        media = _record(row) or (_image_row(row) if method == "ogiZ0b" else None)
        if media is not None:
            out.append(media)
    return out


def _image_row(row: Any) -> FlowMedia | None:
    """A character image (ogiZ0b, live): ``[media_id, null, workflow_id, …, [[…, link, 3]]]``."""
    media_id = opt_index(row, 0)
    if not isinstance(media_id, str) or not media_id:
        return None
    link = next((s for s in _strings(row) if s.startswith(_IMAGE)), None)
    return FlowMedia(media_id=media_id, project_id=None, prompt=None, model_key=None,
                     status=3 if link else None, created=None, seconds=None, size=None,
                     video_url=None, image_url=link)


def credits_left(payload: Any) -> int | None:
    value = opt_index(payload, 1)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_permission(payload: Any) -> Quote | None:
    """Flow's ``ask_for_permission`` call in a StreamChat payload (findings): only counts and cost.

    ``["ask_for_permission", "ask_for_permission", [[["num_videos", [None, n]], …]]]``
    """
    for node in _lists(payload):
        if len(node) >= 3 and node[0] == "ask_for_permission" and isinstance(node[2], list):
            params = {pair[0]: pair[1][1] for pair in _lists(node[2])
                      if len(pair) == 2 and isinstance(pair[0], str)
                      and isinstance(pair[1], list) and len(pair[1]) == 2}
            videos, images = params.get("num_videos"), params.get("num_images")
            cost = params.get("total_cost")
            if not all(isinstance(v, int) and not isinstance(v, bool)
                       for v in (videos, images, cost)):
                return None
            kind = ("video" if videos and not images else "image" if images and not videos
                    else "mixed")
            return Quote(count=videos + images, kind=kind, credits=cost)
    return None


def parse_plan(text: str) -> Plan:
    model = _MODEL.search(text)
    seconds = _SECONDS.search(text)
    return Plan(model=model.group(1).strip() if model else None,
                seconds=int(seconds.group(1)) if seconds else None)


def _record(row: Any) -> FlowMedia | None:
    media_id = opt_index(row, 0)
    details = opt_index(row, 5)
    if not isinstance(media_id, str) or not media_id or not isinstance(details, list):
        return None
    links = list(_strings(row))
    status = opt_index(details, 8, 0)
    created = opt_index(details, 0, 0)
    seconds = opt_index(row, 7, 1, 2, 0)
    size = opt_index(details, 13)
    return FlowMedia(
        media_id=media_id,
        project_id=opt_index(row, 1) if isinstance(opt_index(row, 1), str) else None,
        prompt=opt_index(details, 1) if isinstance(opt_index(details, 1), str) else None,
        model_key=_str(opt_index(details, 6, 1, 0, 0)),
        status=status if isinstance(status, int) else None,
        created=created if isinstance(created, int) else None,
        seconds=seconds if isinstance(seconds, int) else None,
        size=size if isinstance(size, int) else None,
        video_url=next((s for s in links if s.startswith(_VIDEO)), None),
        image_url=next((s for s in links if s.startswith(_IMAGE)), None),
    )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _lists(node: Any):
    if isinstance(node, list):
        yield node
        for child in node:
            yield from _lists(child)


def _strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for child in node:
            yield from _strings(child)
