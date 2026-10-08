# Flow Plan B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add these Flow features:
- `aistudio flow projects`, `flow media` and `flow download`;
- local uploads (`--i-have-rights`);
- frames and ingredients on `flow video` and `flow image`;
- `flow character create`;
- `flow extend`.

**Architecture:** This plan extends the Flow layers from plan A:
- `_flow/rows.py` gains decoders for projects (`UpteDb`), project contents (`Zzl0ze`), characters (`C4BZMd`/`ogiZ0b`) and extend (`fZytfe`).
- `_flow/models.py` gains the measured price table for direct-spend actions.
- `FlowPage` gains list, upload, attach, character and extend drivers.
- `_app/flow.py` gains the use cases, and `cli/flow_cmd.py` the commands.

**Tech Stack:** Python 3.10+, click, httpx, Playwright, pytest + pytest-asyncio, respx, ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md`, including **§10 Amendments**. The evidence is in `docs/wire/findings.md`, in the Flow sections dated 2026-10-07 and 2026-10-08.

**Not in this plan** (unmapped, so they are rulings rather than guesses): a character NAME and `--photo`, `flow character list`, `--character` on video/image, and `flow scene`. Each needs its own discovery first.

## Global Constraints

- No Gemini API key. Flow runs on the saved Google session in a minimized Chrome; `--visible` shows it, maximized and fitted to the screen.
- **Quoted actions** (video/image) keep plan A's rule: spend only via `decide()` → APPROVE.
- **Direct-spend actions** (character, extend) are refused unless the price table knows the action+model and `--max-credits` ≥ that price (a price of 0 needs no flag). Credits are read before and after, and a difference is a warning.
- **Uploads:** Flow's "Rights to use this image" dialog is accepted only with `--i-have-rights`. Without it, the command stops before uploading.
- Writes go only to the "aistudio" project or `--project`. A paid result is never lost. No tokens, cookies, XSRF values or signatures appear in output or fixtures.
- Errors after a spend name the media and project and say not to re-run (plan A's `_spent`).
- ruff is clean, and the suite is green after every task. Live tests that cost credits are marked `live_slow` and run only with the user's OK.

## Review Focus

1. **An unmeasured direct-spend action** (extend on a Veo 3.1 Fast or Omni clip). Expected: refused before any click, with a hint naming the price table. Test: Task 2 `test_unpriced_actions_are_refused`.
2. **A local file passed without `--i-have-rights`.** Expected: refused before Chrome opens; Flow's dialog is never accepted. Test: Task 5 `test_local_files_need_i_have_rights`.
3. **Extend's real cost differs from the table.** Expected: a done result plus a warning with both numbers. Test: Task 5 `test_a_cost_different_from_the_table_is_a_warning`.
4. **An attachment media id that is not in the project.** Expected: NOT_FOUND before submitting, nothing spent. Test: Task 5 `test_unknown_attachment_ids_fail_before_submitting`.
5. **`flow download` of a media id without a link** (still running, or failed). Expected: a clear error that the media has no download yet. Test: Task 5 `test_download_needs_a_finished_media`.

---

### Task 1: Decoders for lists, characters and extend

**Files:** Modify `src/aistudio/_flow/rows.py`. Test: `tests/unit/test_flow_rows_b.py`

**Interfaces:**
- Produces:
  - `FlowProject(id, title, created)`;
  - `decode_projects(payload) -> list[FlowProject]` (`UpteDb`);
  - `ProjectContents(titles: dict[str, str], media: list[FlowMedia])`, where `titles` maps media_id to its workflow title;
  - `decode_contents(payload) -> ProjectContents` (`Zzl0ze`);
  - `character_id(payload) -> str | None` (`C4BZMd`);
  - `extend_started(payload) -> tuple[int | None, list[FlowMedia]]` (`fZytfe`: credits left, new media).
- `FlowMedia` is unchanged. Character images come back through `media_records("ogiZ0b", payload)`, which is extended to read `payload[0]` as a list of records.

Shapes (findings, 2026-10-08):
- `UpteDb`: `[[ [pid, [title, null, [created_s, ns], url?, media_id?]], … ]]`.
- `Zzl0ze`: `[_, [[workflow_id, null, null, [title, [created], null, null, media_id, …], pid], …], [media records], [voices]]`.
- `C4BZMd`: `[[pid, character_id, null, [1, "Untitled character", []], …]]`.
- `ogiZ0b`: `[[[media_id, null, workflow_id, …, [[…, "<url>", 3, …]], null, [w, h]]], …]`, a record carrying an image link.
- `fZytfe`: `[null, credits_left, [[workflow…]], [[media record]]]`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_flow_rows_b.py
from aistudio._flow.rows import (
    FlowProject,
    character_id,
    decode_contents,
    decode_projects,
    extend_started,
    media_records,
)
from tests.unit.test_flow_rows import CREATED, MEDIA, PROJECT, record

IMAGE = "https://flow-content.google/image/i1?Signature=s"


def test_projects_with_titles():
    payload = [[["p1", ["aistudio", None, [1791371089, 0], "https://x/y", "m1"]],
                ["p2", ["Oct 07 - 13:57", None, [1791356229, 0]]], ["bad"]]]
    assert decode_projects(payload) == [FlowProject("p1", "aistudio", 1791371089),
                                        FlowProject("p2", "Oct 07 - 13:57", 1791356229)]
    assert decode_projects(None) == []


def test_contents_map_media_to_titles():
    workflow = ["w1", None, None, ["Red apple rolling on table", [CREATED, 0], None, None, MEDIA],
                PROJECT]
    contents = decode_contents([None, [workflow], [record()], []])
    assert contents.titles == {MEDIA: "Red apple rolling on table"}
    assert [m.media_id for m in contents.media] == [MEDIA]
    assert decode_contents(None).media == []


def test_character_id_and_image():
    assert character_id([[PROJECT, "c1", None, [1, "Untitled character", []]]]) == "c1"
    assert character_id(None) is None
    image = ["i1", None, "w1", None, None, [[CREATED, 0], "a robot", None, None, None, None,
                                            None, None, [3], 1, IMAGE]]
    (media,) = media_records("ogiZ0b", [[image]])
    assert media.image_url == IMAGE and media.status == 3


def test_extend_started_reads_credits_and_new_media():
    credits, media = extend_started([None, 1020, [["w2"]], [record(media_id="m2")]])
    assert credits == 1020 and [m.media_id for m in media] == ["m2"]
    assert extend_started(None) == (None, [])
```

