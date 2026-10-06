import json

import pytest

from aistudio._auth.storage import AccountInfo, save_session_info, save_state
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
