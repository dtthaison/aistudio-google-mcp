"""An attached browser (ShardX through muse-browser-shardx): specs, the provider, close rules."""

import json
import os
import stat
import sys

import pytest

from aistudio._auth.login import run_login
from aistudio._browser import provider as prov
from aistudio._browser.provider import (
    AttachedContext,
    BrowserLock,
    BrowserSpec,
    connect_browser,
    has_browser_session,
    load_spec,
    parse_spec,
    reset_spec,
    resolve_spec,
    save_spec,
)
from aistudio._browser.ui import PlaywrightUi, open_browser
from aistudio.exceptions import BrowserError, ValidationError
from aistudio.paths import get_profile

URL = "http://127.0.0.1:45123"

PROVIDER_SCRIPT = """#!{python}
import os, sys, time
mode = sys.argv[2]
marker = os.environ.get("FAKE_MARKER")
if mode not in ("fail", "badurl", "silent"):  # any other profile works
    print("{url}", flush=True)
    sys.stdin.read()  # stays up until stdin closes
    if marker:
        open(marker, "w").write("stopped")
elif mode == "fail":
    sys.stderr.write("ShardX Launcher API is not reachable; token=abc123secret\\n")
    sys.exit(3)
elif mode == "badurl":
    print("http://evil.example.com:9222", flush=True)
    time.sleep(30)
elif mode == "silent":
    time.sleep(30)
"""


