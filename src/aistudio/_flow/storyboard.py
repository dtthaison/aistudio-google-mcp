"""A Flow scene described in one YAML file: parsing and checking, with no browser (pure).

``parse_storyboard`` checks the whole file and reports every problem at once, so nothing runs
(and nothing is spent) from a file that is wrong somewhere. The rules come from what was
measured live (docs/wire/findings.md, SKILL.md "Making a Flow scene"):

- Flow cannot combine a start frame with a character (its agent asks a question instead).
- A character clip takes at most one ingredient (a character plus two failed, refunded).
- ``flow extend`` takes only a prompt and always uses Veo 3.1 Lite.
- Stitch trims are whole seconds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from aistudio._flow.models import ASPECTS, CHARACTER_MODELS, find_model
from aistudio.exceptions import ValidationError

DEFAULT_MODEL = "veo-3.1-lite"
DEFAULT_DURATION = 8
DEFAULT_ASPECT = "16:9"
EXTEND_TRIM_MAX = 7  # an extension adds 7-8 s (live); a trim past 7 could miss the clip's end
LONG_LINE_WORDS = 20  # SKILL.md: a spoken line under about 20 words fits an 8-second clip
JOINS = ("cut", "extend", "frame")
MODES = ("start_frame", "ingredient")
MAX_FILE_BYTES = 1024 * 1024

_MEDIA_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PROJECT = re.compile(r"(?:project/)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{12})/?$")
_SHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
_TOP = {"project", "style", "wardrobe", "model", "duration", "aspect", "characters", "locations",
        "shots"}
_CHARACTER = {"name", "id", "create"}
_CREATE = {"description", "photo", "personality", "model"}
_LOCATION = {"name", "prompt", "media", "aspect"}
_SHOT = {"id", "prompt", "say", "speaker", "join", "mode", "characters", "location",
         "ingredients", "keyframe", "frame_at", "model", "duration", "aspect", "trim"}
_KEYFRAME = {"prompt", "media", "characters", "location", "ingredients"}
# What an extend shot may not carry: Flow extends the clip as it is (flow extend takes a prompt)
_EXTEND_REFUSES = ("mode", "characters", "location", "ingredients", "keyframe", "frame_at",
                   "duration", "aspect")


@dataclasses.dataclass(frozen=True)
class CharacterSpec:
    name: str
    id: str | None = None
    description: str | None = None
    photo: str | None = None  # a Flow media id (a local file would need --i-have-rights)
    personality: str | None = None
    model: str | None = None

    @property
    def creates(self) -> bool:
        return self.description is not None or self.photo is not None


@dataclasses.dataclass(frozen=True)
class LocationSpec:
    name: str
    prompt: str | None = None  # a free image of an empty place, style sentence appended
    media: str | None = None  # or an image already in the project
    aspect: str | None = None  # of the generated plate


@dataclasses.dataclass(frozen=True)
class KeyframeSpec:
    prompt: str  # with the fixed sentences, no spoken line
    media: str | None = None  # an image already in the project: nothing is generated
    characters: tuple[str, ...] = ()
    location: str | None = None
    ingredients: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ShotSpec:
    id: str
    join: str
    prompt: str  # the clip's prompt: line and fixed sentences included
    mode: str | None  # None for an extension
    characters: tuple[str, ...]  # attached to the clip
    ingredients: tuple[str, ...]  # attached to the clip besides the keyframe
    location: str | None
    keyframe: KeyframeSpec | None  # join "cut" only
    frame_at: float | None
    model: str
    duration: int | None  # None for an extension (Flow decides, 7-8 s)
    aspect: str
    trim: tuple[int, int] | None


@dataclasses.dataclass(frozen=True)
class Storyboard:
    project: str | None
    characters: tuple[CharacterSpec, ...]
    locations: tuple[LocationSpec, ...]
    shots: tuple[ShotSpec, ...]
    warnings: tuple[str, ...] = ()


def fingerprint(item: Any) -> str:
    """A short hash of a spec's content: a finished step is only reused while it is unchanged."""
    is_spec = dataclasses.is_dataclass(item) and not isinstance(item, type)
    data = dataclasses.asdict(item) if is_spec else item
    text = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def name_key(name: str) -> str:
    """How Flow compares character names: spaces collapsed, any case."""
    return " ".join(name.split()).casefold()


