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
    async def responses(self, marker=None):
        yield self

    async def next(self, method, *, timeout_s):
        self.waits.append((method, timeout_s))
        queue = self._responses.get(method) or []
        return queue.pop(0) if queue else None


@pytest.fixture
def fake_ui():
    return FakeUi
