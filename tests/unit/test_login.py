import pytest

from aistudio._auth.login import api_key_from_request, authuser_from_url, is_logged_in, run_login
from aistudio._auth.storage import load_api_key, load_state
from aistudio.exceptions import BrowserError, WaitTimeoutError
from aistudio.paths import get_profile

KEY = "AIza" + "k" * 35
API_URL = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
)
LOGGED_IN = [
    {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/", "expires": -1},
    {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/", "expires": -1},
]


def test_is_logged_in():
    assert is_logged_in("https://aistudio.google.com/prompts/new_chat", {"SAPISID", "SID"})
    assert not is_logged_in("https://accounts.google.com/signin", {"SAPISID"})
    assert not is_logged_in("https://aistudio.google.com/", {"SID"})


def test_api_key_from_request():
    assert api_key_from_request(API_URL, {"x-goog-api-key": KEY}) == KEY
    assert api_key_from_request("https://www.google.com/x", {"x-goog-api-key": KEY}) is None
    assert api_key_from_request(API_URL, {"x-goog-api-key": "not-a-key"}) is None
    assert api_key_from_request(API_URL, {}) is None


def test_authuser_from_url():
    assert authuser_from_url("https://aistudio.google.com/?authuser=2") == 2
    assert authuser_from_url("https://aistudio.google.com/") == 0
    assert authuser_from_url("https://aistudio.google.com/?authuser=x") == 0


class FakeRequest:
    def __init__(self, url, headers):
        self.url = url
        self.headers = headers


class FakePage:
    def __init__(self, context, landing_url, html):
        self.context = context
        self.url = "about:blank"
        self._landing = landing_url
        self._html = html

    async def goto(self, url, wait_until=None):
        self.url = self._landing
        for request in self.context.emitted_requests:
            for handler in self.context.handlers.get("request", []):
                handler(request)

    async def content(self):
        return self._html


class FakeContext:
    def __init__(self, cookies, *, landing_url="https://aistudio.google.com/?authuser=1",
                 requests=(), html=""):
        self._cookies = cookies
        self.handlers = {}
        self.emitted_requests = list(requests)
        self.pages = [FakePage(self, landing_url, html)]
        self.closed = False

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def cookies(self):
        return self._cookies

    async def storage_state(self):
        foreign = {"name": "YT", "value": "y", "domain": ".youtube.com", "path": "/",
                   "expires": -1}
        return {"cookies": self._cookies + [foreign], "origins": []}

    async def new_page(self):
        return self.pages[0]

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context=None, error=None):
        self.context = context
        self.error = error
        self.launch_kwargs = None

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launch_kwargs = {"user_data_dir": user_data_dir, **kwargs}
        if self.error:
            raise self.error
        return self.context


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def factory(chromium):
    return lambda: FakePlaywright(chromium)


async def test_run_login_saves_filtered_state_and_api_key(aistudio_home):
    context = FakeContext(LOGGED_IN, requests=[FakeRequest(API_URL, {"x-goog-api-key": KEY})])
    chromium = FakeChromium(context)
    profile = get_profile("default")
    result = await run_login(profile, playwright_factory=factory(chromium), poll=0, key_wait=0)
    assert result.api_key_captured is True
    assert result.account.authuser == 1
    assert load_api_key(profile) == KEY
    assert {c["name"] for c in load_state(profile)["cookies"]} == {"SID", "SAPISID"}
    assert context.closed
    assert chromium.launch_kwargs["user_data_dir"] == str(profile.chrome_dir)
    assert chromium.launch_kwargs["channel"] == "chrome"
    assert chromium.launch_kwargs["headless"] is False
    assert chromium.launch_kwargs["ignore_default_args"] == ["--enable-automation"]
    assert "--disable-blink-features=AutomationControlled" in chromium.launch_kwargs["args"]


async def test_run_login_falls_back_to_key_in_html(aistudio_home):
    context = FakeContext(LOGGED_IN, html=f'<script>var k="{KEY}";</script>')
    profile = get_profile("default")
    result = await run_login(profile, playwright_factory=factory(FakeChromium(context)),
                             poll=0, key_wait=0)
    assert result.api_key_captured is True
    assert load_api_key(profile) == KEY


async def test_run_login_without_any_key_still_saves_cookies(aistudio_home):
    profile = get_profile("default")
    chromium = FakeChromium(FakeContext(LOGGED_IN))
    result = await run_login(profile, playwright_factory=factory(chromium), poll=0, key_wait=0)
    assert result.api_key_captured is False
    assert load_api_key(profile) is None
    assert profile.storage_state.exists()


async def test_run_login_times_out_and_closes_browser(aistudio_home):
    nid = {"name": "NID", "value": "n", "domain": ".google.com", "path": "/", "expires": -1}
    context = FakeContext([nid])
    with pytest.raises(WaitTimeoutError):
        await run_login(get_profile("default"), playwright_factory=factory(FakeChromium(context)),
                        timeout=0, poll=0)
    assert context.closed


async def test_missing_chrome_becomes_browser_error_with_hint(aistudio_home):
    chromium = FakeChromium(error=Exception("Chromium distribution 'chrome' is not found"))
    with pytest.raises(BrowserError) as info:
        await run_login(get_profile("default"), playwright_factory=factory(chromium), poll=0)
    assert "--browser chromium" in info.value.hint


async def test_chromium_browser_uses_bundled_channel(aistudio_home):
    chromium = FakeChromium(FakeContext(LOGGED_IN))
    await run_login(get_profile("default"), browser="chromium",
                    playwright_factory=factory(chromium), poll=0, key_wait=0)
    assert chromium.launch_kwargs["channel"] is None


async def test_fresh_deletes_cached_browser_profile(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    (profile.chrome_dir / "Cookies").write_text("old")
    await run_login(profile, fresh=True,
                    playwright_factory=factory(FakeChromium(FakeContext(LOGGED_IN))),
                    poll=0, key_wait=0)
    assert not (profile.chrome_dir / "Cookies").exists()


async def test_profile_in_use_gets_a_close_the_other_window_hint(aistudio_home):
    chromium = FakeChromium(error=Exception(
        "Failed to create a ProcessSingleton for your profile directory"
    ))
    with pytest.raises(BrowserError) as info:
        await run_login(get_profile("default"), playwright_factory=factory(chromium), poll=0)
    assert "another aistudio browser window" in info.value.hint
