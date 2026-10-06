import asyncio
import json

import pytest

from aistudio._dev.capture import CaptureRecorder, run_capture
from aistudio._dev.fixtures import FixtureWriter
from aistudio.exceptions import BrowserError
from aistudio.paths import get_profile

RPC = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)


class FakeResponse:
    def __init__(self, status=200, body=b"[]", headers=None, body_error=None):
        self.status = status
        self._body = body
        self._headers = headers or {"content-type": "application/json+protobuf"}
        self._body_error = body_error

    async def body(self):
        if self._body_error:
            raise self._body_error
        return self._body

    async def all_headers(self):
        return self._headers


class FakeRequest:
    def __init__(self, url, method="POST", body=b"[]", headers=None, response=None,
                 headers_error=None):
        self.url = url
        self.method = method
        self.post_data_buffer = body
        self._headers = headers or {}
        self._response = response if response is not None else FakeResponse()
        self._headers_error = headers_error

    async def all_headers(self):
        if self._headers_error:
            raise self._headers_error
        return self._headers

    async def response(self):
        return self._response


async def test_records_matching_request_redacted(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    headers = {"cookie": "SID=secret", "content-type": "application/json+protobuf"}
    request = FakeRequest(RPC + "ListPrompts", body=b"[null,20]", headers=headers)
    path = await recorder.handle(request)
    assert path == tmp_path / "ListPrompts" / "1.json"
    data = json.loads(path.read_text())
    assert data["request"]["body"] == "[null,20]"
    assert "cookie" not in data["request"]["headers"]
    assert recorder.counts == {"ListPrompts": 1}


@pytest.mark.parametrize(
    "url,method",
    [("https://www.gstatic.com/app.js", "GET"), (RPC + "ListPrompts", "OPTIONS")],
)
async def test_ignores_other_hosts_and_preflight(tmp_path, url, method):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    assert await recorder.handle(FakeRequest(url, method=method)) is None
    assert not list(tmp_path.iterdir())


async def test_unreadable_body_is_recorded_empty(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    response = FakeResponse(body_error=RuntimeError("body evicted"))
    path = await recorder.handle(FakeRequest(RPC + "GenerateContent", response=response))
    assert json.loads(path.read_text())["response"]["body"] == ""


async def test_handle_swallows_closed_browser_errors(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    request = FakeRequest(RPC + "ListModels", headers_error=RuntimeError("Target closed"))
    assert await recorder.handle(request) is None
    assert not list(tmp_path.iterdir())


async def test_on_write_callback(tmp_path):
    seen = []
    recorder = CaptureRecorder(FixtureWriter(tmp_path), on_write=lambda p, m: seen.append(m))
    await recorder.handle(FakeRequest(RPC + "ListModels"))
    assert seen == ["ListModels"]


async def test_run_capture_requires_login(aistudio_home, tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    with pytest.raises(BrowserError) as info:
        await run_capture(get_profile("default"), recorder, playwright_factory=lambda: None)
    assert info.value.hint == 'run "aistudio login" first'


class FakeWindow:
    """A page whose window the user closes right after the app loads."""

    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def goto(self, url, wait_until=None):
        asyncio.get_running_loop().call_soon(self._user_closes_window)

    def _user_closes_window(self):
        for handler in self.handlers.get("close", []):
            handler(self)


class BrowserThatStaysRunning:
    """macOS Chrome: closing the last window does not end the browser, so no context 'close'."""

    def __init__(self):
        self.pages = [FakeWindow()]
        self.handlers = {}
        self.closed = False

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def new_page(self):
        return self.pages[0]

    async def close(self):
        self.closed = True


async def test_capture_ends_when_last_window_closes_but_browser_keeps_running(aistudio_home,
                                                                              tmp_path):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = BrowserThatStaysRunning()

    class Chromium:
        async def launch_persistent_context(self, user_data_dir, **kwargs):
            return context

    class Playwright:
        chromium = Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    await asyncio.wait_for(run_capture(profile, recorder, playwright_factory=Playwright), 2)
    assert context.closed


@pytest.mark.parametrize("method", ["ListCloudProjects", "ListCloudApiKeys", "ListImportedProjects",
                                    "GetCloudProjectQuotaTier", "GetDefaultCloudProject"])
async def test_cloud_console_calls_are_never_recorded(tmp_path, method):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    assert await recorder.handle(FakeRequest(RPC + method)) is None
    assert not list(tmp_path.iterdir())


async def test_applet_secret_values_are_never_recorded(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    assert await recorder.handle(FakeRequest(RPC + "ListAppletSecrets")) is None
    assert not list(tmp_path.iterdir())
