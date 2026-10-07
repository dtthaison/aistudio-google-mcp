"""Decode Flow media records, credits, and the agent's quote and plan (findings "Google Flow")."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from aistudio._rpc.wire import opt_index

_VIDEO = "https://flow-content.google/video/"
_IMAGE = "https://flow-content.google/image/"
_QUOTE = re.compile(r"kick off (?:this|these) ([\d,]+) (video|image) generations?, costing "
                    r"([\d,]+) credits?", re.I)
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
    seconds: int | None
    size: int | None
    video_url: str | None
    image_url: str | None


@dataclass(frozen=True)
class Quote:
    count: int
    kind: str  # "video" | "image"
    credits: int


@dataclass(frozen=True)
class Plan:
    model: str | None  # as the agent names it, e.g. "Veo 3.1 - Lite"
    seconds: int | None


def media_records(method: str, payload: Any) -> list[FlowMedia]:
    """``jwpduf``: ``[None, credits, [record, …]]``; ``as29s``/``bOKtO``: one record (or a list)."""
    if method == "jwpduf":
        rows = opt_index(payload, 2)
    elif method in ("as29s", "bOKtO"):
        rows = [payload] if isinstance(opt_index(payload, 0), str) else (
            opt_index(payload, 0) if isinstance(opt_index(payload, 0), list) else [])
    else:
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        media = _record(row)
        if media is not None:
            out.append(media)
    return out


def credits_left(payload: Any) -> int | None:
    value = opt_index(payload, 1)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_quote(text: str) -> Quote | None:
    match = _QUOTE.search(text)
    if match is None:
        return None
    return Quote(count=int(match.group(1).replace(",", "")), kind=match.group(2).lower(),
                 credits=int(match.group(3).replace(",", "")))


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
    seconds = opt_index(row, 7, 1, 2, 0)
    size = opt_index(details, 13)
    return FlowMedia(
        media_id=media_id,
        project_id=opt_index(row, 1) if isinstance(opt_index(row, 1), str) else None,
        prompt=opt_index(details, 1) if isinstance(opt_index(details, 1), str) else None,
        model_key=_str(opt_index(details, 6, 1, 0, 0)),
        status=status if isinstance(status, int) else None,
        seconds=seconds if isinstance(seconds, int) else None,
        size=size if isinstance(size, int) else None,
        video_url=next((s for s in links if s.startswith(_VIDEO)), None),
        image_url=next((s for s in links if s.startswith(_IMAGE)), None),
    )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for child in node:
            yield from _strings(child)