- [ ] **Step 2:** Run `uv run pytest tests/unit/test_flow_rows_b.py -q`. Expected: an ImportError.
- [ ] **Step 3: Implement** in `rows.py`:

```python
@dataclass(frozen=True)
class FlowProject:
    id: str
    title: str | None
    created: int | None


@dataclass(frozen=True)
class ProjectContents:
    titles: dict[str, str]  # media_id -> its workflow title (what the grid shows)
    media: list[FlowMedia]


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
    for workflow in opt_index(payload, 1) or []:
        title, media_id = opt_index(workflow, 3, 0), opt_index(workflow, 3, 4)
        if isinstance(title, str) and isinstance(media_id, str):
            titles[media_id] = title
    media = [m for m in (_record(r) for r in opt_index(payload, 2) or []) if m is not None]
    return ProjectContents(titles, media)


def character_id(payload: Any) -> str | None:
    return _str(opt_index(payload, 0, 1))


def extend_started(payload: Any) -> tuple[int | None, list[FlowMedia]]:
    credits = opt_index(payload, 1)
    media = [m for m in (_record(r) for r in opt_index(payload, 3) or []) if m is not None]
    return (credits if isinstance(credits, int) and not isinstance(credits, bool) else None,
            media)
```

  In `media_records`, add `"ogiZ0b"` to the accepted methods. Its rows are `opt_index(payload, 0)` when that is a list of lists.
- [ ] **Step 4:** Run the test file. Expected: PASS. **Step 5:** Commit with message `feat: decode Flow projects, project contents, characters and extend results`.

### Task 2: Price table and the direct-spend check

**Files:** Modify `src/aistudio/_flow/models.py`. Test: `tests/unit/test_flow_models.py`

**Interfaces:**
- Produces:
  - `PRICES: dict[tuple[str, str], int]`, keyed by (action, model label) and filled from the findings: `("character", "Nano Banana 2.1"): 0`, `("extend", "Veo 3.1 - Lite"): 10`;
  - `check_direct_spend(action: str, model_label: str, max_credits: int | None) -> int`, which returns the price or raises `ValidationError` (unpriced) or `OverBudgetError`.