@pytest.fixture
def fake_provider(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "muse-browser-fake"
    exe.write_text(PROVIDER_SCRIPT.format(python=sys.executable, url=URL))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    marker = tmp_path / "marker"
    monkeypatch.setenv("FAKE_MARKER", str(marker))
    monkeypatch.setattr(prov, "PROVIDER_EXIT_WAIT_S", 3.0)
    return marker


class FakePage:
    def __init__(self, url="about:blank"):
        self.url = url
        self.closed = False
        self.visited = []

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = url

    async def set_viewport_size(self, size):
        raise AssertionError("an attached browser's viewport is not touched")

    async def content(self):
        return ""


class FakeContext:
    def __init__(self, pages):
        self.pages = pages
        self.closed = False
        self.handlers = {}

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def on(self, event, handler):
        self.handlers[event] = handler

    async def cookies(self):
        return [
            {"name": "SID", "value": "s", "domain": ".google.com", "path": "/"},
            {"name": "SAPISID", "value": "s", "domain": ".google.com", "path": "/"},
        ]

    async def storage_state(self):
        return {"cookies": await self.cookies(), "origins": []}

    async def new_cdp_session(self, page):
        raise AssertionError("the window of an attached browser is not resized")

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts
        self.closed = False
        self.created = []

    async def new_context(self):
        context = FakeContext([])
        self.created.append(context)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.url = None

    async def connect_over_cdp(self, url):
        self.url = url
        return self.browser

    async def launch_persistent_context(self, *args, **kwargs):
        raise AssertionError("an attached browser is never launched")


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


# --- specs ----------------------------------------------------------------------------------


def test_parse_spec_accepts_local_provider_and_cdp():
    assert parse_spec("chrome") == BrowserSpec(kind="local", browser="chrome")
    assert parse_spec("msedge").browser == "msedge"
    spec = parse_spec("shardx:My Profile 1")
    assert (spec.kind, spec.name, spec.arg, spec.attached) == (
        "provider", "shardx", "My Profile 1", True)
    assert str(spec) == "shardx:My Profile 1"
    cdp = parse_spec("cdp:http://127.0.0.1:9222/")
    assert (cdp.kind, cdp.url, str(cdp)) == ("cdp", "http://127.0.0.1:9222", "cdp:http://127.0.0.1:9222")


@pytest.mark.parametrize("text", [
    "", "firefox", "shardx", "shardx:", "shardx:  ", "Shardx:x", "-x:y", "chrome:default",
    "cdp:http://evil.example.com:9222", "cdp:ws://127.0.0.1:9222", "cdp:",
])
def test_parse_spec_rejects_bad_specs(text):
    with pytest.raises(ValidationError):
        parse_spec(text)


def test_browser_json_round_trip_and_reset(aistudio_home):
    profile = get_profile("work")
    assert load_spec(profile) == BrowserSpec(kind="local")
    save_spec(profile, parse_spec("shardx:acct"))
    assert json.loads(profile.browser_json.read_text()) == {"browser": "shardx:acct"}
    assert stat.S_IMODE(profile.browser_json.stat().st_mode) == 0o600
    assert load_spec(profile) == BrowserSpec(kind="provider", name="shardx", arg="acct")
    assert reset_spec(profile) is True and reset_spec(profile) is False
    assert load_spec(profile) == BrowserSpec(kind="local")


def test_a_corrupt_browser_json_is_a_clear_error(aistudio_home):
    profile = get_profile("work").ensure()
    profile.browser_json.write_text("{nope")
    with pytest.raises(ValidationError, match="browser.json"):
        load_spec(profile)
    profile.browser_json.write_text('{"browser": 3}')
    with pytest.raises(ValidationError):
        load_spec(profile)


def test_resolve_spec_prefers_the_override(aistudio_home):
    profile = get_profile("work")
    save_spec(profile, parse_spec("shardx:a"))
    assert str(resolve_spec(profile)) == "shardx:a"
    assert str(resolve_spec(profile, "chromium")) == "chromium"


def test_session_check_uses_the_saved_login_for_an_attached_browser(logged_in_profile):
    profile = get_profile("default").ensure()
    attached, local = parse_spec("shardx:a"), parse_spec("chrome")
    assert not has_browser_session(profile, attached) and not has_browser_session(profile, local)
    logged_in_profile()
    assert has_browser_session(profile, attached)
    assert not has_browser_session(profile, local)
    profile.chrome_dir.mkdir()
    assert has_browser_session(profile, local)


# --- the provider ---------------------------------------------------------------------------


async def test_provider_happy_path_attaches_and_stops_the_provider(aistudio_home, fake_provider):
    profile = get_profile("default")
    own_tab = FakePage("https://example.com/")
    browser = FakeBrowser([FakeContext([own_tab])])
    playwright = FakePlaywright(browser)
    context = await connect_browser(playwright, profile, parse_spec("fake:p1"))
    assert isinstance(context, AttachedContext)
    assert playwright.chromium.url == URL
    assert context.pages == [own_tab]
    await context.close()
    assert browser.closed
    assert fake_provider.read_text() == "stopped"  # stdin closed: the provider shut down itself


async def test_close_leaves_the_users_tabs_and_context_alone(aistudio_home, fake_provider):
    profile = get_profile("default")
    tab_a, tab_b = FakePage("https://a/"), FakePage("https://b/")
    existing = FakeContext([tab_a, tab_b])
    browser = FakeBrowser([existing])
    context = await connect_browser(FakePlaywright(browser), profile, parse_spec("fake:p1"))
    ours = await context.new_page()
    assert len(existing.pages) == 3
    await context.close()
    assert ours.closed
    assert not tab_a.closed and not tab_b.closed
    assert not existing.closed
    assert tab_a.visited == [] and tab_b.visited == []
    await context.close()  # idempotent


async def test_the_last_tab_of_a_browser_is_blanked_not_closed(aistudio_home, fake_provider):
    # Closing a window's last tab can quit the browser; ShardX must keep running.
    profile = get_profile("default")
    existing = FakeContext([])
    context = await connect_browser(FakePlaywright(FakeBrowser([existing])), profile,
                                    parse_spec("fake:p1"))
    ours = await context.new_page()
    await context.close()
    assert not ours.closed and ours.visited == ["about:blank"]


async def test_a_context_aistudio_created_is_closed_with_it(aistudio_home, fake_provider):
    browser = FakeBrowser([])
    context = await connect_browser(FakePlaywright(browser), get_profile("default"),
                                    parse_spec("fake:p1"))
    assert len(browser.created) == 1
    await context.close()
    assert browser.created[0].closed


async def test_provider_failure_reports_its_stderr_without_secrets(aistudio_home, fake_provider):
    browser = FakeBrowser([FakeContext([])])
    with pytest.raises(BrowserError) as err:
        await connect_browser(FakePlaywright(browser), get_profile("default"),
                              parse_spec("fake:fail"))
    message = str(err.value)
    assert "muse-browser-fake" in message and "exit code 3" in message
    assert "ShardX Launcher API is not reachable" in message
    assert "abc123secret" not in message
    assert "muse-browser-shardx setup" in (err.value.hint or "")


async def test_a_non_local_devtools_url_is_refused(aistudio_home, fake_provider):
    with pytest.raises(BrowserError, match="not a local DevTools address"):
        await connect_browser(FakePlaywright(FakeBrowser([])), get_profile("default"),
                              parse_spec("fake:badurl"))


async def test_a_provider_that_prints_nothing_times_out(aistudio_home, fake_provider, monkeypatch):
    monkeypatch.setattr(prov, "PROVIDER_START_TIMEOUT_S", 0.3)
    monkeypatch.setattr(prov, "PROVIDER_EXIT_WAIT_S", 0.2)
    with pytest.raises(BrowserError, match="did not print a DevTools address"):
        await connect_browser(FakePlaywright(FakeBrowser([])), get_profile("default"),
                              parse_spec("fake:silent"))


async def test_a_missing_provider_says_how_to_install_it(aistudio_home, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(BrowserError, match="muse-browser-nope") as err:
        await connect_browser(FakePlaywright(FakeBrowser([])), get_profile("default"),
                              parse_spec("nope:x"))
    assert "muse-browser-shardx" in (err.value.hint or "")
    # the lock was released: another run can start
    lock = BrowserLock(get_profile("default").root / "browser.lock")
    lock.acquire()
    lock.release()


async def test_a_cdp_connection_failure_stops_the_provider(aistudio_home, fake_provider):
    class Refusing(FakeChromium):
        async def connect_over_cdp(self, url):
            raise RuntimeError("connect ECONNREFUSED 127.0.0.1:45123")

    playwright = FakePlaywright(FakeBrowser([]))
    playwright.chromium = Refusing(None)
    with pytest.raises(BrowserError, match="could not attach"):
        await connect_browser(playwright, get_profile("default"), parse_spec("fake:p1"))
    assert fake_provider.read_text() == "stopped"


async def test_a_plain_cdp_spec_needs_no_provider(aistudio_home):
    browser = FakeBrowser([FakeContext([FakePage()])])
    playwright = FakePlaywright(browser)
    context = await connect_browser(playwright, get_profile("default"),
                                    parse_spec("cdp:http://localhost:9222"))
    assert playwright.chromium.url == "http://localhost:9222"
    await context.close()
    assert browser.closed


# --- one browser run per profile -------------------------------------------------------------


async def test_a_second_run_on_the_same_profile_is_refused(aistudio_home, fake_provider):
    profile = get_profile("default")
    first = await connect_browser(FakePlaywright(FakeBrowser([FakeContext([])])), profile,
                                  parse_spec("fake:p1"))
    with pytest.raises(BrowserError, match="another aistudio command is using this profile"):
        await connect_browser(FakePlaywright(FakeBrowser([FakeContext([])])), profile,
                              parse_spec("fake:p1"))
    await first.close()
    again = await connect_browser(FakePlaywright(FakeBrowser([FakeContext([])])), profile,
                                  parse_spec("fake:p1"))
    await again.close()


def test_the_lock_is_per_profile(aistudio_home):
    a, b = BrowserLock(get_profile("a").ensure().root / "browser.lock"), BrowserLock(
        get_profile("b").ensure().root / "browser.lock")
    a.acquire()
    b.acquire()
    a.release()
    b.release()


# --- callers ----------------------------------------------------------------------------------


async def test_open_browser_uses_the_saved_spec_and_leaves_the_window_alone(
        logged_in_profile, fake_provider):
    profile = logged_in_profile()
    save_spec(profile, parse_spec("fake:p1"))
    existing = FakePage("https://example.com/")
    context = FakeContext([existing])
    browser = FakeBrowser([context])
    async with open_browser(profile, playwright_factory=lambda: FakePlaywright(browser)) as ui:
        assert isinstance(ui, PlaywrightUi)
        assert len(context.pages) == 2  # a new tab; the user's tab is not reused
    assert existing.closed is False and existing.visited == []
    assert context.pages[1].closed
    assert browser.closed and not context.closed


async def test_open_browser_needs_a_saved_login_for_an_attached_browser(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()  # Chrome's folder does not count for a ShardX profile
    save_spec(profile, parse_spec("shardx:a"))
    with pytest.raises(BrowserError, match="has no browser session"):
        async with open_browser(profile, playwright_factory=lambda: None):
            pass


async def test_login_with_an_attached_browser_saves_the_spec_and_never_wipes(
        aistudio_home, fake_provider):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    (profile.chrome_dir / "Cookies").write_text("keep")
    context = FakeContext([])
    browser = FakeBrowser([context])
    with pytest.raises(ValidationError, match="--fresh"):
        await run_login(profile, browser="fake:p1", fresh=True,
                        playwright_factory=lambda: FakePlaywright(browser))
    assert (profile.chrome_dir / "Cookies").exists() and not profile.browser_json.exists()

    page_url = {}

    class SignedInContext(FakeContext):
        async def new_page(self):
            page = await super().new_page()
            page.url = "https://aistudio.google.com/prompts/new_chat"
            page_url["page"] = page
            return page

    context = SignedInContext([])
    browser = FakeBrowser([context])
    await run_login(profile, browser="fake:p1", poll=0, key_wait=0,
                    playwright_factory=lambda: FakePlaywright(browser))
    assert load_spec(profile) == BrowserSpec(kind="provider", name="fake", arg="p1")
    assert profile.storage_state.exists()
    assert page_url["page"].closed is False  # the only tab: blanked rather than closed
    assert browser.closed