def compose_clip_prompt(prompt: str, say: str | None, speaker: str | None,
                        fixed: list[str]) -> str:
    """The prompt, the spoken line and the fixed sentences, in that order, each unchanged."""
    parts = [_sentence(prompt)]
    if say:
        parts.append(f'{speaker or "The speaker"} says: "{say}".')
    parts += fixed
    return " ".join(parts)


def compose_image_prompt(prompt: str, fixed: list[str]) -> str:
    return " ".join([_sentence(prompt), *fixed])


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?", '"', "…")) else text + "."


def quote_groups(sb: Storyboard) -> dict[tuple[str, int, str], list[str]]:
    """Shots that share one Flow quote: it depends on the model, length and aspect, not the
    prompt (10 credits a Lite clip with or without attachments). Extensions are priced by
    aistudio's table, not quoted."""
    groups: dict[tuple[str, int, str], list[str]] = {}
    for shot in sb.shots:
        if shot.join != "extend" and shot.duration is not None:
            groups.setdefault((shot.model, shot.duration, shot.aspect), []).append(shot.id)
    return groups


def load_storyboard(path: Path) -> Storyboard:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValidationError(f"{path} is larger than 1 MB; is it a storyboard?")
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValidationError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValidationError(f"{path} is not valid YAML text: {exc}") from exc
    return parse_storyboard(data)


def parse_storyboard(data: Any) -> Storyboard:
    parser = _Parser()
    sb = parser.run(data)
    if parser.problems or sb is None:
        count = len(parser.problems) or 1
        raise ValidationError(
            f"the storyboard has {count} problem{'s' if count > 1 else ''}:\n"
            + "\n".join(f"- {p}" for p in parser.problems),
            hint="nothing was run; fix the file and try again")
    return dataclasses.replace(sb, warnings=tuple(parser.warnings))