- [ ] **Step 1: Failing tests**

```python
def test_priced_actions_need_a_budget_that_covers_them():
    from aistudio._flow.models import check_direct_spend
    from aistudio.exceptions import OverBudgetError

    assert check_direct_spend("character", "Nano Banana 2.1", None) == 0
    assert check_direct_spend("extend", "Veo 3.1 - Lite", 10) == 10
    with pytest.raises(OverBudgetError) as info:
        check_direct_spend("extend", "Veo 3.1 - Lite", None)
    assert "--max-credits 10" in info.value.hint
    with pytest.raises(OverBudgetError):
        check_direct_spend("extend", "Veo 3.1 - Lite", 9)


def test_unpriced_actions_are_refused():
    from aistudio._flow.models import check_direct_spend

    with pytest.raises(ValidationError) as info:
        check_direct_spend("extend", "Veo 3.1 - Fast", 100)
    assert "price" in info.value.message
```

- [ ] **Step 2:** Run. Expected: an ImportError.
- [ ] **Step 3: Implement**

```python
# Direct-spend actions: Flow charges without a quote, so aistudio keeps measured prices
# (docs/wire/findings.md, "measured costs"). Add a row only after measuring it.
PRICES = {("character", "Nano Banana 2.1"): 0, ("extend", "Veo 3.1 - Lite"): 10}


def check_direct_spend(action: str, model_label: str, max_credits: int | None) -> int:
    price = PRICES.get((action, model_label))
    if price is None:
        raise ValidationError(f"aistudio has no measured price for {action} with {model_label}",
                              hint="Flow does not quote this action; it needs a measured price "
                                   "in aistudio's table before it can be used")
    if price > 0 and (max_credits is None or max_credits < price):
        raise OverBudgetError(f"{action} with {model_label} costs {price} credits",
                              hint=f"nothing was spent; pass --max-credits {price} to accept")
    return price
```

  (Import `OverBudgetError`.)
- [ ] **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `feat: measured price table for Flow's direct-spend actions`.

### Task 3: FlowPage: lists, upload and attach

**Files:** Modify `src/aistudio/_flow/page.py`. Test: `tests/unit/test_flow_page_b.py`

**Interfaces:**
- Consumes: Task 1 decoders and plan A's `FlowPage` internals.
- Produces:
  - `FlowPage.projects() -> list[FlowProject]`, which loads home and captures `UpteDb`;
  - `FlowPage.contents(project_id) -> ProjectContents`, which opens the project and captures `Zzl0ze`;
  - `FlowPage.upload(path: Path) -> str`, the new media id, which accepts the rights dialog (the caller has already checked `--i-have-rights`);
  - `FlowPage.attach(title: str) -> None`, using the tile ⋮ → "Add to prompt".

Selectors (findings):
- Upload:
  1. Activate the button "Add ingredients to the prompt box".
  2. **Dispatch** `click` on the button `/Upload media/`, inside `responses(FLOW_RPC)`.
  3. Use Playwright's file chooser.
  4. Dispatch `click` on the button "I agree".
  5. Wait for `maseQ`, then the first `as29s` record created after the upload started.
- The Ui needs `dispatch_click(role, name)` and `choose_file(role, name, path)`. Add both to `Ui`, `PlaywrightUi` and `FakeUi`:
  - `choose_file` wraps `page.expect_file_chooser()` around a dispatched click and calls `set_files`;
  - `FakeUi` records `("choose_file", role, key, str(path))` and `("dispatch", role, key)`.
- Attach:
  1. Hover the grid tile whose text is the title (`Ui.hover_text(text)`).
  2. Dispatch `click` on the `More options` button nearest to it (`Ui.dispatch_click_near(role, name, text)`).
  3. Dispatch `click` on the menuitem `/Add to prompt/`.

  `FakeUi` records `("hover", text)` and `("dispatch_near", role, key, text)`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_flow_page_b.py
import json
from pathlib import Path

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import FlowPage
from tests.unit.test_flow_page import PID, Clock, poll
from tests.unit.test_flow_rows import CREATED, MEDIA, batch, record


