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
    FREE_WITHOUT_ASKING,
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
from aistudio._flow.rows import FlowCharacter, ProjectContents, parse_plan
from aistudio._fs import read_json, write_private_json
from aistudio._media.save import check_folder, free_name, write_file
from aistudio.exceptions import (
    AgentReplyError,
    AIStudioError,
    BrowserError,
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
    characters: list[str] | None = None,
    on_start: Callable[[], None] | None = None,
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
    if on_start:
        on_start()
    free: tuple[list[Any], list[str]] | None = None
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        before = await page.credits()  # free models generate without asking (plan F)
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id) if attachments or characters else None
        titles, known_ids = _attachment_titles(contents, attachments)
        chosen = _characters(contents, characters or []) if contents is not None else []
        previous = await page.read_defaults(kind)
        if not previous.asks:
            raise ValidationError(
                "Flow's agent is set to generate without asking first",
                hint="in Flow's settings set 'Confirm before generating' to Always; with Never, "
                     "any prompt can spend credits. Nothing was sent")
        wanted = Defaults(flow_model.label if flow_model else previous.model,
                          aspect or previous.aspect, count or previous.count)
        _check_output(out, wanted.count, overwrite)  # the count may come from Flow's defaults
        if kind == "image" and wanted.model not in FREE_WITHOUT_ASKING:
            raise ValidationError(
                f"aistudio has not measured whether Flow asks before generating with "
                f"{wanted.model}; it could charge with no quote",
                hint="nothing was sent; use -m nano-banana-2.1 (free), or set it as Flow's image "
                     "default")
        if duration is not None and flow_model is None:  # check against the default model
            check_options(kind, model_by_label(previous.model), None, None, duration)
        uploads: list[dict[str, str]] = []
        attached_ids: list[str] = []  # in attach order: frames are named "in order"
        try:
            if wanted != previous:
                await page.apply_defaults(kind, wanted)
            for _, value in attachments:
                if isinstance(value, Path):  # --i-have-rights was checked before Chrome opened
                    media_id = await page.upload(value, known_ids=known_ids)
                    known_ids.add(media_id)  # its late as29s record is not the next upload
                    uploads.append({"file": value.name, "media_id": media_id})
                    attached_ids.append(media_id)
                else:
                    attached_ids.append(value)
            if uploads:  # the + panel stays over the grid after an upload (live): reload
                await page.open_project(project_id)
            await page.new_session()
            for title in titles:
                await page.attach(title)
            for chosen_character in chosen:
                await page.attach_character(chosen_character.name)
            if attachments:
                roles = ", ".join(role for role, _ in attachments)
                prompt = f"Use the attached images, in order, as: {roles}. {prompt}"
            if chosen:
                names = ", ".join(c.name for c in chosen)
                prompt = f"Feature the attached characters: {names}. {prompt}"
            turn = await page.submit_for_quote(instruction(
                kind, prompt, count=wanted.count,
                model_label=flow_model.label if flow_model else None, seconds=duration,
                aspect=aspect))
            if turn.quote is None and not turn.generated:
                raise AgentReplyError(f"Flow's agent replied without a quote: {turn.text[-500:]}",
                                      hint="rephrase the prompt or add options; nothing was "
                                           "spent")
            if turn.quote is None:  # a free model: Flow generated without asking (live)
                free = await _free_run(page, turn, kind, wanted.count, flow_model, aspect,
                                       attached_ids, [c.id for c in chosen])
                waiting = await _reject_noting(page, expect=False)  # a later ask for the rest
                if waiting:
                    free[1].append(waiting[2:])
            else:
                if list(dict.fromkeys(turn.media)) != list(dict.fromkeys(attached_ids)):
                    waiting = await _reject_noting(page)
                    raise PlanMismatchError(
                        f"Flow's request carries media {list(turn.media) or 'none'}, "
                        f"not {attached_ids or 'none'} (in that order)",
                        hint=f"nothing was spent; check the prompt box's attachments in Flow, then "
                             f"re-run{waiting}")
                wanted_ids = {c.id for c in chosen}
                if set(turn.characters) != wanted_ids:
                    waiting = await _reject_noting(page)
                    raise PlanMismatchError(
                        f"Flow's request carries characters {sorted(turn.characters) or 'none'}, "
                        f"not {sorted(wanted_ids) or 'none'}",
                        hint=f"nothing was spent; check the prompt box's attachments in Flow, then "
                             f"re-run{waiting}")
                expect = Expect(kind, wanted.count, flow_model.label if flow_model else None,
                                duration)
                decision, wrong = decide(turn.quote, parse_plan(turn.text), expect, max_credits)
                if decision is Decision.QUOTE_ONLY:
                    # Declined in Flow too: a waiting request could later be approved there with
                    # the restored settings, not the quoted ones.
                    note = await _reject_noting(page)
                    declined = not note
                    if note:
                        wrong = [*wrong, f"{note[2:]} (the settings were restored)"]
                    return {"status": "quoted", "declined": declined, "credits": turn.quote.credits,
                            "count": turn.quote.count, "plan": turn.text, "project": project_id,
                            **({"uploads": uploads} if uploads else {}),
                            **({"warnings": wrong} if wrong else {})}
                if decision is Decision.REJECT_MISMATCH:
                    waiting = await _reject_noting(page)
                    raise PlanMismatchError("; ".join(wrong), hint=f"nothing was spent; adjust "
                                                                   f"the flags or the prompt"
                                                                   f"{waiting}")
                if decision is Decision.REJECT_OVER_BUDGET:
                    waiting = await _reject_noting(page)
                    raise OverBudgetError(f"Flow quotes {turn.quote.credits} credits, over "
                                          f"--max-credits {max_credits}",
                                          hint=f"nothing was spent; re-run with --max-credits "
                                               f"{turn.quote.credits} to accept{waiting}")
                try:
                    outcome = await page.approve_and_follow(count=wanted.count,
                                                            credits=turn.quote.credits,
                                                            timeout_s=timeout_s, kind=kind)
                except Exception as exc:
                    if not page.started:  # refused before Approve: decline the waiting request
                        waiting = await _reject_noting(page)
                        if waiting and isinstance(exc, AIStudioError):
                            exc.hint = f"{exc.hint or ''}{waiting}"
                        raise
                    # Approve may have spent: name the media, never invite a re-run
                    raise _spent(exc, page.paid_media, project_id) from exc
        finally:
            if wanted != previous:
                try:
                    await page.apply_defaults(kind, previous)
                except Exception:  # noqa: BLE001 - restoring must never hide the run's outcome
                    warnings.append(f"could not restore Flow's {kind} defaults "
                                    f"({previous.model}, {previous.aspect}, x{previous.count}); "
                                    "set them in Flow's settings")
        if free is not None:  # after the restore: reading credits leaves the project page
            after, free_notes = await _credits_after(page, None)
    project_url = PROJECT_URL.format(id=project_id)
    if free is not None:
        media, notes = free
        notes = free_notes + notes
        spent, charge_notes = _free_charge(before, after)
        files, saved_notes = await _save_all(kind, media, out=out, overwrite=overwrite,
                                             downloader=downloader, stamp=now(),
                                             project_url=project_url)
        warnings = notes + charge_notes + warnings + saved_notes
        named = next((g.model for g in turn.generated if g.model), None)
        model_id = label_to_id(named or wanted.model)  # the settings' model made it otherwise
        return {"status": "done", "files": files, "credits_spent": spent, "credits_left": after,
                "model": model_id, "project": project_id,
                **({"warnings": warnings} if warnings else {})}
    if (before is not None and outcome.credits_left is not None
            and before - outcome.credits_left != turn.quote.credits):
        warnings.append(f"Flow charged {before - outcome.credits_left} credits; the quote was "
                        f"{turn.quote.credits}")
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


