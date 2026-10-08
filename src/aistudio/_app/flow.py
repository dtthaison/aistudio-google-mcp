"""Google Flow use cases: credits and agent-driven video/image generation (spec §3–§7)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aistudio._browser.ui import open_browser
from aistudio._flow.download import download_signed
from aistudio._flow.models import (
    MODEL_KEYS,
    Decision,
    Expect,
    check_direct_spend,
    check_options,
    decide,
    find_model,
    instruction,
    label_to_id,
    model_by_label,
)
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
        err = SchemaDriftError("Flow account menu", (), "a credits count")
        err.hint = "re-run; if it persists, the Flow UI changed"
        raise err
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
    start_frame: str | None = None,
    end_frame: str | None = None,
    ingredients: list[str] | None = None,
    i_have_rights: bool = False,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValidationError(f"nothing to generate: the {kind} prompt is empty")
    attachments = _attachments(start_frame, end_frame, ingredients or [], i_have_rights)
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
        titles, known_ids = await _attachment_titles(page, project_id, attachments)
        previous = await page.read_defaults(kind)
        if not previous.asks:
            raise ValidationError(
                "Flow's agent is set to generate without asking first",
                hint="in Flow's settings set 'Confirm before generating' to Always; with Never, "
                     "any prompt can spend credits. Nothing was sent")
        wanted = Defaults(flow_model.label if flow_model else previous.model,
                          aspect or previous.aspect, count or previous.count)
        _check_output(out, wanted.count, overwrite)  # the count may come from Flow's defaults
        if duration is not None and flow_model is None:  # check against the default model
            check_options(kind, model_by_label(previous.model), None, None, duration)
        uploads: list[dict[str, str]] = []
        try:
            if wanted != previous:
                await page.apply_defaults(kind, wanted)
            await page.new_session()
            for (_, value), title in zip(attachments, titles, strict=True):
                if isinstance(value, Path):  # --i-have-rights was checked before Chrome opened
                    media_id = await page.upload(value, known_ids=known_ids)
                    uploads.append({"file": value.name, "media_id": media_id})
                await page.attach(title)
            if attachments:
                roles = ", ".join(role for role, _ in attachments)
                prompt = f"Use the attached images, in order, as: {roles}. {prompt}"
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
                # Declined in Flow too: a waiting request could later be approved there with
                # the restored settings, not the quoted ones.
                await page.reject()
                return {"status": "quoted", "declined": True, "credits": turn.quote.credits,
                        "count": turn.quote.count, "plan": turn.text, "project": project_id,
                        **({"uploads": uploads} if uploads else {}),
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
    expected_id = flow_model.id if flow_model else label_to_id(wanted.model)
    made = {MODEL_KEYS[m.model_key] for m in outcome.media if m.model_key in MODEL_KEYS}
    warnings += [f"Flow made the {kind} with {got}, not {expected_id}"
                 for got in sorted(made) if got != expected_id]
    if duration is not None:
        lengths = {m.seconds for m in outcome.media if m.seconds is not None}
        warnings += [f"Flow made a {n}-second video, not the {duration} seconds asked for"
                     for n in sorted(lengths) if n != duration]
    files, saved_notes = await _save_all(kind, outcome.media, out=out, overwrite=overwrite,
                                         downloader=downloader, stamp=now(),
                                         project_url=project_url)
    warnings += saved_notes
    return {"status": "done", "files": files, "credits_spent": turn.quote.credits,
            "credits_left": outcome.credits_left,
            "model": expected_id, "project": project_id,
            **({"warnings": warnings} if warnings else {})}


_MEDIA_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _attachments(start_frame: str | None, end_frame: str | None, ingredients: list[str],
                 i_have_rights: bool) -> list[tuple[str, Path | str]]:
    """(role, local path or media id) in attach order; local files need --i-have-rights."""
    wanted = [("start frame", start_frame), ("end frame", end_frame)]
    wanted += [("ingredient", value) for value in ingredients]
    out: list[tuple[str, Path | str]] = []
    for role, value in wanted:
        if value is None:
            continue
        path = Path(value).expanduser()
        if path.is_file():
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                raise ValidationError(f"{path} is not an image (.jpg, .png or .webp)")
            if not i_have_rights:
                raise ValidationError(
                    f"uploading {path.name} needs your confirmation that you have the rights to it",
                    hint="pass --i-have-rights if you have the necessary rights to this file; "
                         "nothing was uploaded")
            out.append((role, path))
        elif _MEDIA_ID.match(value.strip()):
            out.append((role, value.strip()))
        else:
            raise ValidationError(f"{value!r} is neither an image file nor a Flow media id",
                                  hint='see "aistudio flow media" for media ids')
    return out


async def _attachment_titles(page: Any, project_id: str,
                             attachments: list[tuple[str, Path | str]]
                             ) -> tuple[list[str], set[str]]:
    """The grid titles to attach by, checked before anything is uploaded or sent: attach works
    by title, so each title must name exactly one media in the project."""
    if not attachments:
        return [], set()
    contents = await page.contents(project_id)
    by_title: dict[str, list[str]] = {}
    for media_id, title in contents.titles.items():
        by_title.setdefault(title, []).append(media_id)
    titles = []
    for _, value in attachments:
        if isinstance(value, Path):
            existing = by_title.get(value.name)
            if existing:
                raise ValidationError(f"{value.name} is already in this Flow project",
                                      hint=f"pass its media id instead: {', '.join(existing)}; "
                                           "nothing was uploaded")
            titles.append(value.name)
            continue
        title = contents.titles.get(value)
        if title is None:
            raise NotFoundError(f"no media {value} in this Flow project",
                                hint='see "aistudio flow media"; nothing was sent')
        if len(by_title.get(title, [])) > 1:
            raise ValidationError(f"several media share the same title {title!r}",
                                  hint="rename one in Flow so aistudio can attach the right one; "
                                       "nothing was sent")
        titles.append(title)
    return titles, set(contents.titles) | {m.media_id for m in contents.media}


def _spent(exc: Exception, media_ids: list[str], project_id: str) -> AIStudioError:
    """An error after Approve: credits may be spent, so name the media and forbid a re-run."""
    note = (f"credits were spent: Flow made media {', '.join(media_ids) or '(not seen yet)'} in "
            f"{PROJECT_URL.format(id=project_id)}; do not re-run, check the project in Flow")
    if isinstance(exc, AIStudioError):
        exc.hint = note
        return exc
    return ServerError(f"Flow job interrupted: {type(exc).__name__}: {exc}", hint=note)


def _iso(epoch: int | None) -> str | None:
    return (datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if epoch else None)


def _media_kind(media: Any) -> str:
    """Flow's project list carries no links (live), so judge by length and model too."""
    veo = bool(media.model_key) and "veo" in media.model_key
    return "video" if media.video_url or media.seconds or veo else "image"