async def test_projects_come_from_the_home_page_list(fake_ui):
    payload = [[["p1", ["aistudio", None, [1, 0]]]]]
    ui = fake_ui({"UpteDb": [CapturedResponse("UpteDb", 200, batch("UpteDb", payload), "")]})
    (project,) = await FlowPage(ui, now=Clock()).projects()
    assert (project.id, project.title) == ("p1", "aistudio")


async def test_contents_open_the_project(fake_ui):
    workflow = ["w1", None, None, ["Red apple", [CREATED, 0], None, None, MEDIA], PID]
    ui = fake_ui({"Zzl0ze": [CapturedResponse("Zzl0ze", 200,
                                              batch("Zzl0ze", [None, [workflow], [record()], []]),
                                              "")]},
                 url=f"https://flow.google.com/project/{PID}")
    contents = await FlowPage(ui, now=Clock()).contents(PID)
    assert contents.titles == {MEDIA: "Red apple"}


async def test_upload_accepts_the_rights_dialog_and_returns_the_new_media(fake_ui, tmp_path):
    image = tmp_path / "apple.jpg"
    image.write_bytes(b"\xff\xd8x")
    ui = fake_ui({"maseQ": [CapturedResponse("maseQ", 200, ")]}'", "")],
                  "as29s": [poll("as29s", record(media_id="new1"))]})
    media_id = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).upload(image)
    assert media_id == "new1"
    assert ("choose_file", "button", "Upload media", str(image)) in ui.ops
    assert ("dispatch", "button", "I agree") in ui.ops


async def test_attach_uses_the_tiles_add_to_prompt(fake_ui):
    ui = fake_ui()
    await FlowPage(ui).attach("Red apple")
    assert ui.ops[:3] == [("hover", "Red apple"),
                          ("dispatch_near", "button", "More options", "Red apple"),
                          ("dispatch", "menuitem", "Add to prompt")]
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Ui additions** (PlaywrightUi):
    - `dispatch_click(role, name)` calls `locator.first.dispatch_event("click")` through `_act`.
    - `choose_file(role, name, path)` does `async with page.expect_file_chooser(timeout=10000) as fc: await locator.first.dispatch_event("click")`, then `(await fc.value).set_files(str(path))`, through `_act`.
    - `hover_text(text)` moves the mouse onto `get_by_text(text).first`'s bounding box: x+20, y−100, i.e. the tile image above its caption.
    - `dispatch_click_near(role, name, text)` dispatches a click on the match whose bounding box is closest to that text.
  - **FakeUi:** record the ops listed above.
  - **FlowPage:**

```python
    async def projects(self) -> list[FlowProject]:
        async with self._ui.responses(marker=FLOW_RPC) as log:
            await self._ui.goto(HOME_URL)
            response = await log.next("UpteDb", timeout_s=30.0)
        if response is None:
            raise BrowserError("Flow did not list the projects",
                               hint="re-run; if it persists, the Flow UI changed")
        return decode_projects(decode_batch(response.text).get("UpteDb"))

    async def contents(self, project_id: str) -> ProjectContents:
        async with self._ui.responses(marker=FLOW_RPC) as log:
            await self.open_project(project_id)
            response = await log.next("Zzl0ze", timeout_s=30.0)
        if response is None:
            raise BrowserError("Flow did not list the project's media",
                               hint="re-run; if it persists, the Flow UI changed")
        return decode_contents(decode_batch(response.text).get("Zzl0ze"))

    async def upload(self, path: Path) -> str:
        """Upload a local file (the caller has the person's --i-have-rights)."""
        await self._ui.activate("button", INGREDIENTS, exact=True)
        await self._ui.pause(1500)
        async with self._ui.responses(marker=FLOW_RPC) as log:
            since = self._clock()
            await self._ui.choose_file("button", UPLOAD_MEDIA, path)
            await self._ui.pause(1500)
            await self._ui.dispatch_click("button", AGREE)
            if await log.next("maseQ", timeout_s=120.0) is None:
                raise BrowserError(f"Flow did not upload {path.name}",
                                   hint="re-run; if it persists, the Flow UI changed")
            deadline = self._now() + 60.0
            while self._now() < deadline:
                response = await log.next("as29s", timeout_s=self._poll_s)
                if response is None:
                    continue
                for media in media_records("as29s", decode_batch(response.text).get("as29s")):
                    if media.created is not None and media.created >= since - CREATED_SLACK_S:
                        return media.media_id
        raise BrowserError(f"Flow did not report the uploaded {path.name}",
                           hint="check the project's Uploads in Flow")

    async def attach(self, title: str) -> None:
        await self._ui.hover_text(title)
        await self._ui.dispatch_click_near("button", MORE, title)
        await self._ui.pause(800)
        await self._ui.dispatch_click("menuitem", ADD_TO_PROMPT)
        await self._ui.pause(800)
```

  Constants: `INGREDIENTS = "Add ingredients to the prompt box"`, `UPLOAD_MEDIA = "Upload media"`, `AGREE = "I agree"`, `MORE = "More options"`, `ADD_TO_PROMPT = "Add to prompt"`. Note that `FakeUi` keys names verbatim, so the tests use the plain strings.