async def _free_run(page: Any, turn: Any, kind: str, count: int, flow_model: Any,
                    aspect: str | None, attached_ids: list[str], character_ids: list[str]
                    ) -> tuple[list[Any], list[str]]:
    """What Flow made without asking: the media with links, and notes. Credits are read later,
    after the settings are restored (reading them leaves the project page)."""
    notes = ["Flow generated without asking (free model), so there was no quote to check"]
    made = [g for g in turn.generated if g.status == "success" and g.kind == kind]
    if not made:
        reported = ", ".join(f"{g.media_id} ({g.kind}, {g.status or 'unknown'})"
                             for g in turn.generated)
        raise AgentReplyError(f"Flow made no {kind}; it reported {reported}",
                              hint="check the project in Flow before re-running")
    notes += [f"Flow also made {g.media_id}, a {g.kind}, which was not downloaded"
              for g in turn.generated if g.status == "success" and g.kind != kind]
    notes += [f"Flow reported {g.media_id} as {g.status or 'unknown'}, not made"
              for g in turn.generated if g.status != "success"]
    if len(made) != count:
        notes.append(f"Flow made {len(made)} {kind}(s), not {count}")
    if flow_model is not None:
        notes += [f"Flow used {g.model}, not {flow_model.label}"
                  for g in made if g.model and g.model != flow_model.label]
    if aspect is not None:
        notes += [f"Flow made {g.aspect}, not {aspect}"
                  for g in made if g.aspect and g.aspect != aspect]
    if list(dict.fromkeys(turn.media)) != list(dict.fromkeys(attached_ids)):
        notes.append(f"Flow's request carried media {list(turn.media) or 'none'}, not "
                     f"{attached_ids or 'none'}")
    if set(turn.characters) != set(character_ids):
        notes.append(f"Flow's request carried characters {sorted(turn.characters) or 'none'}, "
                     f"not {sorted(character_ids) or 'none'}")
    media = []
    for generated in made:
        try:
            found = await page.media_details(generated.media_id)
        except Exception:  # noqa: BLE001 - it exists in Flow; name it, never fail the run
            found = None
        if found is None or not (found.video_url if kind == "video" else found.image_url):
            notes.append(f"no link yet for {generated.media_id}; download it with: "
                         f"aistudio flow download {generated.media_id}")
        else:
            media.append(found)
    return media, notes