async def flow_projects(profile: Profile, *, visible: bool = False,
                        browser: Callable[..., Any] = open_browser,
                        page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        projects = await page_factory(ui).projects()
    return {"projects": [{"id": p.id, "title": p.title, "created": _iso(p.created)}
                         for p in projects], "count": len(projects)}


async def flow_media(profile: Profile, *, project: str | None = None, kind: str | None = None,
                     visible: bool = False, browser: Callable[..., Any] = open_browser,
                     page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    project_id = _project_id(project) if project else None
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
    items = [{"media_id": m.media_id, "title": contents.titles.get(m.media_id),
              "prompt": m.prompt,
              "kind": _media_kind(m),
              "status": {None: "ready", 1: "running", 2: "running", 3: "done"}.get(
                  m.status, f"status:{m.status}"),
              "model": m.model_key, "seconds": m.seconds, "created": _iso(m.created)}
             for m in contents.media]
    if kind:
        items = [item for item in items if item["kind"] == kind]
    return {"media": items, "count": len(items), "project": project_id}


async def flow_download(profile: Profile, media_id: str, *, project: str | None = None,
                        out: Path | None = None, overwrite: bool = False, visible: bool = False,
                        browser: Callable[..., Any] = open_browser,
                        downloader: Callable[[str], Any] = download_signed,
                        now: Callable[[], datetime] = datetime.now,
                        page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    project_id = _project_id(project) if project else None
    _check_output(out, 1, overwrite)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
        listed = next((m for m in contents.media if m.media_id == media_id), None)
        if listed is None:
            raise NotFoundError(f"no media {media_id} in this Flow project",
                                hint='see "aistudio flow media"')
        found = await page.media_details(media_id) or listed  # the list has no links
    kind = _media_kind(listed)
    if not (found.video_url if kind == "video" else found.image_url):
        raise ValidationError(f"media {media_id} has no download yet (still running or failed)",
                              hint="check it in Flow, then retry")
    files, warnings = await _save_all(kind, [found], out=out, overwrite=overwrite,
                                      downloader=downloader, stamp=now(),
                                      project_url=PROJECT_URL.format(id=project_id))
    return {"files": files, "project": project_id, **({"warnings": warnings} if warnings else {})}


def _cost_warning(before: int | None, after: int | None, price: int) -> tuple[int, list[str]]:
    if before is None or after is None:
        return price, [f"could not read Flow credits around the run; the charge (table price "
                       f"{price}) was not verified"]
    spent = before - after
    note = ([f"Flow charged {spent} credits; aistudio's table says {price}"]
            if spent != price else [])
    return spent, note


async def _credits_after(page: Any, fallback: int | None) -> tuple[int | None, list[str]]:
    """Credits after a spend; never raises, so a paid result is never turned into "re-run"."""
    try:
        return await page.credits(), []
    except Exception:  # noqa: BLE001 - the run already spent; report, don't fail
        source = f"Flow's own reply ({fallback})" if fallback is not None else "nothing"
        return fallback, [f"could not read Flow credits after the run; the charge was not "
                          f"verified (using {source})"]


async def flow_extend(profile: Profile, media_id: str, prompt: str, *,
                      max_credits: int | None = None, project: str | None = None,
                      out: Path | None = None, overwrite: bool = False, timeout_s: float = 600.0,
                      visible: bool = False, browser: Callable[..., Any] = open_browser,
                      downloader: Callable[[str], Any] = download_signed,
                      now: Callable[[], datetime] = datetime.now,
                      page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """Extend a Veo clip by one clip (direct spend: priced from aistudio's table)."""
    prompt = " ".join(prompt.split())
    if not prompt:
        raise ValidationError("nothing to extend with: the prompt is empty")
    project_id = _project_id(project) if project else None
    _check_output(out, 1, overwrite)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        before = await page.credits()
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
        source = next((m for m in contents.media if m.media_id == media_id), None)
        workflow_id = contents.workflows.get(media_id)
        if source is None or workflow_id is None:
            raise NotFoundError(f"no media {media_id} in this Flow project",
                                hint='see "aistudio flow media"')
        if _media_kind(source) != "video" or source.status != 3:
            raise ValidationError(f"media {media_id} is not a finished video")
        label = await page.extend_label(project_id, workflow_id)  # opens that exact clip
        price = check_direct_spend("extend", label, max_credits)  # refuses before any spend
        try:
            outcome = await page.extend(prompt, timeout_s=timeout_s)
        except Exception as exc:  # the click spent credits; never invite a re-run
            raise _spent(exc, page.paid_media, project_id) from exc
        after, read_notes = await _credits_after(page, outcome.credits_left)
    spent, warnings = _cost_warning(before, after, price)
    warnings = read_notes + warnings
    project_url = PROJECT_URL.format(id=project_id)
    if outcome.timed_out:
        return {"status": "timeout",
                "media_ids": page.paid_media or [m.media_id for m in outcome.media],
                "project_url": project_url, "credits_spent": spent,
                **({"warnings": warnings} if warnings else {})}
    files, saved_notes = await _save_all("video", outcome.media, out=out, overwrite=overwrite,
                                         downloader=downloader, stamp=now(),
                                         project_url=project_url)
    warnings += saved_notes
    return {"status": "done", "files": files, "credits_spent": spent, "credits_left": after,
            "model": label_to_id(label), "scene_id": page.last_scene_id, "source": media_id,
            "project": project_id,
            **({"warnings": warnings} if warnings else {})}


async def flow_character(profile: Profile, description: str, *, max_credits: int | None = None,
                         project: str | None = None, out: Path | None = None,
                         overwrite: bool = False, visible: bool = False,
                         browser: Callable[..., Any] = open_browser,
                         downloader: Callable[[str], Any] = download_signed,
                         now: Callable[[], datetime] = datetime.now,
                         page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """Create a character from a description (direct spend: priced from aistudio's table)."""
    description = " ".join(description.split())
    if not description:
        raise ValidationError("nothing to create: the character description is empty")
    project_id = _project_id(project) if project else None
    _check_output(out, 1, overwrite)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        before = await page.credits()
        project_id = await _open(page, profile, project_id)
        model = await page.open_character(project_id)
        price = check_direct_spend("character", model, max_credits)  # refuses before any spend
        try:
            outcome = await page.start_character(description)
        except Exception as exc:
            raise _spent(exc, page.paid_media, project_id) from exc
        after, read_notes = await _credits_after(page, None)
    spent, warnings = _cost_warning(before, after, price)
    warnings = read_notes + warnings
    files, saved_notes = await _save_all("image", outcome.media, out=out, overwrite=overwrite,
                                         downloader=downloader, stamp=now(),
                                         project_url=PROJECT_URL.format(id=project_id))
    warnings += saved_notes
    return {"status": "done", "character_id": outcome.character_id, "files": files,
            "credits_spent": spent, "credits_left": after, "model": model,
            "project": project_id, **({"warnings": warnings} if warnings else {})}


def _project_id(value: str) -> str:
    match = _PROJECT.search(value.strip().split("#", 1)[0].split("?", 1)[0])
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
                    project_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    ids = ", ".join(m.media_id for m in media)
    folder = out if out is not None and out.is_dir() else Path(".")
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        for i, item in enumerate(media):
            url = item.video_url if kind == "video" else item.image_url
            if not url:
                raise SchemaDriftError("as29s", (), f"a {kind} link")
            data = await downloader(url)
            mime, ext = _type_of(kind, data)
            if out is not None and not out.is_dir() and len(media) == 1:
                path = out
                if _normal_suffix(out.suffix) != ext:
                    warnings.append(f"{out} holds {mime} data; its extension says otherwise")
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
    return files, warnings


def _type_of(kind: str, data: bytes) -> tuple[str, str]:
    """The real type from the bytes (Flow returns JPEG images even for a .png name)."""
    if data.startswith(b"\x89PNG"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg", ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data[4:8] == b"ftyp" or kind == "video":
        return "video/mp4", ".mp4"
    return "image/jpeg", ".jpg"


def _normal_suffix(suffix: str) -> str:
    lowered = suffix.lower()
    return ".jpg" if lowered == ".jpeg" else lowered