- [ ] **Step 4:** Run the test file and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: Flow page lists projects and media, uploads with consent, attaches media`.

### Task 4: FlowPage: create a character and extend a clip

**Files:** Modify `src/aistudio/_flow/page.py`. Test: `tests/unit/test_flow_page_b.py`

**Interfaces:**
- Produces:
  - `CharacterOutcome(character_id: str | None, media: list[FlowMedia])`;
  - `FlowPage.create_character(project_id, description) -> CharacterOutcome`;
  - `FlowPage.extend_label(title) -> str`, the model label shown in "Extend (…)";
  - `FlowPage.extend(title, prompt, *, timeout_s) -> FlowOutcome`.

Selectors (findings):
- **Character:**
  1. `goto /project/<id>/character`.
  2. `type_text("[contenteditable=true]", description)`.
  3. A real `click("button", "Start generation", exact=True)` inside `responses(FLOW_RPC)`.
  4. Read `C4BZMd` (the id), then `ogiZ0b` (the image record).
- **Extend:**
  1. `open_project`.
  2. `click_text(title)`, a real mouse click on the tile; add `Ui.click_text`.
  3. Wait for `wait_for("[contenteditable=true]")`.
  4. Dispatch a click on `Add clip`, then read the label of the menuitem `/^Extend/` (`inner_text`).
  5. Dispatch a click on that menuitem.
  6. Type into the last editor: `type_text("[contenteditable=true] >> nth=-1", prompt)`.
  7. A real `click("button", "Start generation", exact=True, nth=-1)`.
  8. Read `fZytfe` (credits and new media), then follow the new media with plan A's `_follow` rules (created after start, status 3 with a video link).

- [ ] **Step 1: Failing tests**

```python
async def test_create_character_returns_its_id_and_image(fake_ui):
    image = ["i1", None, "w1", None, None, [[CREATED, 0], "a robot", None, None, None, None,
                                            None, None, [3], 1,
                                            "https://flow-content.google/image/i1?S=s"]]
    ui = fake_ui({"C4BZMd": [CapturedResponse("C4BZMd", 200, batch(
                      "C4BZMd", [[PID, "c1", None, [1, "Untitled character", []]]]), "")],
                  "ogiZ0b": [CapturedResponse("ogiZ0b", 200, batch("ogiZ0b", [[image]]), "")]})
    outcome = await FlowPage(ui, now=Clock()).create_character(PID, "a robot")
    assert outcome.character_id == "c1" and outcome.media[0].image_url
    assert ("type", "[contenteditable=true]", "a robot") in ui.ops


async def test_extend_reads_the_model_and_follows_the_new_clip(fake_ui):
    started = [None, 1020, [["w2"]], [record(status=2, with_links=False, media_id="m2")]]
    ui = fake_ui({"fZytfe": [CapturedResponse("fZytfe", 200, batch("fZytfe", started), "")],
                  "as29s": [poll("as29s", record(media_id="m2"))]},
                 inner={("menuitem", "^Extend"): ["keyboard_double_arrow_right Extend (Veo 3.1 - "
                                                  "Lite)"]},
                 url=f"https://flow.google.com/project/{PID}")
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    await page.open_project(PID)
    assert await page.extend_label("Red apple") == "Veo 3.1 - Lite"
    outcome = await page.extend("Red apple", "rolls off", timeout_s=60)
    assert [m.media_id for m in outcome.media] == ["m2"] and outcome.credits_left == 1020
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Ui:** `click_text(text)` performs a real `page.mouse.click` at the tile image above `get_by_text(text).first`, through `_act`. `click(..., nth=-1)` already works through `locator.nth(-1)`. `FakeUi.click_text` records `("click_text", text)`.
  - **FlowPage:**