def _free_charge(before: int | None, after: int | None) -> tuple[int | None, list[str]]:
    if before is None or after is None:
        return None, ["could not read Flow credits around the run; the charge was not verified"]
    spent = before - after
    return spent, ([f"Flow charged {spent} credits without asking"] if spent else [])


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


def _attachment_titles(contents: ProjectContents | None,
                       attachments: list[tuple[str, Path | str]]) -> tuple[list[str], set[str]]:
    """The grid titles to attach by, checked before anything is uploaded or sent: attach works
    by title, so each title must name exactly one media in the project."""
    if not attachments or contents is None:
        return [], set()
    hidden = {c.media_id for c in contents.characters}  # characters' images: copies, no tile
    by_title: dict[str, list[str]] = {}
    for media_id, title in contents.titles.items():
        if media_id not in hidden:
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
        owner = next((c for c in contents.characters if c.media_id == value), None)
        if owner is not None:
            raise ValidationError(f"{value} is the image of character {owner.name or owner.id!r}",
                                  hint=f'use --character "{owner.name or owner.id}" instead; '
                                       f'nothing was sent')
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


_STILL_WAITING = "Flow's request may still be waiting: press Reject on it in Flow, never Approve"


async def _reject_noting(page: Any, *, expect: bool = True) -> str:
    """Reject Flow's waiting request(s). The note is empty only when one was pressed, or none
    was expected; otherwise the person must check Flow's chat."""
    try:
        pressed = await page.reject(expect=expect)
    except BrowserError:
        return f"; {_STILL_WAITING}"
    return f"; {_STILL_WAITING}" if expect and not pressed else ""


_NAME_MAX = 100
UNTITLED = "Untitled character"  # Flow's name for a character made from a description
_FINISHED = 3
_CLIP = re.compile(r"^(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
                   r"(?:@(?P<start>\d+)-(?P<end>\d+))?$")


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > _NAME_MAX:
        raise ValidationError(f"a character name needs 1 to {_NAME_MAX} characters")
    return name


def _key(name: str) -> str:
    """How names are compared: spaces collapsed (as Flow's panel matches them), any case."""
    return " ".join(name.split()).casefold()


