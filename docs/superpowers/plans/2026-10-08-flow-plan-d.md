# Flow Plan D: Names, Photos, Characters as Ingredients, Scenes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Each step is RED→GREEN: write the test, watch it fail, implement, run the suite.

**Goal:** Build the Flow features still missing after plans A–C (user: "go ahead all the issues an unbuilt"):
- character names (`create NAME`, `rename`);
- a character from a photo;
- `character list`;
- `--character` on `flow video`/`flow image`;
- scene stitching (`scene list|stitch|download`).

**Architecture:**
- **Reads and free writes** that carry no reCAPTCHA token are sent from the page with `Ui.batch_rpc`, as `as29s` already is:
  - `rzMKMb` renames;
  - `C4BZMd` + `Sc7aEb` create a character from a photo;
  - `GjQLTd` + `IxdSwd` stitch.
- **Paid or token-protected calls stay page-driven.** Characters are attached through the + panel. The free quote's `StreamChat` request is then checked for the exact character ids before anything can be approved.

**Tech Stack:** Python 3.12+, Playwright (via `aistudio._browser.ui`), click, pytest (asyncio auto), ruff.

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md`, §11 (plan D amendments) together with §3, §7, §10. Evidence: `docs/wire/findings.md`, "Flow plan D discovery".

## Global Constraints

- **Secrets:** never print or log cookies, `at=` tokens, reCAPTCHA tokens or signed-URL signatures. Test fixtures are synthetic.
- **Spending:**
  - Nothing new spends credits, except `--character` on video/image, which keeps the quote and `--max-credits` path (§3.1).
  - Character from a description keeps the price table (§10.2).
- **Local files:** uploading a local file needs `--i-have-rights` (§10.3).
- **Never lose a spent result:** an error after a spend names the media or character id.
- **Unique names:** character names are unique per project, compared case-insensitively (§11.2).
- **Trims:** whole seconds; `0 ≤ START < END ≤ length` (§11.5).
- **Quality gate:** ruff clean and the suite green after each task.

## Review Focus

1. **Two characters share a name, or names differ only in case.** `--character NAME` refuses with a rename hint. `create` and `rename` refuse the name.
2. **The panel attaches a different character than asked.** The `StreamChat` id check rejects Flow's request and raises `PLAN_MISMATCH` before any approval.
3. **A stitch clip is trimmed past its length, or is still running or failed.** It is refused before anything is sent, naming the clip.
4. **`Sc7aEb` fails after `C4BZMd` created the record.** The error names the new character id and its name, so nobody re-runs blindly and makes duplicates.
5. **A stitched MP4 of more than 16 MB in base64.** It is written byte-exact. An existing `-o` without `--force` is refused before Chrome opens.

---

### Task 1: Decode characters, scenes, stitch replies and sent characters

**Files:**
- Modify: `src/aistudio/_flow/rows.py`
- Test: `tests/unit/test_flow_rows_d.py` (new)

**Interfaces:**
- Produces:
  - `FlowCharacter(id: str, name: str, media_id: str | None, created: int | None)`;
  - `SceneClip(media_id: str, title: str | None, start: int, end: int)`;
  - `FlowScene(id: str, title: str | None, created: int | None, clips: list[SceneClip])`;
  - `ProjectContents.characters: list[FlowCharacter]` and `ProjectContents.scenes: list[FlowScene]` (both default empty);
  - `stitch_job(payload) -> str | None`;
  - `stitch_state(payload) -> tuple[int | None, str | None]`;
  - `sent_characters(body: str) -> list[str]`;
  - `linked_image(payload) -> str | None`;
  - `character_name(payload) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
import json
from urllib.parse import urlencode

from aistudio._flow.rows import (
    decode_contents, linked_image, sent_characters, stitch_job, stitch_state, character_name)

PID = "p1"
CHARS = [[PID, "c1", None, [1, "Paper boat", [[["w9"]]]], "i1", [1376, 768], [100, 0], [101, 0]],
         [PID, "c2", None, [1, "Untitled character", []], None, None, [90, 0], [90, 0]]]
