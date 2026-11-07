import contextlib
import json
import pathlib
import re
from types import SimpleNamespace

import pytest

from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio._rpc import wire
from aistudio.exceptions import BrowserError
from aistudio.paths import get_profile

TEST_API_KEY = "AIza" + "k" * 35
TEST_COOKIES = [
    {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/", "expires": -1},
    {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/", "expires": -1},
]


@pytest.fixture
def aistudio_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AISTUDIO_HOME", str(home))
    monkeypatch.delenv("AISTUDIO_PROFILE", raising=False)
    return home


@pytest.fixture(autouse=True)
def _isolated_home(aistudio_home):
    """No test reads or writes the real ~/.aistudio (the spend log is written by every run)."""
    return aistudio_home


@pytest.fixture
def logged_in_profile(aistudio_home):
    def make(name="default", api_key=TEST_API_KEY, authuser=1):
        profile = get_profile(name)
        save_state(
            profile,
            {"cookies": TEST_COOKIES, "origins": []},
            AccountInfo(email=None, authuser=authuser),
        )
        save_session_info(profile, api_key)
        return profile

    return make


@pytest.fixture
def write_rpc_fixture():
    from aistudio._transport.http import method_url

    def write(root, method, body="[]", url=None):
        path = root / method / "1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "method": method,
            "url": url or method_url(method),
            "http_method": "POST",
            "request": {"headers": {}, "body": body},
            "response": {"status": 200, "headers": {}, "body": "[]"},
        }))
        return path

    return write


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def captured():
    """Load an M1 capture fixture: decoded request/response plus the raw record."""

    def load(method, n=1):
        record = json.loads((FIXTURES / method / f"{n}.json").read_text(encoding="utf-8"))
        return SimpleNamespace(
            request=wire.decode(record["request"]["body"]),
            response=wire.decode(record["response"]["body"]),
            raw=record,
        )

    return load


@pytest.fixture
def make_client():
    from aistudio._auth.session import Session
    from aistudio._transport.http import HttpTransport
    from aistudio.client import AIStudioClient

    def make():
        return AIStudioClient(HttpTransport(Session(cookies=list(TEST_COOKIES))))

    return make


class FakeUi:
    """Scripted stand-in for aistudio._browser.ui.Ui: records ops, replays responses per method.

    ``next`` returns the next scripted response for a method, or None (a timeout) when none
    are left.
    """

    def __init__(self, responses=None, labels=None, visible=None, record_pauses=False,
                 texts=None, checks=None, inner=None, url="https://flow.google.com/",
                 missing=(), enabled=None, texts_shown=None):
        self.ops = []
        self.waits = []  # (method, timeout_s) of every ResponseLog.next
        self._record_pauses = record_pauses
        self._texts = {k: list(v) for k, v in (texts or {}).items()}
        self._checks = {k: list(v) for k, v in (checks or {}).items()}
        self._inner = {k: list(v) for k, v in (inner or {}).items()}
        self.url = url
        self._missing = set(missing)
        self._responses = {k: list(v) for k, v in (responses or {}).items()}
        self._labels = {k: list(v) for k, v in (labels or {}).items()}
        self._visible = {k: list(v) for k, v in (visible or {}).items()}
        self._enabled = {k: list(v) for k, v in (enabled or {}).items()}
        self._shown = {k: list(v) for k, v in (texts_shown or {}).items()}

    async def goto(self, url):
        self.ops.append(("goto", url))

    async def fill_first(self, selector, text, *, timeout_s=30.0):
        self.ops.append(("fill", selector, text))

    async def press_first(self, selector, key):
        self.ops.append(("press", selector, key))

    async def wait_enabled(self, role, name, *, timeout_s=30.0):
        self.ops.append(("wait_enabled", role, name))

    @staticmethod
    def _key(name):
        return name.pattern if isinstance(name, re.Pattern) else name

    @staticmethod
    def _answer(table, key, default):
        answers = table.get(key)
        if not answers:
            return default
        return answers.pop(0) if len(answers) > 1 else answers[0]

    async def click(self, role, name, *, exact=False, nth=0):
        self.ops.append(("click", role, self._key(name), nth))

    async def fill(self, role, name, text):
        self.ops.append(("fill", role, self._key(name), text))

    async def activate(self, role, name, *, exact=False, nth=0):
        self.ops.append(("activate", role, self._key(name), nth))

    async def press(self, role, name, key):
        self.ops.append(("press", role, self._key(name), key))

    async def key(self, key):
        self.ops.append(("key", key))

    async def visible(self, role, name, *, exact=False):
        return self._answer(self._visible, (role, self._key(name)), False)

    async def labels(self, role, name):
        return self._answer(self._labels, (role, self._key(name)), [])

    async def click_selector(self, selector):
        self.ops.append(("click_selector", selector))

    async def count(self, selector):
        """A number per selector, or a list of answers in order (the last one repeats)."""
        answer = getattr(self, "counts", {}).get(selector, 0)
        if isinstance(answer, list):
            return answer.pop(0) if len(answer) > 1 else answer[0]
        return answer

    async def boxes(self, selector):
        """Scripted per selector in ``box_table`` (a list of answers, like the other tables)."""
        return self._answer(getattr(self, "box_table", {}), selector, [])

    async def click_at(self, x, y):
        self.ops.append(("click_at", x, y))

    async def set_files(self, selector, paths, types=None):
        self.ops.append(("set_files", selector, tuple(paths))
                        + ((tuple(sorted(types.items())),) if types else ()))

    async def has_text(self, text):
        return self._answer(self._shown, text, True)

    async def enabled(self, role, name):
        """Scripted per (role, name); otherwise one control, disabled once it was activated."""
        key = (role, self._key(name))
        if key in self._enabled:
            return self._answer(self._enabled, key, [])
        return [not any(op[:3] == ("activate", *key) for op in self.ops)]

    async def pause(self, ms):
        if self._record_pauses:
            self.ops.append(("pause", ms))

    async def type_text(self, selector, text):
        self.ops.append(("type", selector, text))

    async def text_of(self, selector):
        return self._answer(self._texts, selector, "")

    async def inner_text(self, role, name, *, exact=False, nth=0):
        return self._answer(self._inner, (role, self._key(name)), "")

    async def checked(self, role, name, *, nth=0):
        return self._answer(self._checks, (role, self._key(name), nth), False)

    async def wait_for(self, selector, *, timeout_s=30.0):
        self.ops.append(("wait_for", selector))
        if selector in self._missing:
            raise BrowserError(f"{selector} not found")

    def current_url(self):
        return self.url

    async def batch_rpc(self, rpcid, args_json):
        self.ops.append(("batch_rpc", rpcid, args_json))
        queue = self._responses.get(rpcid) or []
        response = queue.pop(0) if queue else None
        return (response.status, response.text) if response else (404, "")

    async def music_api(self, method, path, body=None, *, timeout_s=60.0):
        self.ops.append(("music_api", method, path, body))
        queue = self._responses.get(f"{method} {path}") or []
        response = queue.pop(0) if queue else None
        return (response.status, response.text) if response else (404, "")

    async def dispatch_click(self, role, name, *, exact=False):
        self.ops.append(("dispatch", role, self._key(name)))

    async def choose_file(self, role, name, path):
        self.ops.append(("choose_file", role, self._key(name), str(path)))

    async def hover_text(self, text):
        self.ops.append(("hover", text))

    async def click_text(self, text):
        self.ops.append(("click_text", text))

    async def dispatch_click_near(self, role, name, text):
        self.ops.append(("dispatch_near", role, self._key(name), text))

    @contextlib.asynccontextmanager
    async def responses(self, marker=None, *, fetch_bodies=False):
        self.fetch_bodies = fetch_bodies
        yield self

    @contextlib.asynccontextmanager
    async def refuse_requests(self, methods, allow):
        """Scripted: ``self.sent_bodies`` (per method) are the requests the page sends; the
        refused ones are listed, as the real check does."""
        refused = [body for method in methods
                   for body in getattr(self, "sent_bodies", {}).get(method, []) if not allow(body)]
        self.ops.append(("refuse_requests", tuple(methods)))
        yield refused

    async def next(self, method, *, timeout_s):
        self.waits.append((method, timeout_s))
        queue = self._responses.get(method) or []
        return queue.pop(0) if queue else None