def _free_name(contents: ProjectContents, name: str, *, keep: str | None = None) -> None:
    """Names are unique per project (case-insensitively): Flow attaches characters by name."""
    taken = [c for c in contents.characters if _key(c.name) == _key(name) and c.id != keep]
    if taken:
        raise ValidationError(f"character {taken[0].id} is already named {taken[0].name!r}",
                              hint="choose another name; --character picks characters by name")


def _characters(contents: ProjectContents, wanted: list[str]) -> list[FlowCharacter]:
    """Characters by id or name, checked before anything is sent: each must have an image and
    a name no other character shares (the + panel lists them by name only)."""
    out = []
    for value in wanted:
        value = " ".join(value.split())
        found = ([c for c in contents.characters if c.id == value]
                 or [c for c in contents.characters if _key(c.name) == _key(value)])
        if not found:
            raise NotFoundError(f"no character {value!r} in this Flow project",
                                hint='see "aistudio flow character list"; nothing was sent')
        same = [c for c in contents.characters if _key(c.name) == _key(found[0].name)]
        if len(same) > 1:
            raise ValidationError(
                f"{len(same)} characters are named {found[0].name!r}",
                hint=f'Flow attaches characters by name: rename one with "aistudio flow '
                     f'character rename {same[0].id} NEW-NAME"; nothing was sent')
        if not found[0].name.strip():  # Flow's panel picks characters by name
            raise ValidationError(f"character {found[0].id} has no name",
                                  hint=f'name it first: aistudio flow character rename '
                                       f'{found[0].id} NAME; nothing was sent')
        if found[0].media_id is None:
            raise ValidationError(f"character {found[0].name!r} has no image yet",
                                  hint="finish it in Flow first; nothing was sent")
        if found[0] not in out:
            out.append(found[0])
    return out


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
        except Exception as exc:
            if not page.started:  # a check before Start: its own "nothing was spent" stands
                raise
            raise _spent(exc, page.paid_media, project_id) from exc  # Start may have spent
        after, read_notes = await _credits_after(page, outcome.credits_left)
        new_ids = page.paid_media or [m.media_id for m in outcome.media]
        scene_id = await _scene_holding(page, project_id, new_ids) or page.last_scene_id
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
            "model": label_to_id(label), "scene_id": scene_id, "source": media_id,
            "project": project_id,
            **({"warnings": warnings} if warnings else {})}


