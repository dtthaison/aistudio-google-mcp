"""Drive Google Flow's project page and agent (docs/wire/findings.md, "Google Flow").

The project page is agent-only. The prompt is a ProseMirror editor (keyboard typing only), the
tune panel holds the defaults, and permission questions are radios inside
``flow-permission-message``. Keyboard activation is used throughout because pointer clicks time
out in a minimized window.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aistudio._browser.ui import ResponseLog, Ui
from aistudio._flow.models import ASPECTS
from aistudio._flow.rows import (
    FlowMedia,
    FlowProject,
    ProjectContents,
    Quote,
    character_id,
    credits_left,
    decode_contents,
    decode_projects,
    extend_started,
    media_records,
    parse_permission,
)
from aistudio._flow.wire import decode_batch, payloads
from aistudio._rpc.wire import opt_index
from aistudio.exceptions import BrowserError, NotFoundError

HOME_URL = "https://flow.google.com/"
PROJECT_URL = "https://flow.google.com/project/{id}"
PROJECT_ID = re.compile(r"/project/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
PROMPT_EDITOR = "flow-rich-text-editor.prompt-input .ProseMirror"
AGENT_MESSAGE = ".agent-message-content"
TITLE = "Editable text"
TUNE = "Settings"
NEW_PROJECT = re.compile(r"New project")
NEW_SESSION = "Start new session"
EXPAND = "Expand"  # a collapsed prompt box hides "Start new session" (live)
ACCOUNT = "Account details"
INGREDIENTS = "Add ingredients to the prompt box"
UPLOAD_MEDIA = "Upload media"
AGREE = "I agree"  # Flow's "Rights to use this image" dialog
MORE = "More options"
ADD_TO_PROMPT = "Add to prompt"
EDITOR_ANY = "[contenteditable=true]"
EDITOR_LAST = "[contenteditable=true] >> nth=-1"
START = "Start generation"
ADD_CLIP = "Add clip"
EXTEND = re.compile(r"^Extend")
CHARACTER_MODEL = "Select model family"
EXTEND_PANEL = "What happens next?"  # the Extend box's placeholder (the edit box differs)
TUNE_ROLE = "radio"  # confirmed in Step 0
MODEL_OPTION_ROLE = "menuitem"  # confirmed in Step 0
MODEL_BUTTON = {"video": "Video generation default model",
                "image": "Image generation default model"}
GROUP = {"image": 0, "video": 1}  # tune panel: the image group comes first
FLOW_RPC = "batchexecute"
CHAT_RPC = "FlowCreationAgentService/StreamChat"
CHAT_METHOD = "StreamChat"
ASKS_FIRST = re.compile(r"^Always\b.*ask for confirmation")  # "Confirm before generating"
CREATED_SLACK_S = 120  # clock skew allowed when matching job records to this run
REPLY_GRACE_S = 10.0  # after a reply without a permission request, wait this long for one
PROMPT_POLLS = 20  # x 250 ms for the editor to show a typed or inserted prompt
SAVE = "Save"  # the tune panel keeps changes only after Save (live probe)
SAVE_METHOD = "Kcr7Ub"  # the save call Save sends
SAVE_TIMEOUT_S = 10.0
MEDIA_METHODS = ("as29s", "jwpduf", "bOKtO")
_CREDITS = re.compile(r"([\d,]+) Google Flow credits")
_DONE = 3


@dataclass(frozen=True)
class Defaults:
    model: str  # picker label, e.g. "Veo 3.1 - Lite"
    aspect: str
    count: int
    # "Confirm before generating: Always". With "Never" Flow spends without asking, so the CLI
    # refuses to send anything. Read only; never changed by aistudio.
    asks: bool = field(default=True, compare=False)


@dataclass(frozen=True)
class AgentTurn:
    text: str
    quote: Quote | None


@dataclass(frozen=True)
class CharacterOutcome:
    character_id: str | None
    media: list[FlowMedia]


@dataclass(frozen=True)
class FlowOutcome:
    media: list[FlowMedia]
    credits_left: int | None
    timed_out: bool


def _aspect_name(aspect: str) -> re.Pattern[str]:
    return re.compile(rf"(^|\s){re.escape(aspect)}$")


def _count_name(count: int) -> re.Pattern[str]:
    return re.compile(rf"^x{count}$")


def _group(kind: str, aspect: str | None = None) -> int:
    """Index of a tune control: shared names (16:9, 9:16, xN) appear once per group."""
    if aspect is not None and aspect not in ("16:9", "9:16"):
        return 0  # only images offer 4:3, 1:1 and 3:4
    return GROUP[kind]


def _label(text: str) -> str:
    """'🍌 Nano Banana 2.1 arrow_drop_down' -> 'Nano Banana 2.1'."""
    label = " ".join(w for w in text.split() if w != "arrow_drop_down")
    while label and not label[0].isalnum():  # a leading emoji such as 🍌
        label = label[1:].lstrip()
    return label


class FlowPage:
    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic,
                 clock: Callable[[], float] = time.time, poll_s: float = 1.0) -> None:
        self._ui = ui
        self._now = now
        self._clock = clock  # wall clock, compared with Flow's job creation times
        self._poll_s = poll_s
        self.paid_media: list[str] = []  # media ids of the approved job, as soon as seen
        self.last_scene_id: str | None = None  # the scene an extension creates

    async def open_project(self, project_id: str) -> None:
        await self._ui.goto(PROJECT_URL.format(id=project_id))
        try:
            await self._ui.wait_for(PROMPT_EDITOR, timeout_s=30.0)
        except BrowserError as exc:
            raise NotFoundError(f"Flow project {project_id} did not open",
                                hint="check the id, or leave out --project") from exc
        landed = PROJECT_ID.search(self._ui.current_url())
        if landed is None or landed.group(1) != project_id:
            raise NotFoundError(f"Flow project {project_id} did not open (landed elsewhere)",
                                hint="check the id, or leave out --project")

    async def create_project(self, title: str) -> str:
        await self._ui.goto(HOME_URL)
        await self._ui.activate("button", NEW_PROJECT)
        project_id = None
        for _ in range(60):
            match = PROJECT_ID.search(self._ui.current_url())
            if match:
                project_id = match.group(1)
                break
            await self._ui.pause(500)
        if project_id is None:
            raise BrowserError("Flow did not open the new project",
                               hint="re-run; if it persists, the Flow UI changed")
        await self._ui.fill("textbox", TITLE, title)
        await self._ui.press("textbox", TITLE, "Enter")
        return project_id

    async def new_session(self) -> None:
        if (not await self._ui.visible("button", NEW_SESSION, exact=True)
                and await self._ui.visible("button", EXPAND, exact=True)):
            await self._ui.activate("button", EXPAND, exact=True)
            await self._ui.pause(1000)
        await self._ui.activate("button", NEW_SESSION, exact=True)
        await self._ui.pause(1000)

    async def read_defaults(self, kind: str) -> Defaults:
        await self._ui.activate("button", TUNE, exact=True)
        await self._ui.pause(1200)
        model = _label(await self._ui.inner_text("button", MODEL_BUTTON[kind], exact=True))
        aspect = await self._checked_one(kind, ASPECTS[kind], _aspect_name, aspect=True)
        count = await self._checked_one(kind, (1, 2, 3, 4), _count_name, aspect=False)
        asks = await self._ui.checked("radio", ASKS_FIRST)
        await self._close_tune()
        if not model or aspect is None or count is None:
            raise BrowserError(f"could not read Flow's {kind} defaults",
                               hint="re-run; if it persists, the Flow UI changed")
        return Defaults(model, str(aspect), int(count), asks=asks)

    async def apply_defaults(self, kind: str, wanted: Defaults) -> None:
        await self._ui.activate("button", TUNE, exact=True)
        await self._ui.pause(1200)
        current = _label(await self._ui.inner_text("button", MODEL_BUTTON[kind], exact=True))
        if current != wanted.model:
            await self._ui.activate("button", MODEL_BUTTON[kind], exact=True)
            await self._ui.pause(1000)
            await self._ui.activate(MODEL_OPTION_ROLE, re.compile(re.escape(wanted.model)))
            await self._ui.pause(1000)
        await self._ui.activate(TUNE_ROLE, _aspect_name(wanted.aspect),
                                nth=_group(kind, wanted.aspect))
        await self._ui.activate(TUNE_ROLE, _count_name(wanted.count), nth=_group(kind))
        async with self._ui.responses(marker=FLOW_RPC) as log:
            await self._ui.activate("button", SAVE, exact=True)
            saved = await log.next(SAVE_METHOD, timeout_s=SAVE_TIMEOUT_S)
        if saved is None or saved.status >= 400:
            raise BrowserError(f"Flow did not save the {kind} settings",
                               hint="re-run; if it persists, the Flow UI changed")
        await self._close_tune()
        if await self.read_defaults(kind) != wanted:
            raise BrowserError(f"Flow's {kind} settings did not take effect",
                               hint="re-run; if it persists, the Flow UI changed")

    async def submit_for_quote(self, text: str, *, timeout_s: float = 90.0) -> AgentTurn:
        """Send the instruction and read Flow's structured permission request for it.

        The quote comes from the StreamChat response to this message, never from page text, so
        an echoed prompt or an older message cannot fake a price.
        """
        async with self._ui.responses(marker=CHAT_RPC) as log:
            await self._ui.type_text(PROMPT_EDITOR, text)
            await self._wait_for_prompt(text)
            await self._ui.key("Enter")
            deadline = self._now() + timeout_s
            while self._now() < deadline:
                response = await log.next(CHAT_METHOD, timeout_s=self._poll_s)
                if response is None or response.status >= 400:
                    continue
                for _, payload in payloads(response.text):
                    quote = parse_permission(payload)
                    if quote is not None:
                        return AgentTurn(await self._ui.text_of(AGENT_MESSAGE), quote)
                deadline = min(deadline, self._now() + REPLY_GRACE_S)
        return AgentTurn(await self._ui.text_of(AGENT_MESSAGE), None)

    async def _wait_for_prompt(self, text: str) -> None:
        """Enter only once the editor shows the prompt: a long prompt is inserted at once, and an
        immediate Enter sent an empty box (live)."""
        want = int(len(" ".join(text.split())) * 0.9)
        for _ in range(PROMPT_POLLS):
            if len(await self._ui.text_of(PROMPT_EDITOR)) >= want:
                return
            await self._ui.pause(250)
        raise BrowserError("the prompt did not reach Flow's editor; nothing was sent",
                           hint="re-run; if it persists, add --visible to watch it")

    async def reject(self) -> None:
        await self._ui.activate("radio", "Reject", exact=True)
        await self._ui.pause(1000)

    async def approve_and_follow(self, *, count: int, timeout_s: float,
                                 kind: str) -> FlowOutcome:
        if len(await self._ui.labels("radio", re.compile(r"^Approve$"))) != 1:
            raise BrowserError("could not find exactly one Approve option; nothing was spent",
                               hint="re-run; if it persists, the Flow UI changed")
        async with self._ui.responses(marker=FLOW_RPC) as log:
            since = self._clock()
            await self._ui.activate("radio", "Approve", exact=True)
            return await self._follow(log, count, timeout_s, kind, since)

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

    async def media_details(self, media_id: str) -> FlowMedia | None:
        """One media with its signed links (the project list leaves them out)."""
        status, text = await self._ui.batch_rpc("as29s", json.dumps([media_id]))
        if status >= 400:
            raise BrowserError(f"Flow did not return media {media_id} (HTTP {status})",
                               hint="re-run; if it persists, the Flow UI changed")
        records = media_records("as29s", decode_batch(text).get("as29s"))
        return records[0] if records else None

    async def upload(self, path: Path, known_ids: set[str] | frozenset[str] = frozenset()) -> str:
        """Upload a local file (the caller has the person's --i-have-rights)."""
        await self._ui.activate("button", INGREDIENTS, exact=True)
        await self._ui.pause(1500)
        async with self._ui.responses(marker=FLOW_RPC) as log:
            since = self._clock()
            await self._ui.choose_file("button", UPLOAD_MEDIA, path)
            # Flow asks "Rights to use this image" only on a first upload (live).
            for _ in range(10):
                await self._ui.pause(500)
                if await self._ui.visible("button", AGREE, exact=True):
                    await self._ui.dispatch_click("button", AGREE)
                    break
            if await log.next("maseQ", timeout_s=120.0) is None:
                raise BrowserError(f"Flow did not upload {path.name}",
                                   hint="re-run; if it persists, the Flow UI changed")
            deadline = self._now() + 60.0
            while self._now() < deadline:
                response = await log.next("as29s", timeout_s=self._poll_s)
                if response is None:
                    continue
                for media in media_records("as29s", decode_batch(response.text).get("as29s")):
                    if media.media_id in known_ids:
                        continue  # existed before this upload
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

    async def open_character(self, project_id: str) -> str:
        """Open the character page and return the image model it will use (its picker)."""
        await self._ui.goto(f"{PROJECT_URL.format(id=project_id)}/character")
        await self._ui.wait_for(EDITOR_ANY, timeout_s=30.0)
        return _label(await self._ui.inner_text("button", CHARACTER_MODEL, exact=True))

    async def start_character(self, description: str) -> CharacterOutcome:
        """Call after open_character (same page load). Creating a character may spend."""
        await self._ui.type_text(EDITOR_ANY, description)
        async with self._ui.responses(marker=FLOW_RPC) as log:
            await self._ui.click("button", START, exact=True)
            created = await log.next("C4BZMd", timeout_s=60.0)
            cid = character_id(decode_batch(created.text).get("C4BZMd")) if created else None
            self.paid_media = [cid] if cid else []
            image = await log.next("ogiZ0b", timeout_s=180.0)
        media = media_records("ogiZ0b", decode_batch(image.text).get("ogiZ0b")) if image else []
        if not media:
            raise BrowserError(f"Flow created character {cid or '(id not seen)'} but sent no image",
                               hint="check the project's Characters in Flow")
        return CharacterOutcome(cid, media)

    async def extend_label(self, project_id: str, workflow_id: str) -> str:
        """Open the clip's editor by its workflow id and read the model "Extend (…)" will use."""
        await self._ui.goto(f"{PROJECT_URL.format(id=project_id)}/edit/{workflow_id}")
        await self._ui.wait_for(EDITOR_ANY, timeout_s=30.0)
        if not self._ui.current_url().split("?", 1)[0].endswith(f"/edit/{workflow_id}"):
            raise BrowserError("Flow opened a different clip; nothing was spent",
                               hint="re-run; if it persists, the Flow UI changed")
        await self._ui.dispatch_click("button", ADD_CLIP)
        await self._ui.pause(1200)
        text = await self._ui.inner_text("menuitem", EXTEND)
        match = re.search(r"Extend \((.+)\)", text)
        if match is None:
            raise BrowserError("Flow offered no Extend for this clip",
                               hint="only Veo clips can be extended")
        return match.group(1).strip()

    async def extend(self, prompt: str, *, timeout_s: float) -> FlowOutcome:
        """Call after extend_label (the Add clip menu is open). Spends credits."""
        await self._ui.dispatch_click("menuitem", EXTEND)
        await self._ui.pause(1500)
        if EXTEND_PANEL not in await self._ui.text_of("body"):
            raise BrowserError("Flow's Extend panel did not open; nothing was spent",
                               hint="re-run; if it persists, the Flow UI changed")
        await self._ui.type_text(EDITOR_LAST, prompt)
        async with self._ui.responses(marker=FLOW_RPC) as log:
            since = self._clock()
            await self._ui.click("button", START, exact=True, nth=-1)
            scene = await log.next("rqZuUc", timeout_s=15.0)  # Flow wraps the clips in a scene
            if scene is not None:
                scene_id = opt_index(decode_batch(scene.text).get("rqZuUc"), 0, 0)
                self.last_scene_id = scene_id if isinstance(scene_id, str) else None
            started = await log.next("fZytfe", timeout_s=60.0)
            credits, media = (extend_started(decode_batch(started.text).get("fZytfe"))
                              if started is not None and started.status < 400 else (None, []))
            if not media:
                raise BrowserError("Flow did not start the extension",
                                   hint="it may still be charged: check the project in Flow "
                                        "before re-running")
            self.paid_media = [m.media_id for m in media]
            outcome = await self._follow(log, 1, timeout_s, "video", since,
                                         only=set(self.paid_media))
        return FlowOutcome(outcome.media, credits if outcome.credits_left is None
                           else outcome.credits_left, outcome.timed_out)

    async def credits(self) -> int | None:
        await self._ui.goto(HOME_URL)
        await self._ui.activate("button", ACCOUNT, exact=True)
        await self._ui.pause(1200)
        match = _CREDITS.search(await self._ui.text_of("body"))
        await self._ui.key("Escape")
        return int(match.group(1).replace(",", "")) if match else None

    async def _follow(self, log: ResponseLog, count: int, timeout_s: float, kind: str,
                      since: float, only: set[str] | None = None) -> FlowOutcome:
        """Follow only media created by this job: the reused project lists older media too."""
        seen: dict[str, FlowMedia] = {}
        credits = None
        deadline = self._now() + timeout_s
        while self._now() < deadline:
            for method in MEDIA_METHODS:
                response = await log.next(method, timeout_s=self._poll_s)
                if response is None or response.status >= 400:
                    continue
                payload = decode_batch(response.text).get(method)
                if method == "jwpduf" and credits_left(payload) is not None:
                    credits = credits_left(payload)
                for media in media_records(method, payload):
                    if only is not None:
                        if media.media_id not in only:
                            continue  # Flow named this job's media: nothing else counts
                    elif media.media_id not in seen and (
                            media.created is None or media.created < since - CREATED_SLACK_S):
                        continue  # older media in the project, not this job
                    old = seen.get(media.media_id)
                    seen[media.media_id] = _merge(old, media) if old else media
                    if media.media_id not in self.paid_media:
                        self.paid_media.append(media.media_id)
                    # Statuses other than done mean "not finished" here: Flow also reports
                    # queued (6) before running (2), so only the deadline ends the wait.
            ready = [m for m in seen.values()
                     if m.status == _DONE and (m.video_url if kind == "video" else m.image_url)]
            if len(ready) >= count:
                return FlowOutcome(ready, credits, False)
        return FlowOutcome(list(seen.values()), credits, True)

    async def _checked_one(self, kind, values, name, *, aspect: bool):
        for value in values:
            nth = _group(kind, value if aspect else None)
            if await self._ui.checked(TUNE_ROLE, name(value), nth=nth):
                return value
        return None

    async def _close_tune(self) -> None:
        await self._ui.goto(self._ui.current_url())  # Escape does not close it; a reload does
        await self._ui.wait_for(PROMPT_EDITOR, timeout_s=30.0)


def _merge(old: FlowMedia, new: FlowMedia) -> FlowMedia:
    """Later responses fill in what earlier ones lacked (links arrive with as29s); "done" never
    goes back, since a late "running" or "queued" poll can follow the "done" record."""
    merged = {field: getattr(new, field) if getattr(new, field) is not None
              else getattr(old, field) for field in old.__dataclass_fields__}
    if _DONE in (old.status, new.status):  # done is final; codes are not ordered (6 = queued)
        merged["status"] = _DONE
    return FlowMedia(**merged)
