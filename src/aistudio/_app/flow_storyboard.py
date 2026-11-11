"""``aistudio flow storyboard``: a whole Flow scene from one YAML file (use case).

The run is a pipeline of the existing flow use cases, injected as ``StoryboardOps`` so tests
replace them and so no wire logic lives here:

1. check the file and the state file (pure, before any browser);
2. check that the named characters and media exist in the project (free, read-only);
3. quote every pending clip: one Flow quote per model, length and aspect, and the measured
   price table for extensions;
4. refuse when the quotes exceed ``--max-credits``;
5. free assets (new characters, location plates, keyframes), only with ``--keyframes`` or
   ``--max-credits``;
6. with ``--max-credits``: the clips in order, each capped at its own quote, then the join.

Every step is recorded in a state file. A finished shot is never paid for twice, an error
that may have charged blocks its shot until the user says what happened, and nothing paid is
ever retried automatically.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.flow import (
    _check_output,
    flow_character,
    flow_characters,
    flow_download,
    flow_extend,
    flow_frame,
    flow_generate,
    flow_media,
    flow_stitch,
)
from aistudio._browser.ui import open_browser
from aistudio._flow.download import download_signed
from aistudio._flow.page import PROJECT_URL, FlowPage
from aistudio._flow.storyboard import (
    DEFAULT_EXTEND_DURATION,
    EXTEND_CREDITS_PER_S,
    CharacterSpec,
    KeyframeSpec,
    LocationSpec,
    ShotSpec,
    Storyboard,
    fingerprint,
    load_storyboard,
    name_key,
    quote_groups,
)
from aistudio._fs import read_json, write_private_json
from aistudio._media.save import free_name
from aistudio.exceptions import (
    AgentReplyError,
    AIStudioError,
    BrowserError,
    GenerationFailedError,
    NotFoundError,
    OverBudgetError,
    PlanMismatchError,
    RateLimitError,
    ValidationError,
    WaitTimeoutError,
)
from aistudio.paths import Profile

STATE_VERSION = 1
KEYFRAME_MODEL = "nano-banana-2.1"  # the only image model measured as free and unquoted
QUOTE_BASIS = ("Flow's quote per model, length and aspect; extensions from aistudio's "
               "per-second Extend price")
# Errors that happen before Flow is asked to spend: a re-run is the user's call, nothing is
# blocked. Anything else, or any error whose hint says credits may have gone, blocks the shot.
# GenerationFailedError is a job Flow started and failed: failed jobs were refunded (live).
_NOTHING_SPENT = (ValidationError, OverBudgetError, PlanMismatchError, RateLimitError,
                  NotFoundError, AgentReplyError, GenerationFailedError)
_MAY_HAVE_SPENT_HINTS = ("may have been spent", "do not re-run", "Flow made media")
_Op = Callable[..., Awaitable[dict[str, Any]]]


@dataclasses.dataclass
class StoryboardOps:
    characters: _Op = flow_characters
    media: _Op = flow_media
    character: _Op = flow_character
    generate: _Op = flow_generate
    extend: _Op = flow_extend
    frame: _Op = flow_frame
    stitch: _Op = flow_stitch
    download: _Op = flow_download


async def flow_storyboard(
    profile: Profile, path: Path, *, max_credits: int | None = None, keyframes: bool = False,
    out: Path | None = None, overwrite: bool = False, state_path: Path | None = None,
    assets_dir: Path | None = None, adopt: dict[str, str] | None = None,
    timeout_s: float = 1200.0, visible: bool = False, ops: StoryboardOps | None = None,
    browser: Callable[..., Any] = open_browser,
    downloader: Callable[[str], Any] = download_signed,
    now: Callable[[], datetime] = datetime.now,
    page_factory: Callable[[Any], Any] = FlowPage,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if max_credits is not None and max_credits < 0:
        raise ValidationError("--max-credits must be 0 or more")
    if max_credits is not None and out is None:
        raise ValidationError("--max-credits needs -o: the finished scene has to be saved",
                              hint="nothing was run")
    sb = load_storyboard(path)
    run = _Run(profile, sb, path, max_credits=max_credits, keyframes=keyframes, out=out,
               overwrite=overwrite, state_path=state_path, assets_dir=assets_dir,
               adopt=adopt or {}, timeout_s=timeout_s, visible=visible,
               ops=ops or StoryboardOps(), browser=browser, downloader=downloader, now=now,
               page_factory=page_factory, on_progress=on_progress)
    return await run.run()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x"


def _may_have_spent(exc: BaseException) -> bool:
    if not isinstance(exc, _NOTHING_SPENT):
        return True
    hint = getattr(exc, "hint", None) or ""
    return any(marker in hint for marker in _MAY_HAVE_SPENT_HINTS)


class _Run:
    def __init__(self, profile: Profile, sb: Storyboard, path: Path, *,
                 max_credits: int | None, keyframes: bool, out: Path | None, overwrite: bool,
                 state_path: Path | None, assets_dir: Path | None, adopt: dict[str, str],
                 timeout_s: float, visible: bool, ops: StoryboardOps,
                 browser: Callable[..., Any], downloader: Callable[[str], Any],
                 now: Callable[[], datetime], page_factory: Callable[[Any], Any],
                 on_progress: Callable[[str], None] | None) -> None:
        self.profile, self.sb, self.max_credits, self.out = profile, sb, max_credits, out
        self.keyframes, self.overwrite, self.adopt = keyframes, overwrite, adopt
        self.timeout_s, self.ops, self.progress = timeout_s, ops, on_progress
        self.state_path = state_path or path.with_suffix(".state.json")
        self.assets = assets_dir or path.with_name(path.stem + ".assets")
        self.path = path
        self.base: dict[str, Any] = {"visible": visible, "browser": browser,
                                     "page_factory": page_factory}
        self.dl: dict[str, Any] = {"downloader": downloader, "now": now}
        self.now = now
        self.paid = max_credits is not None
        self.warnings: list[str] = list(sb.warnings)
        self.run_spent = 0
        self.credits_left: int | None = None
        self.price: dict[str, int] = {}
        self.basis: dict[str, str] = {}
        self.state: dict[str, Any] = {}
        self.project: str | None = sb.project
        self.finals: list[dict[str, Any]] = []

    # ----- plumbing --------------------------------------------------------------------------

    def say(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def save(self) -> None:
        write_private_json(self.state_path, self.state)

    def learn(self, result: dict[str, Any]) -> None:
        """Pin the project the first call used, so a later run cannot drift to another one."""
        project = result.get("project")
        if project and self.project is None:
            self.project = project
        if self.project and self.state.get("project") != self.project:
            self.state["project"] = self.project
            self.save()

    def note(self, shot: str | None, result: dict[str, Any]) -> None:
        prefix = f"shot {shot}: " if shot else ""
        self.warnings += [prefix + str(w) for w in result.get("warnings", [])]
        if result.get("credits_left") is not None:
            self.credits_left = result["credits_left"]

    def spend(self, result: dict[str, Any]) -> int | None:
        spent = result.get("credits_spent")
        if spent:
            self.run_spent += spent
            self.state["credits_spent"] = self.state.get("credits_spent", 0) + spent
        return spent

    def shot_state(self, shot_id: str) -> dict[str, Any]:
        return self.state.setdefault("shots", {}).setdefault(shot_id, {})

    def status(self, shot_id: str) -> str:
        return self.state.get("shots", {}).get(shot_id, {}).get("status", "pending")

    def shot_fp(self, index: int, shot: ShotSpec) -> str:
        return fingerprint({"index": index, "shot": dataclasses.asdict(shot)})

    def pending(self) -> list[ShotSpec]:
        return [s for s in self.sb.shots if self.status(s.id) != "done"]

    def target(self, name: str) -> Path:
        self.assets.mkdir(parents=True, exist_ok=True)
        return free_name(self.assets / name)

    # ----- the run ---------------------------------------------------------------------------

    async def run(self) -> dict[str, Any]:
        self.prepare()
        await self.preflight()
        await self.quote()
        self.check_budget()
        error: BaseException | None = None
        try:
            if self.keyframes or self.paid:
                await self.make_assets()
            if self.paid:
                await self.make_clips()
            if not self.pending() and self.out is not None:
                await self.assemble(self.out)
        except Exception as exc:  # stop on the first error; the state already records it
            error = exc
        finally:
            self.save()
        return self.report(error)

    def prepare(self) -> None:
        state: Any = {}
        if self.state_path.exists():
            try:
                state = read_json(self.state_path)
            except (ValueError, OSError) as exc:
                raise ValidationError(f"cannot read {self.state_path}: {exc}",
                                      hint="fix it or delete it (finished shots would then be "
                                           "paid for again)") from exc
            if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
                raise ValidationError(f"{self.state_path} is not a state file this version "
                                      "understands",
                                      hint="delete it to start over (finished shots would be "
                                           "paid for again)")
        self.state = state or {"version": STATE_VERSION, "credits_spent": 0}
        known = self.state.get("project")
        if known and self.sb.project and known != self.sb.project:
            raise ValidationError(f"{self.state_path.name} belongs to project {known}, but the "
                                  f"storyboard names {self.sb.project}",
                                  hint="use --state for a separate state file, or delete this "
                                       "one to start over")
        self.project = self.sb.project or known
        self.check_unchanged()
        self.apply_adopt()
        self.check_output()
        if self.paid:
            self.check_blocked()

    def check_unchanged(self) -> None:
        problems = []
        ids = {s.id: (i, s) for i, s in enumerate(self.sb.shots)}
        for sid, entry in self.state.get("shots", {}).items():
            if entry.get("status") != "done":
                continue
            if sid not in ids:
                problems.append(f"shot {sid} is finished in the state file but not in the "
                                f"storyboard")
            elif entry.get("fp") != self.shot_fp(*ids[sid]):
                problems.append(f"shot {sid} changed (or moved) since it was made")
        for spec in self.sb.characters:
            made = self.state.get("characters", {}).get(spec.name)
            if made and made.get("fp") != fingerprint(spec):
                problems.append(f"character {spec.name} changed since aistudio created it; a "
                                f"created character is not created again")
        if problems:
            raise ValidationError(
                "; ".join(problems),
                hint=f"a finished shot is not paid for twice. Undo the edit, or delete the "
                     f"shot or character from {self.state_path} to make it again (that pays "
                     f"again), or use a new --state to start over")

    def apply_adopt(self) -> None:
        ids = {s.id: (i, s) for i, s in enumerate(self.sb.shots)}
        for sid, media in self.adopt.items():
            if sid not in ids:
                raise ValidationError(f"--adopt names shot {sid}, which is not in the storyboard")
            if self.status(sid) == "done":
                raise ValidationError(f"shot {sid} is already finished")
            if ids[sid][1].join == "extend":
                self.warnings.append(f"shot {sid}: adopted clip {media}; aistudio cannot "
                                     f"check that it extends the clip before")
            self.shot_state(sid).update(
                {"status": "done", "clip": media, "adopted": True, "credits_spent": None,
                 "fp": self.shot_fp(*ids[sid]), "file": None})

    def check_output(self) -> None:
        if self.out is None:
            return
        if self.final_done():
            return
        _check_output(self.out, 1, self.overwrite)

    def final_done(self) -> bool:
        final = self.state.get("final")
        clips = self.clip_refs()
        return bool(final and self.out is not None and final.get("path") == str(self.out)
                    and final.get("clips") == clips and Path(final["path"]).exists()
                    and not self.pending_without_adopt())

    def pending_without_adopt(self) -> list[ShotSpec]:
        return self.pending()

    def check_blocked(self) -> None:
        for shot in self.pending():
            entry = self.state.get("shots", {}).get(shot.id, {})
            status = entry.get("status")
            if status in ("started", "unknown", "timeout"):
                ids = ", ".join(entry.get("media_ids") or []) or "none seen"
                raise ValidationError(
                    f"shot {shot.id} ended without a known result ({status}) in an earlier "
                    f"run, and Flow may have charged for it (media: {ids})",
                    hint=f'find its clip with "aistudio flow media --type video" and pass '
                         f'--adopt {shot.id}=<media id>; or delete the shot from '
                         f'{self.state_path} to pay for it again. Nothing was run')

    # ----- free checks -----------------------------------------------------------------------

    def referenced(self) -> tuple[set[str], list[str]]:
        """Character names and media ids the pending shots (and their keyframes) use."""
        names: set[str] = set()
        media: list[str] = []
        places = {name_key(loc.name): loc for loc in self.sb.locations}
        for shot in self.pending():
            names.update(name_key(n) for n in shot.characters)
            media += list(shot.ingredients)
            kf = shot.keyframe
            if kf is None:
                continue
            if kf.media:
                media.append(kf.media)
                continue
            names.update(name_key(n) for n in kf.characters)
            media += list(kf.ingredients)
            plate = places.get(name_key(kf.location)) if kf.location else None
            if plate is not None and plate.media:
                media.append(plate.media)
        for spec in self.sb.characters:
            if name_key(spec.name) in names and spec.photo and not self.created(spec):
                media.append(spec.photo)
        media += [m for sid, m in self.adopt.items()]
        return names, list(dict.fromkeys(media))

    def created(self, spec: CharacterSpec) -> dict[str, Any] | None:
        return self.state.get("characters", {}).get(spec.name)

    async def preflight(self) -> None:
        names, media = self.referenced()
        wanted = [c for c in self.sb.characters if name_key(c.name) in names]
        to_check = [c for c in wanted if not (c.creates and self.created(c))]
        missing: list[str] = []
        clashes: list[str] = []
        if to_check:
            self.say("Checking the characters in Flow")
            listed = await self.ops.characters(self.profile, project=self.project, **self.base)
            self.learn(listed)
            have = listed.get("characters", [])
            for spec in to_check:
                same = [c for c in have if name_key(c.get("name") or "") == name_key(spec.name)]
                if spec.creates:
                    if same:
                        clashes.append(f"character {spec.name} already exists in the project "
                                       f"({same[0]['id']}); drop its create: and use id: "
                                       f"{same[0]['id']}")
                elif spec.id:
                    if not any(c["id"] == spec.id for c in have):
                        missing.append(f"character {spec.name} (id {spec.id})")
                elif not same:
                    missing.append(f"character {spec.name}")
                elif len(same) > 1:
                    clashes.append(f"{len(same)} characters are named {spec.name}: set id:")
        if media:
            self.say("Checking the media in Flow")
            listed = await self.ops.media(self.profile, project=self.project, **self.base)
            self.learn(listed)
            have_media = {m["media_id"]: m for m in listed.get("media", [])}
            adopted = set(self.adopt.values())
            for media_id in media:
                item = have_media.get(media_id)
                if item is None:
                    missing.append(f"media {media_id}")
                elif media_id in adopted and item.get("kind") != "video":
                    clashes.append(f"--adopt {media_id} is not a video")
                elif media_id not in adopted and item.get("kind") != "image":
                    clashes.append(f"media {media_id} is a {item.get('kind')}, not an image")
        if missing:
            raise NotFoundError("not found in the Flow project: " + "; ".join(missing),
                                hint='see "aistudio flow character list" and "aistudio flow '
                                     'media"; nothing was made')
        if clashes:
            raise ValidationError("; ".join(clashes), hint="nothing was made")
        if self.adopt:
            self.save()

    async def quote(self) -> None:
        pending = {s.id for s in self.pending()}
        by_id = {s.id: s for s in self.sb.shots}
        for (model, duration, aspect), ids in quote_groups(self.sb).items():
            ids = [i for i in ids if i in pending]
            if not ids:
                continue
            self.say(f"Asking Flow for a quote ({model}, {duration} s, {aspect})")
            result = await self.ops.generate(
                self.profile, "video", by_id[ids[0]].prompt, model=model, aspect=aspect,
                duration=duration, max_credits=None, project=self.project, **self.base,
                **self.dl)
            self.learn(result)
            self.note(None, result)
            if result.get("status") != "quoted":
                raise AgentReplyError(
                    f"asked Flow for a quote of a {model} clip and got status "
                    f"{result.get('status')!r}",
                    hint="nothing was run; check the project in Flow")
            if result.get("declined") is False:
                self.warnings.append("Flow's quote request may still be waiting: press Reject "
                                     "on it in Flow, never Approve")
            for sid in ids:
                self.price[sid] = int(result["credits"])
                self.basis[sid] = "flow"
        for shot in self.pending():
            if shot.join == "extend":
                seconds = shot.duration or DEFAULT_EXTEND_DURATION
                self.price[shot.id] = seconds * EXTEND_CREDITS_PER_S
                self.basis[shot.id] = "table"

    def remaining(self) -> int:
        return sum(self.price.get(s.id, 0) for s in self.pending())

    def check_budget(self) -> None:
        total = self.remaining()
        if self.paid and self.max_credits is not None and total > self.max_credits:
            raise OverBudgetError(
                f"the clips still to make are quoted at {total} credits in total, over "
                f"--max-credits {self.max_credits}",
                hint=f"nothing was made or spent; re-run with --max-credits {total} to accept")

    # ----- free assets -----------------------------------------------------------------------

    def char_ref(self, name: str) -> str:
        spec = next(c for c in self.sb.characters if name_key(c.name) == name_key(name))
        made = self.created(spec)
        if made:
            return made["id"]
        return spec.id or spec.name

    async def make_assets(self) -> None:
        names, _ = self.referenced()
        for spec in self.sb.characters:
            if spec.creates and name_key(spec.name) in names and not self.created(spec):
                await self.create_character(spec)
        places = {name_key(loc.name): loc for loc in self.sb.locations}
        for shot in self.sb.shots:
            if (self.status(shot.id) != "done" and shot.keyframe is not None
                    and shot.keyframe.media is None):
                await self.make_keyframe(shot, shot.keyframe, places)

    async def create_character(self, spec: CharacterSpec) -> None:
        self.say(f"Creating character {spec.name} (free)")
        result = await self.ops.character(
            self.profile, spec.name, spec.description, photo=spec.photo,
            personality=spec.personality, model=spec.model, max_credits=None,
            project=self.project, out=self.target(f"character-{_slug(spec.name)}.jpg"),
            **self.base, **self.dl)
        self.learn(result)
        self.note(None, result)
        self.spend(result)
        if not result.get("character_id"):
            self.save()
            raise BrowserError(
                f"Flow made character {spec.name} but did not report its id",
                hint='find it with "aistudio flow character list", rename it, and use '
                     '`id:` in the storyboard instead of `create:`')
        self.state.setdefault("characters", {})[spec.name] = {
            "fp": fingerprint(spec), "id": result["character_id"],
            "media_id": result.get("media_id")}
        self.save()

    async def make_keyframe(self, shot: ShotSpec, kf: KeyframeSpec,
                            places: dict[str, LocationSpec]) -> None:
        ingredients: list[str] = []
        plate = places.get(name_key(kf.location)) if kf.location else None
        if plate is not None:
            ingredients.append(plate.media or await self.make_plate(plate))
        ingredients += list(kf.ingredients)
        refs = [self.char_ref(n) for n in kf.characters]
        fp = fingerprint({"keyframe": dataclasses.asdict(kf), "aspect": shot.aspect,
                          "ingredients": ingredients, "characters": refs})
        entry = self.shot_state(shot.id)
        if entry.get("keyframe", {}).get("fp") == fp:
            return
        self.say(f"Making the keyframe for {shot.id} (free)")
        out = self.target(f"keyframe-{shot.id}.jpg")
        result = await self.ops.generate(
            self.profile, "image", kf.prompt, model=KEYFRAME_MODEL, aspect=shot.aspect,
            count=1, max_credits=None, project=self.project, out=out, characters=refs,
            ingredients=ingredients, **self.base, **self.dl)
        media_id = self.image_result(result, f"the keyframe for {shot.id}")
        entry["keyframe"] = {"fp": fp, "media_id": media_id, "file": str(out)}
        self.save()

    async def make_plate(self, plate: LocationSpec) -> str:
        fp = fingerprint(plate)
        made = self.state.get("locations", {}).get(plate.name)
        if made and made.get("fp") == fp:
            return made["media_id"]
        self.say(f"Making the location plate {plate.name} (free)")
        out = self.target(f"plate-{_slug(plate.name)}.jpg")
        result = await self.ops.generate(
            self.profile, "image", plate.prompt, model=KEYFRAME_MODEL, aspect=plate.aspect,
            count=1, max_credits=None, project=self.project, out=out, characters=[],
            ingredients=[], **self.base, **self.dl)
        media_id = self.image_result(result, f"the plate {plate.name}")
        self.state.setdefault("locations", {})[plate.name] = {
            "fp": fp, "media_id": media_id, "file": str(out)}
        self.save()
        return media_id

    def image_result(self, result: dict[str, Any], what: str) -> str:
        self.learn(result)
        self.note(None, result)
        spent = self.spend(result)
        if result.get("status") != "done":
            raise AgentReplyError(
                f"Flow wanted credits for {what} (status {result.get('status')!r}); nothing "
                f"was spent",
                hint="images on Nano Banana 2.1 are free; check the project in Flow")
        if spent:
            self.warnings.append(f"{what} was charged {spent} credits; it should be free")
        media_id = (result.get("files") or [{}])[0].get("media_id")
        if not media_id:
            raise BrowserError(f"Flow made {what} but aistudio did not get its media id",
                               hint='find it with "aistudio flow media --type image"')
        return media_id

    # ----- the clips -------------------------------------------------------------------------

    def clip_of(self, shot_id: str) -> str:
        return self.state["shots"][shot_id]["clip"]

    async def make_clips(self) -> None:
        shots = list(self.sb.shots)
        for index, shot in enumerate(shots):
            if self.status(shot.id) == "done":
                continue
            price = self.price[shot.id]
            ahead = sum(self.price[s.id] for s in shots[index:] if self.status(s.id) != "done")
            limit = self.max_credits if self.max_credits is not None else 0
            if self.run_spent + ahead > limit:
                raise OverBudgetError(
                    f"{self.run_spent} credits are spent and the clips still to make are "
                    f"quoted at {ahead}: together over --max-credits {limit}",
                    hint="nothing more was spent; re-run (the finished shots are kept) with "
                         "a larger --max-credits")
            previous = self.clip_of(shots[index - 1].id) if index else None
            await self.make_clip(index, shot, previous, price)

    async def make_clip(self, index: int, shot: ShotSpec, previous: str | None,
                        price: int) -> None:
        entry = self.shot_state(shot.id)
        frame = None
        if shot.join == "frame":
            frame = await self.save_frame(shot, previous, entry)
        out = self.target(f"clip-{shot.id}.mp4")
        entry.update({"status": "started", "fp": self.shot_fp(index, shot)})
        entry.pop("error", None)
        self.save()  # before the paid call: a crash leaves "started", which blocks a re-run
        self.say(f"Making the clip for {shot.id} (up to {price} credits)")
        try:
            if shot.join == "extend":
                if previous is None:
                    raise ValidationError(f"shot {shot.id} extends nothing", hint="nothing sent")
                result = await self.ops.extend(
                    self.profile, previous, shot.prompt, duration=shot.duration,
                    max_credits=price, project=self.project, out=out,
                    timeout_s=self.timeout_s, **self.base, **self.dl)
            else:
                result = await self.ops.generate(
                    self.profile, "video", shot.prompt, **self.clip_args(shot, entry, frame),
                    max_credits=price, project=self.project, out=out,
                    timeout_s=self.timeout_s, **self.base, **self.dl)
        except BaseException as exc:
            entry["status"] = "unknown" if _may_have_spent(exc) else "failed"
            entry["error"] = (exc.to_dict() if isinstance(exc, AIStudioError)
                              else {"message": f"{type(exc).__name__}: {exc}"})
            self.save()
            raise
        self.finish_clip(shot, entry, result, price, out)

    def finish_clip(self, shot: ShotSpec, entry: dict[str, Any], result: dict[str, Any],
                    price: int, out: Path) -> None:
        self.learn(result)
        self.note(shot.id, result)
        spent = self.spend(result)
        entry["credits_spent"] = spent
        if result.get("status") == "timeout":
            entry.update({"status": "timeout", "media_ids": result.get("media_ids", [])})
            self.save()
            raise WaitTimeoutError(
                f"Flow was still making shot {shot.id} when aistudio stopped waiting "
                f"({spent} credits spent): {result.get('project_url')}",
                hint=f"check the project in Flow, then pass --adopt {shot.id}=<media id> "
                     f"when the clip is there")
        files = result.get("files") or []
        media_id = files[0].get("media_id") if files else None
        if result.get("status") != "done" or not media_id:
            entry["status"] = "unknown"
            self.save()
            raise BrowserError(
                f"shot {shot.id} came back without a clip id (status "
                f"{result.get('status')!r}); credits may have been spent",
                hint='check "aistudio flow media --type video", then use --adopt')
        if spent is None:
            self.warnings.append(f"shot {shot.id}: the charge was not verified (quoted "
                                 f"{price})")
        elif spent != price:
            self.warnings.append(f"shot {shot.id}: charged {spent} credits, quoted {price}")
        entry.update({"status": "done", "clip": media_id, "file": files[0].get("path")})
        self.save()

    def clip_args(self, shot: ShotSpec, entry: dict[str, Any], frame: str | None
                  ) -> dict[str, Any]:
        first = frame or (shot.keyframe.media if shot.keyframe and shot.keyframe.media
                          else entry.get("keyframe", {}).get("media_id"))
        if not first:
            raise ValidationError(f"shot {shot.id} has no keyframe yet",
                                  hint="nothing was spent; run with --keyframes first")
        args: dict[str, Any] = {"model": shot.model, "aspect": shot.aspect,
                                "duration": shot.duration, "count": 1}
        if shot.mode == "start_frame":
            args.update(start_frame=first, ingredients=[], characters=[])
        else:
            args.update(start_frame=None, ingredients=[first, *shot.ingredients],
                        characters=[self.char_ref(n) for n in shot.characters])
        return args

    async def save_frame(self, shot: ShotSpec, previous: str | None,
                         entry: dict[str, Any]) -> str:
        saved = entry.get("frame")
        if saved and saved.get("of") == previous:
            return saved["media_id"]
        self.say(f"Saving a frame of the clip before {shot.id} (free)")
        result = await self.ops.frame(
            self.profile, previous, at=shot.frame_at, project=self.project,
            out=self.target(f"frame-{shot.id}.jpg"), **self.base, **self.dl)
        self.learn(result)
        self.note(shot.id, result)
        self.spend(result)
        entry["frame"] = {"of": previous, "media_id": result["media_id"]}
        self.save()
        return result["media_id"]

    # ----- the join --------------------------------------------------------------------------

    def clip_refs(self) -> list[str]:
        refs = []
        for shot in self.sb.shots:
            clip = self.state.get("shots", {}).get(shot.id, {}).get("clip")
            if clip is None:
                return []
            refs.append(f"{clip}@{shot.trim[0]}-{shot.trim[1]}" if shot.trim else clip)
        return refs

    async def assemble(self, out: Path) -> None:
        refs = self.clip_refs()
        if self.final_done():
            self.finals = [{"path": str(out)}]
            return
        self.say("Joining the clips (free)")
        if len(refs) == 1:
            result = await self.ops.download(
                self.profile, refs[0], project=self.project, out=out,
                overwrite=self.overwrite, **self.base, **self.dl)
        else:
            result = await self.ops.stitch(
                self.profile, refs, project=self.project, out=out,
                overwrite=self.overwrite, now=self.now, **self.base)
        self.learn(result)
        self.note(None, result)
        self.finals = result.get("files", [])
        self.state["final"] = {"path": str(out), "clips": refs}
        self.save()

    # ----- the answer ------------------------------------------------------------------------

    def report(self, error: BaseException | None) -> dict[str, Any]:
        status = "done" if not self.pending() and error is None else "quoted"
        if error is not None:
            timed_out = isinstance(error, WaitTimeoutError)
            status = "timeout" if timed_out else "failed"
        shots = [self.shot_report(s) for s in self.sb.shots]
        result: dict[str, Any] = {
            "status": status, "storyboard": str(self.path), "state": str(self.state_path),
            "assets": str(self.assets), "project": self.project,
            "credits_spent": self.run_spent,
            "credits_spent_total": self.state.get("credits_spent", 0),
            "credits_left": self.credits_left,
            "quote": {"credits": self.remaining(), "pending_shots": len(self.pending()),
                      "basis": QUOTE_BASIS},
            "shots": shots,
        }
        if self.project:
            result["project_url"] = PROJECT_URL.format(id=self.project)
        if self.finals:
            result["files"] = self.finals
        if error is not None:
            result["error"] = ({k: v for k, v in error.to_dict().items() if k != "error"}
                               if isinstance(error, AIStudioError)
                               else {"code": "UNEXPECTED_ERROR",
                                     "message": f"{type(error).__name__}: {error}"})
        if self.warnings:
            result["warnings"] = self.warnings
        return result

    def shot_report(self, shot: ShotSpec) -> dict[str, Any]:
        entry = self.state.get("shots", {}).get(shot.id, {})
        steps = (["extend"] if shot.join == "extend"
                 else ["frame", "clip"] if shot.join == "frame"
                 else ["clip"] if shot.keyframe and shot.keyframe.media
                 else ["keyframe", "clip"])
        keyframe = (shot.keyframe.media if shot.keyframe and shot.keyframe.media
                    else entry.get("keyframe", {}).get("media_id"))
        status = entry.get("status", "pending")
        item: dict[str, Any] = {
            "id": shot.id, "join": shot.join, "kind": "extend" if shot.join == "extend"
            else "video", "mode": shot.mode, "model": shot.model, "duration": shot.duration,
            "aspect": shot.aspect, "status": status, "steps": steps,
            "quote": self.price.get(shot.id) if status != "done" else None,
            "quote_basis": self.basis.get(shot.id) if status != "done" else None,
            "keyframe": keyframe, "clip": entry.get("clip"), "file": entry.get("file"),
            "credits_spent": entry.get("credits_spent")}
        if entry.get("media_ids"):
            item["media_ids"] = entry["media_ids"]
        if entry.get("error"):
            item["error"] = entry["error"]
        return item