async def flow_character(profile: Profile, name: str, description: str | None = None, *,
                         photo: str | None = None, i_have_rights: bool = False,
                         personality: str | None = None, model: str | None = None,
                         max_credits: int | None = None, project: str | None = None,
                         out: Path | None = None, overwrite: bool = False, visible: bool = False,
                         browser: Callable[..., Any] = open_browser,
                         downloader: Callable[[str], Any] = download_signed,
                         now: Callable[[], datetime] = datetime.now,
                         page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """Create a named character from a description (direct spend, priced from aistudio's table)
    or from a photo (free: Flow links the photo, nothing is generated)."""
    name = _name(name)
    description = " ".join((description or "").split()) or None
    if (description is None) == (photo is None):
        raise ValidationError("give the character a DESCRIPTION or a --photo (exactly one)")
    chosen_model = find_model(model, "image") if model else None
    if chosen_model is not None and photo is not None:
        raise ValidationError("-m applies to a character made from a DESCRIPTION; a --photo "
                              "makes no image")
    source = (_attachments(None, None, [photo], i_have_rights)[0][1]
              if photo is not None else None)
    project_id = _project_id(project) if project else None
    _check_output(out, 1, overwrite)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        before = await page.credits()
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
        _free_name(contents, name)
        model, notes = None, []
        if source is not None:
            price = 0  # measured: linking a photo generates nothing
            media_id = await _photo_id(page, contents, source)
            cid, stored = await page.create_character_record(project_id, name)
            if stored != name:
                notes.append(f"Flow stored the name {stored!r}, not {name!r}")
                name = stored
            try:
                image_id = await page.link_photo(project_id, cid, media_id)
            except Exception as exc:  # the named character exists now
                raise _character_made(exc, cid, name) from exc
            try:
                image = await page.media_details(image_id)
            except Exception:  # noqa: BLE001 - the character is complete; only the link is missing
                image = None
            media = [image] if image is not None else []
            if image is None:
                notes.append(f"Flow gave no link for the character's image {image_id}; "
                             f"download it with: aistudio flow download {image_id}")
        else:
            model = await page.open_character(project_id)
            if chosen_model is not None and model != chosen_model.label:
                await page.choose_character_model(chosen_model.label)
                model = chosen_model.label
            price = check_direct_spend("character", model, max_credits)  # before any spend
            try:
                outcome = await page.start_character(description or "")
            except Exception as exc:
                if not page.started:  # a check before Start: nothing was made
                    raise
                made = page.paid_media[0] if page.paid_media else None  # Start may have made it
                raise _character_made(exc, made, name) from exc
            cid, media = outcome.character_id, outcome.media
            image_id = media[0].media_id if media else None
            if cid is None:
                notes.append(f'Flow did not report the new character\'s id, so it is still '
                             f'"Untitled character"; find it with "aistudio flow character list" '
                             f'and rename it to "{name}"')
                name = UNTITLED
            else:
                try:
                    await page.rename_character(project_id, cid, name)
                except Exception:  # noqa: BLE001 - the character exists; never invite a re-run
                    notes.append(f'Flow kept the name "Untitled character"; rename it with: '
                                 f'aistudio flow character rename {cid} "{name}"')
                    name = UNTITLED
        personality = personality.strip() if personality is not None else None
        if personality and cid is not None:
            try:
                await page.set_personality(project_id, cid, personality)
            except Exception:  # noqa: BLE001 - the character exists; never invite a re-run
                notes.append(f'Flow did not save the personality; set it with: aistudio flow '
                             f'character personality {cid} "{personality}"')
                personality = None
        after, read_notes = await _credits_after(page, None)
    spent, warnings = _cost_warning(before, after, price)
    warnings = read_notes + warnings + notes
    try:
        files, saved_notes = await _save_all("image", media, out=out, overwrite=overwrite,
                                             downloader=downloader, stamp=now(),
                                             project_url=PROJECT_URL.format(id=project_id))
    except Exception as exc:
        raise _character_made(exc, cid, name) from exc
    warnings += saved_notes
    return {"status": "done", "character_id": cid, "name": name, "media_id": image_id,
            **({"personality": personality} if personality else {}), "files": files,
            "credits_spent": spent, "credits_left": after,
            **({"model": label_to_id(model)} if model else {}), "project": project_id,
            **({"warnings": warnings} if warnings else {})}


async def _photo_id(page: Any, contents: ProjectContents, source: Path | str) -> str:
    """The project image to link: an existing media id, or a local file uploaded now."""
    if isinstance(source, Path):  # --i-have-rights was checked before Chrome opened
        _attachment_titles(contents, [("photo", source)])  # refuses a file already uploaded
        known = set(contents.titles) | {m.media_id for m in contents.media}
        return await page.upload(source, known_ids=known)
    found = next((m for m in contents.media if m.media_id == source), None)
    if found is None:
        raise NotFoundError(f"no media {source} in this Flow project",
                            hint='see "aistudio flow media --type image"; nothing was made')
    if _media_kind(found) != "image":
        raise ValidationError(f"media {source} is not an image", hint="nothing was made")
    if found.status not in (None, _FINISHED):  # uploads carry no status; generated images 3
        raise ValidationError(f"image {source} is not finished (still running or failed)",
                              hint="nothing was made; check it in Flow")
    return source


def _character_made(exc: Exception, cid: str | None, name: str) -> AIStudioError:
    """An error once Flow holds the new character: name it, and forbid a re-run under another
    name (that would make a duplicate)."""
    note = (f"Flow created character {cid or '(id not seen)'} ({name!r}); do not re-run, see "
            f'"aistudio flow character list"')
    if not isinstance(exc, AIStudioError):
        return BrowserError(f"{type(exc).__name__}: {exc}", hint=note)
    exc.hint = f"{note} ({exc.hint})" if exc.hint else note
    return exc


async def _project_contents(profile: Profile, project: str | None, visible: bool,
                            browser: Callable[..., Any], page_factory: Callable[[Any], Any]
                            ) -> tuple[str, ProjectContents]:
    project_id = _project_id(project) if project else None
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        return project_id, await page.contents(project_id)


async def flow_characters(profile: Profile, *, project: str | None = None, visible: bool = False,
                          browser: Callable[..., Any] = open_browser,
                          page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    project_id, contents = await _project_contents(profile, project, visible, browser,
                                                   page_factory)
    items = [{"id": c.id, "name": c.name, "media_id": c.media_id, "created": _iso(c.created)}
             for c in contents.characters]
    return {"characters": items, "count": len(items), "project": project_id}


async def flow_rename_character(profile: Profile, character_id: str, name: str, *,
                                project: str | None = None, visible: bool = False,
                                browser: Callable[..., Any] = open_browser,
                                page_factory: Callable[[Any], Any] = FlowPage
                                ) -> dict[str, Any]:
    name = _name(name)
    project_id = _project_id(project) if project else None
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
        if not any(c.id == character_id for c in contents.characters):
            raise NotFoundError(f"no character {character_id} in this Flow project",
                                hint='see "aistudio flow character list"')
        _free_name(contents, name, keep=character_id)
        await page.rename_character(project_id, character_id, name)
    return {"character_id": character_id, "name": name, "project": project_id}


async def flow_set_personality(profile: Profile, character_id: str, text: str, *,
                               project: str | None = None, visible: bool = False,
                               browser: Callable[..., Any] = open_browser,
                               page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """Set a character's personality notes (free). An empty text clears them."""
    text = text.strip()
    project_id = _project_id(project) if project else None
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
        if not any(c.id == character_id for c in contents.characters):
            raise NotFoundError(f"no character {character_id} in this Flow project",
                                hint='see "aistudio flow character list"')
        await page.set_personality(project_id, character_id, text)
    return {"character_id": character_id, "personality": text, "project": project_id}


async def flow_scenes(profile: Profile, *, project: str | None = None, visible: bool = False,
                      browser: Callable[..., Any] = open_browser,
                      page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    project_id, contents = await _project_contents(profile, project, visible, browser,
                                                   page_factory)
    items = [{"id": scene.id, "title": scene.title, "created": _iso(scene.created),
              "clips": [{"media_id": c.media_id, "title": c.title, "seconds": c.end - c.start}
                        for c in scene.clips]}
             for scene in contents.scenes]
    return {"scenes": items, "count": len(items), "project": project_id}


def _parse_clips(values: list[str]) -> list[tuple[str, int | None, int | None]]:
    if len(values) < 2:
        raise ValidationError("stitching needs at least two clips")
    out = []
    for value in values:
        match = _CLIP.match(value.strip())
        if match is None:
            raise ValidationError(f"{value!r} is not MEDIA-ID or MEDIA-ID@START-END",
                                  hint="START and END are whole seconds, e.g. <id>@2-5")
        out.append((match["id"], int(match["start"]) if match["start"] else None,
                    int(match["end"]) if match["end"] else None))
    return out


def _clip_args(contents: ProjectContents, wanted: list[tuple[str, int | None, int | None]]
               ) -> list[list[Any]]:
    """``[media_id, None, [start], [end]]`` per clip (``[]`` is 0), checked against the project."""
    by_id = {m.media_id: m for m in contents.media}
    out = []
    for media_id, start, end in wanted:
        media = by_id.get(media_id)
        if media is None:
            raise NotFoundError(f"no media {media_id} in this Flow project",
                                hint='see "aistudio flow media --type video"; nothing was sent')
        if _media_kind(media) != "video" or media.status != 3 or not media.seconds:
            raise ValidationError(f"media {media_id} is not a finished video",
                                  hint="nothing was sent")
        start, end = start or 0, media.seconds if end is None else end
        if not 0 <= start < end <= media.seconds:
            raise ValidationError(f"{media_id}@{start}-{end}: a trim must be within "
                                  f"0-{media.seconds} seconds, with START before END",
                                  hint="nothing was sent")
        out.append([media_id, None, [start] if start else [], [end]])
    return out


async def flow_stitch(profile: Profile, clips: list[str], *, project: str | None = None,
                      out: Path | None = None, overwrite: bool = False, timeout_s: float = 300.0,
                      visible: bool = False, browser: Callable[..., Any] = open_browser,
                      now: Callable[[], datetime] = datetime.now,
                      page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """Join finished clips, trimmed or whole, into one MP4 (Flow's "Download scene"; free)."""
    wanted = _parse_clips(clips)
    project_id = _project_id(project) if project else None
    _check_output(out, 1, overwrite)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        args = _clip_args(await page.contents(project_id), wanted)
        data = await page.stitch(args, timeout_s=timeout_s)
    return _stitched(data, args, out=out, overwrite=overwrite, stamp=now(), project_id=project_id)


async def flow_scene_download(profile: Profile, scene_id: str, *, project: str | None = None,
                              out: Path | None = None, overwrite: bool = False,
                              timeout_s: float = 300.0, visible: bool = False,
                              browser: Callable[..., Any] = open_browser,
                              now: Callable[[], datetime] = datetime.now,
                              page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """A scene's clips, in order and with its own trims, as one MP4 (free)."""
    project_id = _project_id(project) if project else None
    _check_output(out, 1, overwrite)
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        contents = await page.contents(project_id)
        scene = next((s for s in contents.scenes if s.id == scene_id), None)
        if scene is None:
            raise NotFoundError(f"no scene {scene_id} in this Flow project",
                                hint='see "aistudio flow scene list"')
        if not scene.clips:
            raise ValidationError(f"scene {scene_id} has no clips")
        args = _clip_args(contents, [(c.media_id, c.start, c.end or None) for c in scene.clips])
        data = await page.stitch(args, timeout_s=timeout_s)
    return {**_stitched(data, args, out=out, overwrite=overwrite, stamp=now(),
                        project_id=project_id), "scene_id": scene_id}


def _stitched(data: bytes, args: list[list[Any]], *, out: Path | None, overwrite: bool,
              stamp: datetime, project_id: str) -> dict[str, Any]:
    mime, ext = _type_of("video", data)
    path = out if out is not None else free_name(Path(f"flow-scene-{stamp:%Y%m%d-%H%M%S}{ext}"))
    warnings = ([f"{out} holds {mime} data; its extension says otherwise"]
                if out is not None and _normal_suffix(out.suffix) != ext else [])
    saved = write_file(path, data, overwrite=overwrite and path == out)
    return {"status": "done",
            "files": [{"path": str(saved), "mime_type": mime, "bytes": len(data)}],
            "clips": [{"media_id": a[0], "start": (a[2] or [0])[0], "end": a[3][0]}
                      for a in args],
            "project": project_id, **({"warnings": warnings} if warnings else {})}


async def _scene_holding(page: Any, project_id: str, media_ids: list[str]) -> str | None:
    """The scene an extension landed in, from the project itself (never raises: paid run)."""
    try:
        contents = await page.contents(project_id)
    except Exception:  # noqa: BLE001 - only a reported field; the result stands
        return None
    return next((scene.id for scene in contents.scenes
                 if any(c.media_id in media_ids for c in scene.clips)), None)


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
                if ext == ".bin":
                    warnings.append(f"the type of {item.media_id} was not recognised; it was "
                                    f"saved as {path.name}")
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
    if kind == "image" and data[4:8] == b"ftyp":
        size = int.from_bytes(data[:4], "big")
        brands = [data[8:12]] + [data[i:i + 4] for i in range(16, min(size, 64), 4)]
        if any(b in (b"avif", b"avis") for b in brands):  # AVIF may name mif1 first
            return "image/avif", ".avif"
        if any(b in (b"heic", b"heix", b"mif1") for b in brands):
            return "image/heic", ".heic"
    if kind == "video":
        return "video/mp4", ".mp4"
    return "application/octet-stream", ".bin"  # unknown image bytes: never call them JPEG


def _normal_suffix(suffix: str) -> str:
    lowered = suffix.lower()
    return ".jpg" if lowered == ".jpeg" else lowered