```python
    async def create_character(self, project_id: str, description: str) -> CharacterOutcome:
        await self._ui.goto(f"{PROJECT_URL.format(id=project_id)}/character")
        await self._ui.wait_for(EDITOR_ANY, timeout_s=30.0)
        await self._ui.type_text(EDITOR_ANY, description)
        async with self._ui.responses(marker=FLOW_RPC) as log:
            await self._ui.click("button", START, exact=True)
            created = await log.next("C4BZMd", timeout_s=60.0)
            image = await log.next("ogiZ0b", timeout_s=180.0)
        if image is None:
            raise BrowserError("Flow did not make the character image",
                               hint="check the project's Characters in Flow")
        return CharacterOutcome(
            character_id(decode_batch(created.text).get("C4BZMd")) if created else None,
            media_records("ogiZ0b", decode_batch(image.text).get("ogiZ0b")))

    async def extend_label(self, title: str) -> str:
        await self._ui.click_text(title)
        await self._ui.wait_for(EDITOR_ANY, timeout_s=30.0)
        await self._ui.dispatch_click("button", ADD_CLIP)
        await self._ui.pause(1200)
        text = await self._ui.inner_text("menuitem", EXTEND)
        match = re.search(r"Extend \((.+)\)", text)
        if match is None:
            raise BrowserError("Flow offered no Extend for this clip",
                               hint="only Veo clips can be extended")
        return match.group(1).strip()

    async def extend(self, title: str, prompt: str, *, timeout_s: float) -> FlowOutcome:
        """Call after extend_label (the Add clip menu is open). Spends credits."""
        await self._ui.dispatch_click("menuitem", EXTEND)
        await self._ui.pause(1500)
        await self._ui.type_text(EDITOR_LAST, prompt)
        async with self._ui.responses(marker=FLOW_RPC) as log:
            since = self._clock()
            await self._ui.click("button", START, exact=True, nth=-1)
            started = await log.next("fZytfe", timeout_s=60.0)
            if started is None:
                raise BrowserError("Flow did not start the extension; it may still be charged",
                                   hint="check the project in Flow before re-running")
            credits, media = extend_started(decode_batch(started.text).get("fZytfe"))
            self.paid_media = [m.media_id for m in media]
            outcome = await self._follow(log, 1, timeout_s, "video", since)
        return FlowOutcome(outcome.media, credits if outcome.credits_left is None
                           else outcome.credits_left, outcome.timed_out)
```

  Constants: `EDITOR_ANY = "[contenteditable=true]"`, `EDITOR_LAST = "[contenteditable=true] >> nth=-1"`, `START = "Start generation"`, `ADD_CLIP = "Add clip"`, `EXTEND = re.compile(r"^Extend")`. The `FakeUi` `inner` key for `EXTEND` is its pattern, `"^Extend"`.
- [ ] **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `feat: Flow page creates characters and extends clips`.

### Task 5: Use cases: projects, media, download, attachments, character, extend

**Files:** Modify `src/aistudio/_app/flow.py`. Test: `tests/unit/test_app_flow_b.py`

**Interfaces:**
- Produces:
  - `flow_projects(profile, *, browser, page_factory) -> dict`;
  - `flow_media(profile, *, project=None, kind=None, …) -> dict`;
  - `flow_download(profile, media_id, *, project=None, out=None, overwrite=False, …) -> dict`;
  - `flow_character(profile, description, *, max_credits=None, project=None, out=None, …) -> dict`;
  - `flow_extend(profile, media_id, prompt, *, max_credits=None, project=None, out=None, timeout_s=600.0, …) -> dict`;
  - `flow_generate` gains `start_frame`, `end_frame` and `ingredients: list[str]` (each a media id or a local path), plus `i_have_rights: bool`.