SCENES = [["s1", "Untitled Scene 10-08", None, [200, 0], [201, 0], 2, None, []]]
CLIPS = [[["w2", None, None, ["the camera pulls back", [210, 0], None, None, "m2"], PID], "s1",
          [1, [8], [], [8], [1]]],
         [["w1", None, None, ["Red apple", [205, 0], None, None, "m1"], PID], "s1",
          [None, [8], [], [8], [1]]]]


def contents(**slots):
    payload = [None, [], [], [], [], [], [], None]
    for index, value in slots.items():
        payload[int(index[1:])] = value
    return decode_contents(payload)


def test_characters_come_from_slot_5():
    got = contents(s5=CHARS).characters
    assert [(c.id, c.name, c.media_id, c.created) for c in got] == [
        ("c1", "Paper boat", "i1", 100), ("c2", "Untitled character", None, 90)]


def test_scenes_list_their_clips_in_position_order():
    (scene,) = contents(s4=SCENES, s6=CLIPS).scenes
    assert (scene.id, scene.title, scene.created) == ("s1", "Untitled Scene 10-08", 200)
    assert [(c.media_id, c.title, c.start, c.end) for c in scene.clips] == [
        ("m1", "Red apple", 0, 8), ("m2", "the camera pulls back", 0, 8)]


def test_a_contents_payload_without_new_slots_still_decodes():
    assert decode_contents([None, [], []]).characters == []


def test_stitch_job_and_state():
    job = "projects/1/locations/us/jobs/u1"
    assert stitch_job([None, [[job]]]) == job
    assert stitch_job([None, [["not-a-job"]]]) is None
    assert stitch_state([2, "", "", 0]) == (2, None)
    assert stitch_state([3, "", "", 3, "AAAA"]) == (3, "AAAA")
    assert stitch_state(None) == (None, None)


def test_sent_characters_reads_the_character_slot():
    message = ["sess", [[[["a prompt"]]], None, None, [["c1"], ["c2"]]], ["projects/p1", None,
                                                                        ["tok", 1], None, None, 3]]
    body = urlencode({"f.req": json.dumps([None, json.dumps(message)]), "at": "x"})
    assert sent_characters(body) == ["c1", "c2"]
    no_chars = ["sess", [[[["a prompt"]]]], ["projects/p1"]]
    assert sent_characters(urlencode({"f.req": json.dumps([None, json.dumps(no_chars)])})) == []
    assert sent_characters("garbage") == []


def test_linked_image_and_character_name():
    assert linked_image([["i9", PID, "w9"], ["w9"]]) == "i9"
    assert linked_image([]) is None
    assert character_name([[PID, "c1", None, [1, "Boat", []]]]) == "Boat"
```

- [ ] **Step 2: Run them.** `uv run pytest tests/unit/test_flow_rows_d.py -q`. Expected: FAIL (ImportError: `linked_image`).

- [ ] **Step 3: Implement in `rows.py`**

```python
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
    start: int  # whole seconds
    end: int


@dataclass(frozen=True)
class FlowScene:
    id: str
    title: str | None
    created: int | None
    clips: list[SceneClip]
```

`ProjectContents` gains `characters: list[FlowCharacter] = field(default_factory=list)` and `scenes: list[FlowScene] = field(default_factory=list)`.

`decode_contents` also reads the new slots:
- `[5]` gives characters;
- `[4]` gives scenes;
- `[6]` gives clips, grouped by scene id and sorted by position, with `None` as 0. A clip's `start`/`end` come from `[2][2]`/`[2][3]` (`[]` is 0).

The helpers:

```python
def _seconds(value: Any) -> int:
    first = opt_index(value, 0)
    return first if isinstance(first, int) and not isinstance(first, bool) else 0


def stitch_job(payload: Any) -> str | None:
    job = opt_index(payload, 1, 0, 0)
    return job if isinstance(job, str) and job.startswith("projects/") else None