@pytest.fixture
def fake_ui():
    return FakeUi


@pytest.fixture
def prompt_wire():
    """Builders for synthetic ``GetPrompt`` responses (the live shape, made-up content).

    Shape: ``[name, , , run settings, metadata, , , , , , , , system instruction,
    [chunks, pending inputs]]``; a Veo prompt has its body at index 16 instead.
    """

    def chunk(text=None, *, role="user", drive=None, inline=None, thought=False, at=None):
        row = [None] * 35
        row[0] = text
        if drive:
            row[1] = [drive]
        if inline:
            row[12] = [inline, "QUJD"]
        row[8] = role
        if thought:
            row[19] = 1
        if at is not None:
            row[32] = [str(at), 123000000]
        return row

    def settings(model="models/gemini-test", *, top_p=0.95, top_k=64, max_tokens=65536,
                 temperature=None, safety=None, modalities=None):
        row = [1, None, model, temperature, top_p, top_k, max_tokens]
        row += [safety] + [None] * 7 + [modalities]
        return row

    def prompt(chunks, *, name="prompts/synthetic-id-1", title="A synthetic chat",
               description=None, kind="CHUNKED_PROMPT", modified=1778571344,
               system=None, run=None):
        meta = [title, description, ["Test User", 1, "https://example.invalid/photo"], None,
                [[str(modified), 328000000], ["Test User", 1, "https://example.invalid/photo"]],
                [1, 1, 1], None, None, None, None, [],
                [["version", "1"], ["promptType", kind], ["hasImages", "false"]]]
        row = [name, None, None, run if run is not None else settings(), meta]
        row += [None] * 7 + [[system] if system is not None else [], [chunks, [chunk("")]]]
        return row

    def veo(text, *, image="image/jpeg", name="prompts/synthetic-veo-1"):
        run = [None, None, "models/veo-test"] + [None] * 18 + ["9:16", None, ["8"]]
        meta = ["A synthetic video", None, None, None, [["1759497321", 1]], None, None, None,
                None, None, [], [["promptType", "VEO_PROMPT"]]]
        body = [text, None, [image, "QUJD"] if image else None, None, None]
        return [name, None, None, run, meta] + [None] * 11 + [body]

    return SimpleNamespace(chunk=chunk, settings=settings, prompt=prompt, veo=veo)
