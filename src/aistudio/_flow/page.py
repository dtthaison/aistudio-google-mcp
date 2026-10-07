"""Drive Google Flow's project page and agent (docs/wire/findings.md, "Google Flow").

The project page is agent-only. The prompt is a ProseMirror editor (keyboard typing only), the
tune panel holds the defaults, and permission questions are radios inside
``flow-permission-message``. Keyboard activation is used throughout because pointer clicks time
out in a minimized window.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from aistudio._browser.ui import ResponseLog, Ui
from aistudio._flow.models import ASPECTS
from aistudio._flow.rows import FlowMedia, Quote, credits_left, media_records, parse_quote
from aistudio._flow.wire import decode_batch
from aistudio.exceptions import BrowserError, NotFoundError, ServerError

HOME_URL = "https://flow.google.com/"
PROJECT_URL = "https://flow.google.com/project/{id}"
PROJECT_ID = re.compile(r"/project/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
PROMPT_EDITOR = "flow-rich-text-editor.prompt-input .ProseMirror"
AGENT_MESSAGE = ".agent-message-content"
TITLE = "Editable text"
TUNE = "Settings"
NEW_PROJECT = re.compile(r"New project")
NEW_SESSION = "Start new session"
ACCOUNT = "Account details"
TUNE_ROLE = "radio"  # confirmed in Step 0
MODEL_OPTION_ROLE = "menuitem"  # confirmed in Step 0
MODEL_BUTTON = {"video": "Video generation default model",
                "image": "Image generation default model"}
GROUP = {"image": 0, "video": 1}  # tune panel: the image group comes first
FLOW_RPC = "batchexecute"
SAVE = "Save"  # the tune panel keeps changes only after Save (live probe)
SAVE_METHOD = "Kcr7Ub"  # the save call Save sends
SAVE_TIMEOUT_S = 10.0
MEDIA_METHODS = ("as29s", "jwpduf", "bOKtO")
_CREDITS = re.compile(r"([\d,]+) Google Flow credits")
_DONE = 3
_RUNNING = (1, 2)


@dataclass(frozen=True)
class Defaults:
    model: str  # picker label, e.g. "Veo 3.1 - Lite"
    aspect: str
    count: int


@dataclass(frozen=True)
class AgentTurn:
    text: str
    quote: Quote | None


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
                 poll_s: float = 1.0) -> None:
        self._ui = ui
        self._now = now
        self._poll_s = poll_s

    async def open_project(self, project_id: str) -> None:
        await self._ui.goto(PROJECT_URL.format(id=project_id))
        try:
            await self._ui.wait_for(PROMPT_EDITOR, timeout_s=30.0)
        except BrowserError as exc:
            raise NotFoundError(f"Flow project {project_id} did not open",
                                hint="check the id, or leave out --project") from exc

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
        await self._ui.activate("button", NEW_SESSION, exact=True)
        await self._ui.pause(1000)

    async def read_defaults(self, kind: str) -> Defaults:
        await self._ui.activate("button", TUNE, exact=True)
        await self._ui.pause(1200)
        model = _label(await self._ui.inner_text("button", MODEL_BUTTON[kind], exact=True))
        aspect = await self._checked_one(kind, ASPECTS[kind], _aspect_name, aspect=True)
        count = await self._checked_one(kind, (1, 2, 3, 4), _count_name, aspect=False)
        await self._close_tune()
        if not model or aspect is None or count is None:
            raise BrowserError(f"could not read Flow's {kind} defaults",
                               hint="re-run; if it persists, the Flow UI changed")
        return Defaults(model, str(aspect), int(count))

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

    async def submit(self, text: str) -> None:
        await self._ui.type_text(PROMPT_EDITOR, text)
        await self._ui.key("Enter")

    async def wait_for_quote(self, *, timeout_s: float = 90.0) -> AgentTurn:
        deadline = self._now() + timeout_s
        text = ""
        while self._now() < deadline:
            text = await self._ui.text_of(AGENT_MESSAGE)
            quote = parse_quote(text) if text else None
            if quote is not None:
                return AgentTurn(text, quote)
            await self._ui.pause(int(self._poll_s * 1000))
        return AgentTurn(text, None)

    async def reject(self) -> None:
        await self._ui.activate("radio", "Reject", exact=True)
        await self._ui.pause(1000)

    async def approve_and_follow(self, *, count: int, timeout_s: float) -> FlowOutcome:
        if len(await self._ui.labels("radio", re.compile(r"^Approve$"))) != 1:
            raise BrowserError("could not find exactly one Approve option; nothing was spent",
                               hint="re-run; if it persists, the Flow UI changed")
        async with self._ui.responses(marker=FLOW_RPC) as log:
            await self._ui.activate("radio", "Approve", exact=True)
            return await self._follow(log, count, timeout_s)

    async def credits(self) -> int | None:
        await self._ui.goto(HOME_URL)
        await self._ui.activate("button", ACCOUNT, exact=True)
        await self._ui.pause(1200)
        match = _CREDITS.search(await self._ui.text_of("body"))
        await self._ui.key("Escape")
        return int(match.group(1).replace(",", "")) if match else None

    async def _follow(self, log: ResponseLog, count: int, timeout_s: float) -> FlowOutcome:
        seen: dict[str, FlowMedia] = {}
        credits = None
        deadline = self._now() + timeout_s
        while self._now() < deadline:
            for method in MEDIA_METHODS:
                response = await log.next(method, timeout_s=self._poll_s)
                if response is None or response.status >= 400:
                    continue
                payload = decode_batch(response.text).get(method)
                if method == "jwpduf":
                    credits = credits_left(payload) if credits_left(payload) is not None \
                        else credits
                for media in media_records(method, payload):
                    old = seen.get(media.media_id)
                    seen[media.media_id] = _merge(old, media) if old else media
                    if media.status not in (None, _DONE, *_RUNNING):
                        raise ServerError(f"Flow could not make media {media.media_id} "
                                          f"(status {media.status})",
                                          hint="check the project in Flow; credits may be "
                                               "refunded")
            ready = [m for m in seen.values() if m.status == _DONE and (m.video_url or m.image_url)]
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
    """Later responses fill in what earlier ones lacked (links arrive with as29s); a status
    never goes backwards, since a late "running" poll can follow the "done" record."""
    merged = {field: getattr(new, field) if getattr(new, field) is not None
              else getattr(old, field) for field in old.__dataclass_fields__}
    if old.status is not None and new.status is not None:
        merged["status"] = max(old.status, new.status)
    return FlowMedia(**merged)