class _Parser:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def bad(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    # ----- small readers -------------------------------------------------------------------

    def keys(self, data: Mapping[str, Any], allowed: set[str], where: str) -> None:
        for key in data:
            if key not in allowed:
                self.bad(where, f"unknown key {key!r} (allowed: {', '.join(sorted(allowed))})")

    def text(self, data: Mapping[str, Any], key: str, where: str, *,
             required: bool = False) -> str | None:
        value = data.get(key)
        if value is None:
            if required:
                self.bad(where, f"{key} is required")
            return None
        if not isinstance(value, str) or not value.strip():
            self.bad(where, f"{key} must be non-empty text")
            return None
        return value.strip()

    def names(self, data: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
        value = data.get(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                                  for v in value):
            self.bad(where, f"{key} must be a list of names")
            return ()
        return tuple(" ".join(v.split()) for v in value)

    def media_ids(self, data: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
        value = data.get(key)
        if value is None:
            return ()
        if not isinstance(value, list):
            self.bad(where, f"{key} must be a list of Flow media ids")
            return ()
        out = []
        for item in value:
            if isinstance(item, str) and _MEDIA_ID.match(item.strip()):
                out.append(item.strip())
            else:
                self.bad(where, f"{item!r} is not a Flow media id (see \"aistudio flow media\")")
        return tuple(out)

    def media_id(self, value: Any, where: str, what: str) -> str | None:
        if isinstance(value, str) and _MEDIA_ID.match(value.strip()):
            return value.strip()
        self.bad(where, f"{what} {value!r} is not a Flow media id (see \"aistudio flow media\")")
        return None

    # ----- the file --------------------------------------------------------------------------

    def run(self, data: Any) -> Storyboard | None:
        if not isinstance(data, dict):
            self.bad("storyboard", "the file must be a mapping (project, characters, shots, ...)")
            return None
        self.keys(data, _TOP, "storyboard")
        project = self.project(data)
        style = self.text(data, "style", "style")
        wardrobe = self.text(data, "wardrobe", "wardrobe")
        model, duration, aspect = self.defaults(data)
        characters = self.characters(data.get("characters"))
        fixed_all = [s for s in (style, wardrobe) if s]
        locations = self.locations(data.get("locations"), [style] if style else [], aspect)
        shots = self.shots(data.get("shots"), characters, locations, fixed_all, model, duration,
                           aspect)
        if len(shots) == 1 and shots[0].trim is not None:
            self.bad(f"shot {shots[0].id}", "trim needs two shots or more: a single clip is "
                                            "downloaded as it is (stitching joins two or more)")
        return Storyboard(project, tuple(characters), tuple(locations), tuple(shots))

    def project(self, data: Mapping[str, Any]) -> str | None:
        value = self.text(data, "project", "project")
        if value is None:
            return None
        match = _PROJECT.search(value.split("#", 1)[0].split("?", 1)[0])
        if match is None:
            self.bad("project", f"{value!r} is not a Flow project id or link")
            return None
        return match.group(1)

    def defaults(self, data: Mapping[str, Any]) -> tuple[str, int, str]:
        model = self.model(data.get("model"), "model") or DEFAULT_MODEL
        duration = self.duration(data.get("duration"), "duration", model)
        aspect = self.aspect(data.get("aspect"), "aspect") or DEFAULT_ASPECT
        return model, duration if duration is not None else DEFAULT_DURATION, aspect

    def model(self, value: Any, where: str) -> str | None:
        if value is None:
            return None
        try:
            return find_model(str(value), "video").id
        except ValidationError as exc:
            self.bad(where, exc.message)
            return None

    def duration(self, value: Any, where: str, model: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self.bad(where, "duration must be a whole number of seconds")
            return None
        allowed = find_model(model, "video").durations
        if value not in allowed:
            self.bad(where, f"{model} makes {', '.join(map(str, allowed))}-second videos, "
                            f"not {value}")
            return None
        return value

    def aspect(self, value: Any, where: str) -> str | None:
        if value is None:
            return None
        if value not in ASPECTS["video"]:
            self.bad(where, f"Flow videos come in {', '.join(ASPECTS['video'])}, not {value}")
            return None
        return str(value)

    # ----- characters and locations ----------------------------------------------------------

    def characters(self, value: Any) -> list[CharacterSpec]:
        out: list[CharacterSpec] = []
        if value is None:
            return out
        if not isinstance(value, list):
            self.bad("characters", "must be a list")
            return out
        seen: dict[str, str] = {}
        for i, item in enumerate(value):
            where = f"characters[{i}]"
            if not isinstance(item, dict):
                self.bad(where, "must be a mapping with a name")
                continue
            self.keys(item, _CHARACTER, where)
            name = self.text(item, "name", where, required=True)
            if name is None:
                continue
            name = " ".join(name.split())
            where = f"character {name}"
            if name_key(name) in seen:
                self.bad(where, f"character names must be unique (also {seen[name_key(name)]!r}"
                                f"): Flow attaches characters by name, ignoring case")
            seen[name_key(name)] = name
            ident = self.text(item, "id", where)
            create = item.get("create")
            if create is None:
                out.append(CharacterSpec(name, id=ident))
                continue
            if ident is not None:
                self.bad(where, "give an id (an existing character) or create (a new one), "
                                "not both")
            out.append(self.create(name, create, where))
        return out

    def create(self, name: str, create: Any, where: str) -> CharacterSpec:
        if not isinstance(create, dict):
            self.bad(where, "create must be a mapping (description or photo)")
            return CharacterSpec(name)
        self.keys(create, _CREATE, f"{where} create")
        description = self.text(create, "description", f"{where} create")
        photo = None
        if create.get("photo") is not None:
            photo = self.media_id(create["photo"], f"{where} create", "photo")
            if photo is None:
                self.problems[-1] += (" (a local file is never uploaded from a storyboard: it "
                                      "needs --i-have-rights, which only you can give)")
        if (description is None) == (photo is None) and (
                create.get("photo") is None or photo is not None):
            self.bad(where, "create needs exactly one of description or photo")
        model = None
        if create.get("model") is not None:
            model = str(create["model"]).strip().lower()
            if model not in CHARACTER_MODELS:
                self.bad(where, f"{model!r} is not a character model "
                                f"(choose one of: {', '.join(CHARACTER_MODELS)})")
        if model and photo:
            self.bad(where, "model applies to a character made from a description")
        return CharacterSpec(name, description=description, photo=photo,
                             personality=self.text(create, "personality", f"{where} create"),
                             model=model)

    def locations(self, value: Any, fixed: list[str], aspect: str) -> list[LocationSpec]:
        out: list[LocationSpec] = []
        if value is None:
            return out
        if not isinstance(value, list):
            self.bad("locations", "must be a list")
            return out
        seen: set[str] = set()
        for i, item in enumerate(value):
            where = f"locations[{i}]"
            if not isinstance(item, dict):
                self.bad(where, "must be a mapping with a name")
                continue
            self.keys(item, _LOCATION, where)
            name = self.text(item, "name", where, required=True)
            if name is None:
                continue
            name = " ".join(name.split())
            where = f"location {name}"
            if name_key(name) in seen:
                self.bad(where, "location names must be unique")
            seen.add(name_key(name))
            prompt = self.text(item, "prompt", where)
            media = None
            if item.get("media") is not None:
                media = self.media_id(item["media"], where, "media")
            if (prompt is None) == (media is None) and (
                    item.get("media") is None or media is not None):
                self.bad(where, "needs exactly one of prompt (a plate to generate) or media")
            plate_aspect = None
            if prompt is not None:
                if item.get("aspect") is not None and item["aspect"] not in ASPECTS["image"]:
                    self.bad(where, f"Flow images come in {', '.join(ASPECTS['image'])}")
                plate_aspect = str(item["aspect"]) if item.get("aspect") else aspect
                prompt = compose_image_prompt(prompt, fixed)
            out.append(LocationSpec(name, prompt, media, plate_aspect))
        return out

    # ----- shots -----------------------------------------------------------------------------

    def shots(self, value: Any, characters: list[CharacterSpec], locations: list[LocationSpec],
              fixed: list[str], model: str, duration: int, aspect: str) -> list[ShotSpec]:
        out: list[ShotSpec] = []
        if not isinstance(value, list) or not value:
            self.bad("shots", "needs a list with at least one shot")
            return out
        known = {name_key(c.name): c.name for c in characters}
        places = {name_key(loc.name): loc for loc in locations}
        ids: set[str] = set()
        for i, item in enumerate(value):
            default_id = f"shot-{i + 1}"
            if not isinstance(item, dict):
                self.bad(f"shots[{i}]", "must be a mapping")
                continue
            sid = item.get("id", default_id)
            if not isinstance(sid, str) or not _SHOT_ID.match(sid):
                self.bad(f"shot {default_id}", f"id {sid!r} must be letters, digits, '_' or '-' "
                                               f"(up to 40): it names files and state")
                sid = default_id
            if sid in ids:
                self.bad(f"shot {sid}", "shot ids must be unique")
            ids.add(sid)
            shot = self.shot(item, sid, i, out[-1] if out else None, known, places, fixed, model,
                             duration, aspect)
            if shot is not None:
                out.append(shot)
        return out

    def shot(self, item: dict[str, Any], sid: str, index: int, previous: ShotSpec | None,
             known: dict[str, str], places: dict[str, LocationSpec], fixed: list[str],
             model: str, duration: int, aspect: str) -> ShotSpec | None:
        where = f"shot {sid}"
        self.keys(item, _SHOT, where)
        prompt = self.text(item, "prompt", where, required=True)
        say = self.text(item, "say", where)
        join = item.get("join", "cut")
        if join not in JOINS:
            self.bad(where, f"join must be one of {', '.join(JOINS)}, not {join!r}")
            return None
        if index == 0 and join != "cut":
            self.bad(where, f"the first shot has nothing to {join}: leave join out (it opens "
                            "on a keyframe)")
        if previous is None and join != "cut":
            join = "cut"  # reported above; keep checking the rest of the shot
        speaker = self.speaker(item, known, where)
        if join == "extend":
            return self.extension(item, sid, where, prompt, say, speaker, previous, fixed)
        shot_model = self.model(item.get("model"), f"{where} model") or model
        shot_duration = self.duration(item.get("duration", duration), f"{where} duration",
                                      shot_model)
        shot_aspect = self.aspect(item.get("aspect"), f"{where} aspect") or aspect
        characters = self.refs(item, "characters", known, where)
        location = self.location(item.get("location"), places, where)
        ingredients = self.media_ids(item, "ingredients", where)
        mode = item.get("mode", "ingredient" if characters else "start_frame")
        if mode not in MODES:
            self.bad(where, f"mode must be one of {', '.join(MODES)}, not {mode!r}")
            mode = "ingredient" if characters else "start_frame"
        frame_at = self.frame_at(item, join, where)
        if mode == "start_frame":
            if characters:
                self.bad(where, "Flow cannot combine a start frame with a character (its agent "
                                "asks a question and nothing is made): use mode: ingredient to "
                                "attach the character, or keep the character in the keyframe "
                                "only (keyframe: {characters: [...]})")
            if ingredients:
                self.bad(where, "a start frame with an ingredient is not measured: use mode: "
                                "ingredient, or leave the ingredients out")
        elif characters and ingredients:
            self.bad(where, "a clip with a character takes at most one ingredient (the "
                            "keyframe); a character plus two failed in Flow: drop ingredients")
        keyframe = None
        if join == "cut":
            keyframe = self.keyframe(item, where, characters, location, places, known, fixed,
                                     shot_aspect, prompt)
        else:  # frame
            if item.get("keyframe") is not None:
                self.bad(where, "keyframe cannot be set when join is frame: the frame is saved "
                                "from the clip before")
            if item.get("location") is not None:
                self.bad(where, "location only feeds keyframes, and a frame join has none")
            if previous is not None and shot_aspect != previous.aspect:
                self.bad(where, f"a saved frame keeps the aspect of the clip before "
                                f"({previous.aspect}), not {shot_aspect}")
        trim = self.trim(item.get("trim"), where, shot_duration)
        if say and len(say.split()) > LONG_LINE_WORDS:
            self.warnings.append(f"shot {sid}: the spoken line has {len(say.split())} words; "
                                 f"under about {LONG_LINE_WORDS} fits one clip")
        if mode == "start_frame" and keyframe is not None and keyframe.characters:
            self.warnings.append(f"shot {sid}: {', '.join(keyframe.characters)} appears in the "
                                 f"keyframe only; a start frame cannot attach the character, so "
                                 f"its saved voice is not used")
        text = compose_clip_prompt(prompt or "", say, speaker or _first(characters, keyframe),
                                   fixed)
        return ShotSpec(sid, join, text, mode, characters, ingredients, location, keyframe,
                        frame_at, shot_model, shot_duration, shot_aspect, trim)

    def extension(self, item: dict[str, Any], sid: str, where: str, prompt: str | None,
                  say: str | None, speaker: str | None, previous: ShotSpec | None,
                  fixed: list[str]) -> ShotSpec | None:
        for key in _EXTEND_REFUSES:
            if item.get(key) is not None:
                self.bad(where, f"{key} cannot be set on an extend shot: Flow extends the clip "
                                f"before as it is (flow extend takes only a prompt)")
        if item.get("model") is not None and self.model(item["model"], f"{where} model") != (
                DEFAULT_MODEL):
            self.bad(where, "model cannot be set on an extend shot: Flow always extends with "
                            "Veo 3.1 Lite")
        if previous is not None and not previous.model.startswith("veo"):
            self.bad(where, f"cannot extend a {previous.model} clip: Flow extends Veo clips "
                            f"(Veo 3.1 Lite measured); use join: cut or frame")
        trim = self.trim(item.get("trim"), where, EXTEND_TRIM_MAX)
        if say and len(say.split()) > LONG_LINE_WORDS:
            self.warnings.append(f"shot {sid}: the spoken line has {len(say.split())} words; "
                                 f"under about {LONG_LINE_WORDS} fits one clip")
        text = compose_clip_prompt(prompt or "", say, speaker, fixed)
        return ShotSpec(sid, "extend", text, None, (), (), None, None, None, DEFAULT_MODEL, None,
                        previous.aspect if previous else DEFAULT_ASPECT, trim)

    def speaker(self, item: Mapping[str, Any], known: dict[str, str], where: str) -> str | None:
        value = self.text(item, "speaker", where)
        if value is None:
            return None
        if name_key(value) not in known:
            self.bad(where, f"speaker {value!r} is not a declared character")
            return None
        return known[name_key(value)]

    def refs(self, data: Mapping[str, Any], key: str, known: dict[str, str],
             where: str) -> tuple[str, ...]:
        out: list[str] = []
        for name in self.names(data, key, where):
            if name_key(name) not in known:
                self.bad(where, f"{key}: {name!r} is not a declared character")
            elif known[name_key(name)] in out:
                self.bad(where, f"{key}: {name!r} is listed twice")
            else:
                out.append(known[name_key(name)])
        return tuple(out)

    def location(self, value: Any, places: dict[str, LocationSpec], where: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or name_key(value) not in places:
            self.bad(where, f"location {value!r} is not declared under locations")
            return None
        return places[name_key(value)].name

    def frame_at(self, item: Mapping[str, Any], join: str, where: str) -> float | None:
        value = item.get("frame_at")
        if value is None:
            return None
        if join != "frame":
            self.bad(where, "frame_at only goes with join: frame")
            return None
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            self.bad(where, "frame_at must be seconds into the clip before, 0 or more")
            return None
        return float(value)

    def trim(self, value: Any, where: str, limit: int | None) -> tuple[int, int] | None:
        if value is None:
            return None
        ok = (isinstance(value, list) and len(value) == 2
              and all(isinstance(v, int) and not isinstance(v, bool) for v in value))
        if not ok:
            self.bad(where, "trim must be [start, end] in whole seconds, e.g. [1, 6]")
            return None
        start, end = value
        if not 0 <= start < end or (limit is not None and end > limit):
            self.bad(where, f"trim [{start}, {end}] must have 0 <= start < end"
                            f"{f' <= {limit}' if limit is not None else ''} (whole seconds)")
            return None
        return start, end

    def keyframe(self, item: Mapping[str, Any], where: str, characters: tuple[str, ...],
                 location: str | None, places: dict[str, LocationSpec],
                 known: dict[str, str], fixed: list[str], aspect: str,
                 shot_prompt: str | None) -> KeyframeSpec | None:
        raw = item.get("keyframe")
        if raw is None:
            raw = {}
        elif isinstance(raw, str):
            raw = {"prompt": raw}
        if not isinstance(raw, dict):
            self.bad(where, "keyframe must be text or a mapping")
            return None
        w = f"{where} keyframe"
        self.keys(raw, _KEYFRAME, w)
        if raw.get("media") is not None:
            extra = [k for k in _KEYFRAME - {"media"} if raw.get(k) is not None]
            if extra:
                self.bad(w, f"media is an image already in Flow: nothing is generated, so "
                            f"{', '.join(sorted(extra))} cannot be set")
            media = self.media_id(raw["media"], w, "media")
            return KeyframeSpec("", media=media)
        prompt = self.text(raw, "prompt", w) or shot_prompt or ""
        kf_characters = self.refs(raw, "characters", known, w) if "characters" in raw \
            else characters
        kf_location = self.location(raw["location"], places, w) if "location" in raw \
            else location
        extras = self.media_ids(raw, "ingredients", w)
        plate = places[name_key(kf_location)] if kf_location else None
        count = len(extras) + (1 if plate is not None else 0)
        if kf_characters and count > 1:
            self.bad(w, f"a keyframe with a character takes at most one ingredient (the "
                        f"location plate counts); it has {count}")
        if plate is not None and plate.prompt is not None and plate.aspect != aspect:
            self.bad(w, f"location {plate.name} is a {plate.aspect} plate but the shot is "
                        f"{aspect}: set the location's aspect")
        return KeyframeSpec(compose_image_prompt(prompt, fixed), None, kf_characters,
                            kf_location, extras)


def _first(characters: tuple[str, ...], keyframe: KeyframeSpec | None) -> str | None:
    if characters:
        return characters[0]
    if keyframe is not None and keyframe.characters:
        return keyframe.characters[0]
    return None
