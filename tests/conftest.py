import contextlib
import json
import pathlib
from types import SimpleNamespace

import pytest

from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio._rpc import wire
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

    def __init__(self, responses=None):
        self.ops = []
        self._responses = {k: list(v) for k, v in (responses or {}).items()}

    async def goto(self, url):
        self.ops.append(("goto", url))

    async def fill_first(self, selector, text, *, timeout_s=30.0):
        self.ops.append(("fill", selector, text))

    async def press_first(self, selector, key):
        self.ops.append(("press", selector, key))

    @contextlib.asynccontextmanager
    async def responses(self):
        yield self

    async def next(self, method, *, timeout_s):
        queue = self._responses.get(method) or []
        return queue.pop(0) if queue else None


@pytest.fixture
def fake_ui():
    return FakeUi