def stitch_state(payload: Any) -> tuple[int | None, str | None]:
    state, data = opt_index(payload, 0), opt_index(payload, 4)
    return (state if isinstance(state, int) else None,
            data if isinstance(data, str) and data else None)


def sent_characters(body: str) -> list[str]:
    """Character ids in a StreamChat request: [session, [[[[prompt]]], media, None, [[id], …]], …]."""
    raw = parse_qs(body).get("f.req", [body])[0]
    try:
        outer = json.loads(raw)
        inner = opt_index(outer, 1)
        message = json.loads(inner) if isinstance(inner, str) else None
    except ValueError:
        return []
    return [c[0] for c in opt_index(message, 1, 3) or []
            if isinstance(c, list) and c and isinstance(c[0], str)]


def linked_image(payload: Any) -> str | None:
    return _str(opt_index(payload, 0, 0))


def character_name(payload: Any) -> str | None:
    return _str(opt_index(payload, 0, 3, 1))
```

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(flow): decode characters, scenes and stitch replies`.

---

### Task 2: Page actions

**Files:**
- Modify: `src/aistudio/_flow/page.py`
- Test: `tests/unit/test_flow_page_d.py` (new)

**Interfaces:**
- Consumes Task 1.
- Produces these on `FlowPage`:
  - `async rename_character(project_id, character_id, name) -> None`;
  - `async create_character_record(project_id, name) -> str`;
  - `async link_photo(project_id, character_id, media_id) -> str` (the new image's media id);
  - `async stitch(clips: list[list], *, timeout_s: float) -> bytes`;
  - `async attach_character(name) -> None`.
- `AgentTurn` gains `characters: tuple[str, ...] = ()`, the ids sent with the request that produced the quote.

- [ ] **Step 1: Write the failing tests**

```python
import base64
import json
import re

import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import (
    AGENT_MESSAGE, CHARACTERS_TAB, INGREDIENTS, PROMPT_EDITOR, FlowPage)
from aistudio.exceptions import BrowserError, ServerError, WaitTimeoutError
from tests.unit.test_flow_page import PID, Clock, chat, permission, stream
from tests.unit.test_flow_rows import batch

JOB = "projects/1/locations/us/jobs/u1"


def rpc(method, payload, status=200):
    return CapturedResponse(method, status, batch(method, payload), "")


async def test_rename_sends_the_field_mask_and_checks_the_name(fake_ui):
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", [[PID, "c1", None, [1, "Boat", []]]])]})
    await FlowPage(ui).rename_character(PID, "c1", "Boat")
    assert ("batch_rpc", "rzMKMb", json.dumps(
        [[PID, "c1", None, [1, "Boat"]], [["entity_info.display_name"]]])) in ui.ops


async def test_a_rename_flow_did_not_apply_is_an_error(fake_ui):
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", [[PID, "c1", None, [1, "Old", []]]])]})
    with pytest.raises(BrowserError):
        await FlowPage(ui).rename_character(PID, "c1", "Boat")


async def test_a_photo_character_is_created_named_then_linked(fake_ui):
    ui = fake_ui({"C4BZMd": [rpc("C4BZMd", [[PID, "c7", None, [1, "Apple", []]]])],
                  "Sc7aEb": [rpc("Sc7aEb", [["i7", PID, "w7"], ["w7"]])]})
    page = FlowPage(ui)
    cid = await page.create_character_record(PID, "Apple")
    assert cid == "c7"
    assert await page.link_photo(PID, cid, "m3") == "i7"
    sent = json.loads(next(op[2] for op in ui.ops if op[:2] == ("batch_rpc", "Sc7aEb")))
    assert sent[0] == "m3" and sent[3] == PID and sent[7] == [None, None, ["c7", [0]]]
    assert all(re.fullmatch(r"[0-9A-F-]{36}", s) for s in sent[9:11])


async def test_a_failed_link_names_the_character(fake_ui):
    ui = fake_ui({"Sc7aEb": [rpc("Sc7aEb", [], status=500)]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).link_photo(PID, "c7", "m3")
    assert "c7" in info.value.message


async def test_stitch_polls_until_done_and_decodes(fake_ui):
    data = b"\x00\x00\x00\x18ftypisom" + b"x" * 50
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [2, "", "", 0]),
                             rpc("IxdSwd", [3, "", "", 3, base64.b64encode(data).decode()])]})
    clips = [["m1", None, [], [8]], ["m2", None, [2], [5]]]
    assert await FlowPage(ui, now=Clock()).stitch(clips, timeout_s=60) == data
    assert ("batch_rpc", "GjQLTd", json.dumps([clips])) in ui.ops
    assert ("batch_rpc", "IxdSwd", json.dumps([[[JOB]]])) in ui.ops


async def test_a_failed_stitch_is_a_server_error(fake_ui):
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [5, "", "", 0])]})
    with pytest.raises(ServerError):
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=60)


async def test_a_slow_stitch_times_out(fake_ui):
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [2, "", "", 0])] * 100})
    with pytest.raises(WaitTimeoutError):
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=5)


async def test_attach_character_uses_the_characters_tab(fake_ui):
    ui = fake_ui()
    await FlowPage(ui).attach_character("Paper boat")
    assert [op for op in ui.ops if op[0] in ("activate", "dispatch")] == [
        ("activate", "button", INGREDIENTS, 0), ("dispatch", "tab", CHARACTERS_TAB.pattern),
        ("dispatch", "option", "Paper boat")]


async def test_the_quote_reports_the_characters_it_was_sent_with(fake_ui):
    message = ["s", [[[["x"]]], None, None, [["c1"]]], ["projects/p"]]
    body = "f.req=" + json.dumps([None, json.dumps(message)])
    response = chat(stream(permission(cost=10)))
    response = CapturedResponse(response.method, response.status, response.text, body)
    ui = fake_ui({"StreamChat": [response]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 video."], AGENT_MESSAGE: ["q"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert turn.characters == ("c1",)
```

(Check the `dispatch` op key FakeUi records for a regex name, `self._key(name)`, before asserting. Use whatever it records for compiled patterns.)

- [ ] **Step 2: Run them.** `uv run pytest tests/unit/test_flow_page_d.py -q`. Expected: FAIL (ImportError: `CHARACTERS_TAB`).

- [ ] **Step 3: Implement in `page.py`**

```python
CHARACTERS_TAB = re.compile(r"Characters$")  # the + panel's tab (mat-list-item role=tab)
STITCH_POLL_MS = 3000  # the page itself polls IxdSwd every 3 s (live)
_STITCH_RUNNING, _STITCH_DONE = 2, 3

    async def rename_character(self, project_id: str, character_id: str, name: str) -> None:
        args = [[project_id, character_id, None, [1, name]], [["entity_info.display_name"]]]
        status, text = await self._ui.batch_rpc("rzMKMb", json.dumps(args))
        got = character_name(decode_batch(text).get("rzMKMb")) if status < 400 else None
        if got != name:
            raise BrowserError(f"Flow did not rename character {character_id}",
                               hint="check its name in Flow; re-run to retry")

    async def create_character_record(self, project_id: str, name: str) -> str:
        args = [[project_id, None, None, [1, name, []]]]
        status, text = await self._ui.batch_rpc("C4BZMd", json.dumps(args))
        cid = character_id(decode_batch(text).get("C4BZMd")) if status < 400 else None
        if cid is None:
            raise BrowserError("Flow did not create the character",
                               hint="re-run; if it persists, the Flow UI changed")
        return cid

    async def link_photo(self, project_id: str, character_id: str, media_id: str) -> str:
        args = [media_id, None, None, project_id, None, None, None,
                [None, None, [character_id, [0]]], None,
                str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()]
        status, text = await self._ui.batch_rpc("Sc7aEb", json.dumps(args))
        image = linked_image(decode_batch(text).get("Sc7aEb")) if status < 400 else None
        if image is None:
            raise BrowserError(f"Flow created character {character_id} but did not add the photo",
                               hint="open it in Flow's Characters to add one, or rename it and "
                                    "use another name; re-running makes another character")
        return image

    async def stitch(self, clips: list[list[Any]], *, timeout_s: float) -> bytes:
        status, text = await self._ui.batch_rpc("GjQLTd", json.dumps([clips]))
        job = stitch_job(decode_batch(text).get("GjQLTd")) if status < 400 else None
        if job is None:
            raise BrowserError("Flow did not start stitching the clips",
                               hint="re-run; if it persists, the Flow UI changed")
        deadline = self._now() + timeout_s
        while self._now() < deadline:
            await self._ui.pause(STITCH_POLL_MS)
            status, text = await self._ui.batch_rpc("IxdSwd", json.dumps([[[job]]]))
            if status >= 400:
                continue
            state, data = stitch_state(decode_batch(text).get("IxdSwd"))
            if state in (None, _STITCH_RUNNING):
                continue
            if state == _STITCH_DONE and data:
                return base64.b64decode(data)
            raise ServerError(f"Flow could not stitch the clips (state {state})",
                              hint="check the clips in Flow; nothing was spent")
        raise WaitTimeoutError("Flow was still stitching when --timeout passed",
                               hint="re-run with a larger --timeout; nothing was spent")

    async def attach_character(self, name: str) -> None:
        await self._ui.activate("button", INGREDIENTS, exact=True)
        await self._ui.pause(1500)
        await self._ui.dispatch_click("tab", CHARACTERS_TAB)
        await self._ui.pause(1500)
        await self._ui.dispatch_click("option", name, exact=True)  # attaches and closes (live)
        await self._ui.pause(800)
```

In `submit_for_quote`, return `AgentTurn(text, quote, tuple(sent_characters(response.request_body)))`.

- [ ] **Step 4: Run** `uv run pytest -q` and `uv run ruff check src tests`. Expected: all pass, ruff clean.
- [ ] **Step 5: Commit** `feat(flow): page actions for names, photos, stitching and attached characters`.

---

### Task 3: Use cases

**Files:**
- Modify: `src/aistudio/_app/flow.py`
- Test: `tests/unit/test_app_flow_d.py` (new; reuses `tests/unit/test_app_flow_b.py`'s `media`, `listed`, `store`, `PID` and builds its own FakePage subclass)

**Interfaces:**
- Consumes Tasks 1–2.
- Produces:
  - `flow_characters(profile, *, project=None, visible=False, browser, page_factory) -> {"characters": [{id, name, media_id, created}], "count", "project"}`;
  - `flow_rename_character(profile, character_id, name, *, project=None, …) -> {"character_id", "name", "project"}`;
  - `flow_character(profile, name, description=None, *, photo=None, i_have_rights=False, max_credits=None, project=None, out=None, overwrite=False, visible=False, browser, downloader, now, page_factory)`, returning `character_id`, `name`, `files`, `credits_spent`, `credits_left` and, for a description, `model`;
  - `flow_scenes(profile, *, project=None, …) -> {"scenes": [{id, title, created, clips: [{media_id, title, seconds}]}], "count", "project"}`;
  - `flow_stitch(profile, clips: list[str], *, project=None, out=None, overwrite=False, timeout_s=300.0, visible=False, browser, now, page_factory) -> {"status": "done", "files": [{path, mime_type, bytes}], "clips": [{media_id, start, end}], "project"}`;
  - `flow_scene_download(profile, scene_id, *, project=None, out=None, overwrite=False, timeout_s=300.0, …)`, same result plus `scene_id`;
  - `flow_generate(..., characters: list[str] | None = None)`.

- [ ] **Step 1: Write the failing tests** (FakePage gets `characters`/`scenes` in `contents`, plus `rename_character`, `create_character_record`, `link_photo`, `stitch`, `attach_character`, `upload`, `media_details`, and a `turn_characters` knob returned in `AgentTurn.characters`):
  - `test_character_list_and_rename`: rename refuses a name another character has, case-insensitively (`"paper BOAT"` vs `"Paper boat"`), with `ValidationError`, before `rename_character` is called. A free name calls `rename_character(PID, "c2", "Boat")`.
  - `test_character_from_a_project_photo`: `flow_character(profile, "Apple", photo=IMG)` calls `create_character_record(PID, "Apple")`, then `link_photo(PID, "c7", IMG)`, then downloads the linked image via `media_details("i7")`. The result has `character_id == "c7"`, `name == "Apple"` and `credits_spent == 0`, and no generation methods are called.
  - `test_character_from_a_local_photo_needs_rights`: without `i_have_rights`, a `ValidationError` arrives before the browser opens. With it, `upload` runs first and its id is linked.
  - `test_a_photo_must_be_a_project_image`: a video id, or an unknown id, gives `ValidationError`/`NotFoundError` before `create_character_record`.
  - `test_description_character_is_named`: `flow_character(profile, "Robot", "a robot")` calls `start_character("a robot")`, then `rename_character(PID, "c1", "Robot")`. A failing rename becomes a warning, and the result is still `done`.
  - `test_name_and_source_rules`: an empty name, both description and photo, or neither, each gives `ValidationError` before the browser opens. An existing name gives `ValidationError` before any spend.
  - `test_generate_attaches_characters_and_checks_the_quote`: `characters=["Paper boat"]` calls `attach_character("Paper boat")`, and the submitted text contains `"Feature the attached characters: Paper boat."`. With `turn_characters=("c1",)` it returns the quote. With `turn_characters=()` it calls `reject` and raises `PlanMismatchError`.
  - `test_character_lookup_rules`: an id works; an ambiguous name (two "Untitled character") gives `ValidationError` with "rename" in the hint; a character without an image gives `ValidationError`; an unknown one gives `NotFoundError`. Nothing is submitted in any of these.
  - `test_stitch_checks_clips_then_writes_the_mp4`: `["m1", "m2@2-5"]` sends `[["m1", None, [], [8]], ["m2", None, [2], [5]]]` and writes the stub bytes to `-o`. `"m2@5-9"` (past 8 s), `"m2@5-5"`, an image id, a running clip, or a single clip each gives `ValidationError` before `stitch`. An existing `-o` without `overwrite` is refused before the browser opens.
  - `test_scene_download_uses_the_scenes_clips`: scene `s1` sends its clips in position order with their own trims. An unknown scene gives `NotFoundError`.
  - `test_extend_reports_the_scene_holding_the_new_clip`: contents after the job show scene `s9` holding `m2`, so `scene_id == "s9"` even though `last_scene_id == "s1"`. If `contents` raises after the job, `scene_id` falls back to `"s1"` and the result is still `done`.

- [ ] **Step 2: Run them.** `uv run pytest tests/unit/test_app_flow_d.py -q`. Expected: FAIL (ImportError: `flow_characters`).

- [ ] **Step 3: Implement.** Helpers:

```python
_CLIP = re.compile(r"^(?P<id>[0-9a-f-]{36})(?:@(?P<start>\d+)-(?P<end>\d+))?$")
_NAME_MAX = 100


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > _NAME_MAX:
        raise ValidationError(f"a character name needs 1 to {_NAME_MAX} characters")
    return name


def _free_name(contents, name: str, *, keep: str | None = None) -> None:
    taken = [c for c in contents.characters
             if c.name.casefold() == name.casefold() and c.id != keep]
    if taken:
        raise ValidationError(f"character {taken[0].id} is already named {taken[0].name!r}",
                              hint="choose another name; --character picks characters by name")


def _characters(contents, wanted: list[str]) -> list[FlowCharacter]:
    out = []
    for value in wanted:
        value = value.strip()
        by_id = [c for c in contents.characters if c.id == value]
        found = by_id or [c for c in contents.characters if c.name.casefold() == value.casefold()]
        if not found:
            raise NotFoundError(f"no character {value!r} in this Flow project",
                                hint='see "aistudio flow character list"; nothing was sent')
        same_name = [c for c in contents.characters
                     if c.name.casefold() == found[0].name.casefold()]
        if len(same_name) > 1:
            raise ValidationError(
                f"{len(same_name)} characters are named {found[0].name!r}",
                hint=f'Flow attaches characters by name: rename one with "aistudio flow '
                     f'character rename {found[0].id} NEW-NAME"; nothing was sent')
        if found[0].media_id is None:
            raise ValidationError(f"character {found[0].name!r} has no image yet",
                                  hint="finish it in Flow first; nothing was sent")
        out.append(found[0])
    return out


def _clips(contents, values: list[str]) -> list[list[Any]]:
    if len(values) < 2:
        raise ValidationError("stitching needs at least two clips")
    by_id = {m.media_id: m for m in contents.media}
    out = []
    for value in values:
        match = _CLIP.match(value.strip())
        if match is None:
            raise ValidationError(f"{value!r} is not MEDIA-ID or MEDIA-ID@START-END",
                                  hint="START and END are whole seconds, e.g. …@2-5")
        media = by_id.get(match["id"])
        if media is None:
            raise NotFoundError(f"no media {match['id']} in this Flow project",
                                hint='see "aistudio flow media --type video"')
        if _media_kind(media) != "video" or media.status != 3 or not media.seconds:
            raise ValidationError(f"media {match['id']} is not a finished video")
        start = int(match["start"]) if match["start"] else 0
        end = int(match["end"]) if match["end"] else media.seconds
        if not 0 <= start < end <= media.seconds:
            raise ValidationError(f"{value}: the trim must be within 0-{media.seconds} seconds "
                                  f"and START < END")
        out.append([media.media_id, None, [start] if start else [], [end]])
    return out
```

Then:
- `flow_generate` loads contents once when there are attachments **or** characters. `_attachment_titles` becomes a pure function of `(contents, attachments)`, so its callers pass the contents.
- `flow_generate` attaches each resolved character after the media attachments.
- It prefixes `Feature the attached characters: A, B.`
- Right after a quote, if `set(turn.characters) != {c.id for c in chosen}`, it rejects and raises `PlanMismatchError("Flow's request carries characters … not …", hint="nothing was spent; re-run")`.

`flow_character`:
- Validates the name and exactly one of description or photo before the browser opens. For a local photo it uses `_attachments(None, None, [photo], i_have_rights)` for the rights rule.
- Inside the browser:
  1. Read `before` credits.
  2. Open the project and load its contents.
  3. Check the name is free with `_free_name`.
  4. **Photo path:** upload a local file (`known_ids` from contents), or check that the media id is a finished image in the contents. Then `create_character_record` and `link_photo`. Get links with `media_details(image) or FlowMedia(...)`, then read `after` credits. The price is 0, and `_cost_warning` reports any change.
  5. **Description path:** the existing flow, then `rename_character` inside `try`. A failure becomes the warning `named "Untitled character" in Flow; rename it with …`.

`flow_stitch` and `flow_scene_download`:
- Run `_check_output(out, 1, overwrite)` before the browser opens.
- Open the project, load its contents, build the clips (`_clips`, or the scene's clips with `[start] if start else []`, `[end]`), then call `page.stitch(clips, timeout_s=…)`.
- Write with `write_file(out or free_name(Path(f"flow-scene-{stamp:%Y%m%d-%H%M%S}.mp4")), data, overwrite=overwrite)`. Return `mime_type` `video/mp4` from `_type_of("video", data)`.

`flow_extend`, after `_credits_after`, tries `page.contents(project_id)` and takes the scene whose clips include the new media id. Any exception keeps `page.last_scene_id`.

- [ ] **Step 4: Run** `uv run pytest -q` and ruff. Expected: all pass.
- [ ] **Step 5: Commit** `feat(flow): character names, photos, lists, --character and scene stitching`.

---

### Task 4: CLI

**Files:**
- Modify: `src/aistudio/cli/flow_cmd.py`
- Test: `tests/cli/test_flow_cmd.py`

**Interfaces:**
- Consumes Task 3.
- Produces:
  - `flow character create NAME [DESCRIPTION] [--photo FILE|media-id] [--i-have-rights] [--max-credits N] [--project P] [-o PATH] [--force] [--visible] [--json]`;
  - `flow character list [--project] [--visible] [--json]`;
  - `flow character rename CHARACTER_ID NAME [--project] [--visible] [--json]`;
  - `flow scene list [--project] [--visible] [--json]`;
  - `flow scene stitch CLIP... [-o] [--force] [--timeout 300] [--project] [--visible] [--json]`;
  - `flow scene download SCENE_ID [-o] [--force] [--timeout 300] [--project] [--visible] [--json]`;
  - `--character` (multiple) on `flow video` and `flow image`.

- [ ] **Step 1: Write the failing tests** (monkeypatch the use cases as the existing tests do):
  - `test_character_create_passes_name_photo_and_rights`: `["flow", "character", "create", "Apple", "--photo", "m3", "--i-have-rights", "--json"]` calls `flow_character(profile, "Apple", None, photo="m3", i_have_rights=True, max_credits=None, project=None, out=None, overwrite=False, visible=False)`.
  - `test_character_list_and_rename`: human output lists `id  name`, with `(no image)` for a character that has none. `rename c1 "Paper boat"` passes `("c1", "Paper boat")`.
  - `test_scene_commands`: `scene stitch m1 m2@2-5 -o x.mp4 --timeout 120` passes `(["m1", "m2@2-5"], …, timeout_s=120.0)`. `scene download s1`, `scene list`.
  - `test_video_passes_characters`: `--character "Paper boat" --character c2` gives `characters=["Paper boat", "c2"]`. Update `test_flow_video_passes_every_option`'s expected kwargs with `"characters": []`.
- [ ] **Step 2: Run them.** Expected: FAIL (no such command or option).
- [ ] **Step 3: Implement.** Follow the existing command style and reuse `_finish` and `_render_files`. Render lists as aligned lines, and a stitch as `Wrote PATH (N bytes)`.
- [ ] **Step 4: Run** `uv run pytest -q` and ruff. Expected: all pass.
- [ ] **Step 5: Commit** `feat(cli): flow character list/rename/photo, scene list/stitch/download, --character`.

---

### Task 5: Docs, skill and live checks

**Files:**
- `README.md` (Flow section);
- `integrations/claude-code/aistudio/SKILL.md`, copied to `~/.claude/skills/aistudio/SKILL.md`;
- `tests/live/test_live_flow.py`.

- [ ] **Step 1: Docs.**
  - **README:** the new commands with one example each.
  - **SKILL.md:**
    - characters are picked by unique name or id;
    - `flow character create NAME DESCRIPTION|--photo`;
    - a photo from a local file needs the user's rights confirmation (`--i-have-rights`, ask first);
    - `scene stitch`/`scene download` are free;
    - after `flow extend`, `flow scene download <scene_id>` gives the joined video;
    - a `PLAN_MISMATCH` about characters means re-run, and nothing was spent.
- [ ] **Step 2: Live tests.**
  - **Marked `live` (free, read-only or quote-only):**
    - `flow character list` returns at least one character with an image;
    - `flow scene list` returns at least one scene;
    - `flow scene stitch` of two finished Veo clips writes an MP4 longer than its first clip, with credits unchanged;
    - a quote with `--character <a uniquely named character>` is `quoted`, `declined`, and has no mismatch.
  - **Marked `live_slow` (writes a new character, free):** `flow character create "<unique name>" --photo <a project image id>`.
- [ ] **Step 3: Run the live checks.** When the profile is idle, run `uv run pytest -m live tests/live/test_live_flow.py -q` once, plus the photo-character `live_slow` test once. Rename one test character so the `--character` quote test has a unique name.
  - Expected: all pass, and credits are unchanged throughout.
- [ ] **Step 4: Commit** `docs(flow): plan D commands in README and skill; live checks`.
