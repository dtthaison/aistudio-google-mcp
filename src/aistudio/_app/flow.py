"""Google Flow use cases: credits and agent-driven video/image generation (spec §3–§7)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._browser.ui import open_browser
from aistudio._flow.download import download_signed
from aistudio._flow.models import Decision, Expect, check_options, decide, find_model, instruction
from aistudio._flow.page import PROJECT_URL, Defaults, FlowPage
from aistudio._flow.rows import parse_plan
from aistudio._fs import read_json, write_private_json
from aistudio._media.save import check_folder, free_name, write_file
from aistudio.exceptions import (
    AgentReplyError,
    AIStudioError,
    NotFoundError,
    OverBudgetError,
    PlanMismatchError,
    SchemaDriftError,
    ServerError,
    ValidationError,
)
from aistudio.paths import Profile

STATE_FILE = "flow.json"
DEFAULT_PROJECT_TITLE = "aistudio"
_PROJECT = re.compile(r"(?:project/)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{12})/?$")


async def flow_credits(profile: Profile, *, visible: bool = False,
                       browser: Callable[..., Any] = open_browser,
                       page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        credits = await page_factory(ui).credits()
    if credits is None:
        raise SchemaDriftError("Flow account menu", (), "a credits count")
    return {"credits": credits}


async def flow_generate(
    profile: Profile,
    kind: str,
    prompt: str,
    *,
    model: str | None = None,
    aspect: str | None = None,
    count: int | None = None,
    duration: int | None = None,
    max_credits: int | None = None,
    project: str | None = None,
    out: Path | None = None,
    overwrite: bool = False,
    timeout_s: float = 600.0,
    visible: bool = False,
    browser: Callable[..., Any] = open_browser,
    downloader: Callable[[str], Any] = download_signed,
    now: Callable[[], datetime] = datetime.now,
    page_factory: Callable[[Any], Any] = FlowPage,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValidationError(f"nothing to generate: the {kind} prompt is empty")
    if max_credits is not None and max_credits < 0:
        raise ValidationError("--max-credits must be 0 or more")
    project_id = _project_id(project) if project else None
    flow_model = find_model(model, kind) if model else None
    check_options(kind, flow_model, aspect, count, duration)
    _check_output(out, count or 1, overwrite)
    warnings: list[str] = []
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        previous = await page.read_defaults(kind)
        if not previous.asks:
            raise ValidationError(
                "Flow's agent is set to generate without asking first",
                hint="in Flow's settings set 'Confirm before generating' to Always; with Never, "
                     "any prompt can spend credits. Nothing was sent")
        wanted = Defaults(flow_model.label if flow_model else previous.model,
                          aspect or previous.aspect, count or previous.count)
        _check_output(out, wanted.count, overwrite)  # the count may come from Flow's defaults
        try:
            if wanted != previous:
                await page.apply_defaults(kind, wanted)
            await page.new_session()
            turn = await page.submit_for_quote(instruction(
                kind, prompt, count=wanted.count,
                model_label=flow_model.label if flow_model else None, seconds=duration,
                aspect=aspect))
            if turn.quote is None:
                raise AgentReplyError(f"Flow's agent replied without a quote: {turn.text[-500:]}",
                                      hint="rephrase the prompt or add options; nothing was "
                                           "spent")
            expect = Expect(kind, wanted.count, flow_model.label if flow_model else None,
                            duration)
            decision, wrong = decide(turn.quote, parse_plan(turn.text), expect, max_credits)
            if decision is Decision.QUOTE_ONLY:
                return {"status": "quoted", "credits": turn.quote.credits,
                        "count": turn.quote.count, "plan": turn.text, "project": project_id,
                        **({"warnings": wrong} if wrong else {})}
            if decision is Decision.REJECT_MISMATCH:
                await page.reject()
                raise PlanMismatchError("; ".join(wrong),
                                        hint="nothing was spent; adjust the flags or the prompt")
            if decision is Decision.REJECT_OVER_BUDGET:
                await page.reject()
                raise OverBudgetError(f"Flow quotes {turn.quote.credits} credits, over "
                                      f"--max-credits {max_credits}",
                                      hint=f"nothing was spent; re-run with --max-credits "
                                           f"{turn.quote.credits} to accept")
            try:
                outcome = await page.approve_and_follow(count=wanted.count, timeout_s=timeout_s,
                                                        kind=kind)
            except Exception as exc:  # after Approve: credits may be spent; never invite a re-run
                raise _spent(exc, page.paid_media, project_id) from exc
        finally:
            if wanted != previous:
                try:
                    await page.apply_defaults(kind, previous)
                except Exception:  # noqa: BLE001 - restoring must never hide the run's outcome
                    warnings.append(f"could not restore Flow's {kind} defaults "
                                    f"({previous.model}, {previous.aspect}, x{previous.count}); "
                                    "set them in Flow's settings")
    project_url = PROJECT_URL.format(id=project_id)
    if outcome.timed_out:
        return {"status": "timeout", "media_ids": [m.media_id for m in outcome.media],
                "project_url": project_url, "credits_spent": turn.quote.credits,
                **({"warnings": warnings} if warnings else {})}
    if duration is not None:
        lengths = {m.seconds for m in outcome.media if m.seconds is not None}
        warnings += [f"Flow made a {n}-second video, not the {duration} seconds asked for"
                     for n in sorted(lengths) if n != duration]
    files = await _save_all(kind, outcome.media, out=out, overwrite=overwrite,
                            downloader=downloader, stamp=now(), project_url=project_url)
    return {"status": "done", "files": files, "credits_spent": turn.quote.credits,
            "credits_left": outcome.credits_left,
            "model": flow_model.id if flow_model else wanted.model, "project": project_id,
            **({"warnings": warnings} if warnings else {})}


def _spent(exc: Exception, media_ids: list[str], project_id: str) -> AIStudioError:
    """An error after Approve: credits may be spent, so name the media and forbid a re-run."""
    note = (f"credits were spent: Flow made media {', '.join(media_ids) or '(not seen yet)'} in "
            f"{PROJECT_URL.format(id=project_id)}; do not re-run, check the project in Flow")
    if isinstance(exc, AIStudioError):
        exc.hint = note
        return exc
    return ServerError(f"Flow job interrupted: {type(exc).__name__}: {exc}", hint=note)


def _project_id(value: str) -> str:
    match = _PROJECT.search(value.strip())
    if match is None:
        raise ValidationError(f"{value!r} is not a Flow project id or link",
                              hint="copy the id from https://flow.google.com/project/<id>")
    return match.group(1)


def _check_output(out: Path | None, count: int, overwrite: bool) -> None:
    if count > 1:
        if out is not None and not out.is_dir():
            raise ValidationError(f"{count} outputs need -o to be an existing folder",
                                  hint="leave out -o to save them in the current folder")
        check_folder(out or Path("."))
        return
    if out is not None and out.exists() and not overwrite:
        raise ValidationError(f"{out} already exists", hint="pass --force or choose another -o")
    check_folder(out.parent if out is not None and str(out.parent) else Path("."))


async def _open(page: Any, profile: Profile, project_id: str | None) -> str:
    if project_id is not None:
        await page.open_project(project_id)
        return project_id
    state = profile.root / STATE_FILE
    stored = (read_json(state) or {}).get("project_id") if state.exists() else None
    if stored:
        try:
            await page.open_project(stored)
            return stored
        except NotFoundError:
            pass  # deleted in Flow, or did not load: make a new one
    created = await page.create_project(DEFAULT_PROJECT_TITLE)
    profile.ensure()
    write_private_json(state, {"project_id": created})
    await page.open_project(created)
    return created


async def _save_all(kind: str, media: list[Any], *, out: Path | None, overwrite: bool,
                    downloader: Callable[[str], Any], stamp: datetime,
                    project_url: str) -> list[dict[str, Any]]:
    ids = ", ".join(m.media_id for m in media)
    folder = out if out is not None and out.is_dir() else Path(".")
    files = []
    try:
        for i, item in enumerate(media):
            url = item.video_url if kind == "video" else item.image_url
            if not url:
                raise SchemaDriftError("as29s", (), f"a {kind} link")
            data = await downloader(url)
            mime, ext = _type_of(kind, data)
            if out is not None and not out.is_dir() and len(media) == 1:
                path = out
            else:
                suffix = f"-{i + 1}" if len(media) > 1 else ""
                path = free_name(folder / f"flow-{kind}-{stamp:%Y%m%d-%H%M%S}{suffix}{ext}")
            saved = write_file(path, data, overwrite=overwrite and path == out)
            files.append({"path": str(saved), "mime_type": mime, "bytes": len(data),
                          "media_id": item.media_id})
    except Exception as exc:  # a paid result: name it whatever went wrong
        note = f"Flow made media {ids} in {project_url}; download it there"
        if not isinstance(exc, AIStudioError):
            raise ServerError(f"saving Flow media failed: {type(exc).__name__}: {exc}",
                              hint=note) from exc
        exc.hint = f"{note} ({exc.hint})" if exc.hint else note
        raise
    return files


def _type_of(kind: str, data: bytes) -> tuple[str, str]:
    if kind == "video":
        return "video/mp4", ".mp4"
    if data.startswith(b"\x89PNG"):
        return "image/png", ".png"
    return "image/jpeg", ".jpg"