- Rules:
  - Local paths need `i_have_rights` (`ValidationError` before Chrome otherwise) and must be existing image files.
  - Media ids must be in the project's contents (`NotFoundError` before submitting).
  - The instruction adds "Use the attached image(s) as: start frame, end frame, ingredients" in attach order.
  - `flow_extend`:
    1. `contents` gives the title of `media_id`.
    2. `extend_label` gives the model.
    3. `check_direct_spend("extend", label, max_credits)` runs (a refusal raises before spending).
    4. Credits are read before.
    5. `extend` runs.
    6. Credits are read after, and a difference from the price becomes a warning.
    7. The result is downloaded. Errors after the click use `_spent`.
  - `flow_character` follows the same pattern with `check_direct_spend("character", "Nano Banana 2.1", …)`.

- [ ] **Step 1: Failing tests.** Reuse plan A's `FakePage` pattern, with new scripted methods: `contents`, `projects`, `upload`, `attach`, `create_character`, `extend_label`, `extend` and `credits` (returning a scripted sequence).
  - `test_projects_and_media_lists`;
  - `test_download_needs_a_finished_media`;
  - `test_local_files_need_i_have_rights` (`opened == []`);
  - `test_unknown_attachment_ids_fail_before_submitting`;
  - `test_attachments_are_uploaded_attached_and_named_in_order`: the ops show upload → attach(title of the new id) → the submit text includes "start frame";
  - `test_extend_checks_the_price_before_clicking`: no budget gives `OVER_BUDGET`, and `extend` is never called;
  - `test_a_cost_different_from_the_table_is_a_warning`: credits 1030 → 1018, giving the warning "Flow charged 12 credits; aistudio's table says 10";
  - `test_character_is_free_and_downloads_its_image`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement** the use cases. They follow `flow_generate`'s structure: inputs checked before Chrome, `_open` for the project, `_save_all` (with `kind="image"` for characters) and `_spent` after a click that spends. `flow_download` finds the media in `contents`; if it has no link for its kind, it raises `ValidationError("media … has no download yet (still running or failed)")`, and otherwise saves via `_save_all`.
- [ ] **Step 4:** Run the test file and the suite. Expected: PASS. **Step 5:** Commit with message `feat: Flow projects, media, download, attachments, characters and extend use cases`.

### Task 6: CLI, skill, README, findings, live tests

**Files:** `src/aistudio/cli/flow_cmd.py`, `tests/cli/test_flow_cmd.py`, `integrations/claude-code/aistudio/SKILL.md` (then copy to `~/.claude/skills/aistudio/`), `README.md`, `tests/live/test_live_flow.py`

- [ ] **Step 1: Failing CLI tests:**
  - `flow projects --json` and `flow media --project p --type video --json` pass their kwargs;
  - `flow download m1 -o x.mp4 --json`;
  - `flow video "x" --start-frame a.jpg --ingredient m1 --i-have-rights` passes `start_frame`, `ingredients` and `i_have_rights`;
  - `flow character create "a robot" --json`;
  - `flow extend m1 "rolls off" --max-credits 10 --json`, with a timeout status exiting 2.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.** Add the commands:
  - `flow projects`, `flow media [--project] [--type video|image]`, `flow download MEDIA_ID [--project] [-o] [--force]`;
  - `flow character create DESCRIPTION [--project] [-o] [--force] [--max-credits]`;
  - `flow extend MEDIA_ID PROMPT [--project] [--max-credits] [-o] [--force] [--timeout]`;
  - on `video`/`image`: `--start-frame`, `--end-frame` and repeatable `--ingredient`, each a media id or a file, plus `--i-have-rights`.

  Docs:
  - **SKILL.md:** the new rows; the rule *never pass `--i-have-rights` unless the user confirmed they have the rights to that file*; and *extend costs credits without a quote, so ask before passing `--max-credits`*.
  - **README:** the examples.
  - **Live tests:**
    - `live` (free): projects, media, and a character, which costs 0 credits but creates a character, so it stays opt-in under `live`;
    - `live_slow`: one extend (10 credits).
- [ ] **Step 4:** Run the suite. With the profile idle, run `uv run pytest -m live tests/live/test_live_flow.py -q`. Expected: the free tests pass and credits are unchanged.
- [ ] **Step 5:** Commit with message `feat: aistudio flow projects/media/download/character/extend and attachments`.
