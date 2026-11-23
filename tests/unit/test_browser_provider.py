"""An attached browser (ShardX through muse-browser-shardx): specs, the provider, close rules."""

import asyncio
import json
import os
import stat
import sys
import time
from types import SimpleNamespace

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
if mode not in ("fail", "badurl", "silent", "orphan", "stubborn", "longfail"):
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
elif mode == "orphan":  # the leader exits but leaves a tunnel in its group
    import subprocess
    child = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(os.environ["FAKE_CHILD"], "w").write(str(child.pid))
    print("{url}", flush=True)
elif mode == "stubborn":  # ignores stdin EOF and SIGTERM
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("{url}", flush=True)
    while True:
        time.sleep(0.1)
elif mode == "longfail":
    sys.stderr.write("x" * 200000 + "\\n")  # over asyncio's 64 KiB line limit
    sys.stderr.write("boom api_token=zzz\\n")
    sys.exit(2)
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
        async def storage_state(self):
            raise AssertionError("storage_state() would visit the user's other tabs")

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


# --- review fixes -----------------------------------------------------------------------------


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _dies(pid, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_stopping_sweeps_a_tunnel_left_by_a_provider_that_already_exited(
        aistudio_home, fake_provider, monkeypatch, tmp_path):
    child_file = tmp_path / "child"
    monkeypatch.setenv("FAKE_CHILD", str(child_file))
    context = await connect_browser(FakePlaywright(FakeBrowser([FakeContext([])])),
                                    get_profile("default"), parse_spec("fake:orphan"))
    await asyncio.sleep(0.3)  # the leader has exited by now
    pid = int(child_file.read_text())
    assert _alive(pid)
    await context.close()
    assert await _dies(pid)


async def test_a_cancelled_stop_still_kills_the_provider(aistudio_home, fake_provider,
                                                          monkeypatch):
    monkeypatch.setattr(prov, "PROVIDER_EXIT_WAIT_S", 5.0)
    _url, provider = await prov.start_provider(parse_spec("fake:stubborn"))
    pid = provider.proc.pid
    task = asyncio.ensure_future(provider.stop())
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _dies(pid)


async def test_a_provider_that_ignores_eof_and_sigterm_is_killed(aistudio_home, fake_provider,
                                                                  monkeypatch):
    monkeypatch.setattr(prov, "PROVIDER_EXIT_WAIT_S", 0.3)
    _url, provider = await prov.start_provider(parse_spec("fake:stubborn"))
    await provider.stop()
    assert provider.proc.returncode is not None


async def test_a_stderr_line_over_64k_does_not_stop_the_draining(aistudio_home, fake_provider):
    with pytest.raises(BrowserError) as err:
        await connect_browser(FakePlaywright(FakeBrowser([])), get_profile("default"),
                              parse_spec("fake:longfail"))
    assert "boom" in str(err.value) and "zzz" not in str(err.value)
    assert len(str(err.value)) < 2000


async def test_close_runs_every_step_even_when_cancelled(aistudio_home, fake_provider):
    profile = get_profile("default")
    browser = FakeBrowser([FakeContext([])])
    context = await connect_browser(FakePlaywright(browser), profile, parse_spec("fake:p1"))
    page = await context.new_page()
    await context.new_page()

    async def cancelled():
        raise asyncio.CancelledError

    page.close = cancelled
    with pytest.raises(asyncio.CancelledError):
        await context.close()
    assert browser.closed
    assert fake_provider.read_text() == "stopped"
    BrowserLock(profile.root / "browser.lock").acquire()  # the lock was released


async def test_open_browser_still_closes_when_the_session_save_is_cancelled(
        logged_in_profile, fake_provider, monkeypatch):
    profile = logged_in_profile()
    save_spec(profile, parse_spec("fake:p1"))
    browser = FakeBrowser([FakeContext([FakePage()])])

    async def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr("aistudio._browser.ui._save_session", cancelled)
    with pytest.raises(asyncio.CancelledError):
        async with open_browser(profile, playwright_factory=lambda: FakePlaywright(browser)):
            pass
    assert browser.closed and fake_provider.read_text() == "stopped"


async def test_a_failure_opening_the_tab_is_a_browser_error(aistudio_home, fake_provider):
    class BadContext(FakeContext):
        async def new_page(self):
            raise RuntimeError("Target closed token=abc")

    context = await connect_browser(FakePlaywright(FakeBrowser([BadContext([])])),
                                    get_profile("default"), parse_spec("fake:p1"))
    with pytest.raises(BrowserError, match="could not open a tab") as err:
        await prov.page_to_drive(context)
    assert "abc" not in str(err.value)
    await context.close()


async def test_a_context_that_cannot_be_opened_is_a_browser_error(aistudio_home, fake_provider):
    class BadBrowser(FakeBrowser):
        async def new_context(self):
            raise RuntimeError("nope")

    with pytest.raises(BrowserError, match="could not open a browser context"):
        await connect_browser(FakePlaywright(BadBrowser([])), get_profile("default"),
                              parse_spec("fake:p1"))
    assert fake_provider.read_text() == "stopped"


@pytest.mark.parametrize("raw", [
    "launcher error: api_token=sx_secret",
    "access_token: sx_secret retry",
    '{"token": "sx_secret"}',
    "Authorization: Bearer sx_secret",
    "Cookie: SID=sx_secret; SAPISID=other_secret",
    "Set-Cookie: SID=sx_secret; Path=/",
    "x-goog-api-key: sx_secret",
    "SAPISID=sx_secret",
    "dial https://user:sx_secret@host/x failed",
    "http://127.0.0.1:9222/devtools/browser/id?token=sx_secret&x=1",
])
def test_redact_masks_credential_looking_values(raw):
    out = prov.redact(raw)
    assert "sx_secret" not in out and "other_secret" not in out and "[redacted]" in out


def test_redact_leaves_plain_text_alone():
    text = "ShardX Launcher API is not reachable at 127.0.0.1:40325 (inside the sandbox)"
    assert prov.redact(text) == text


def test_a_rejected_devtools_url_is_redacted_in_the_error():
    with pytest.raises(BrowserError) as err:
        prov.check_devtools_url("https://u:sx_secret@evil.example.com/?token=sx_secret")
    assert "sx_secret" not in str(err.value)


async def test_attached_browsers_need_a_posix_system(aistudio_home, monkeypatch):
    monkeypatch.setattr(prov, "_is_posix", lambda: False)
    with pytest.raises(BrowserError, match="macOS or Linux"):
        await connect_browser(FakePlaywright(FakeBrowser([])), get_profile("default"),
                              parse_spec("cdp:http://127.0.0.1:9222"))
    assert not (get_profile("default").root / "browser.lock").exists()


# --- capture and login on a shared browser ---------------------------------------------------


class CapturePage(FakePage):
    def __init__(self):
        super().__init__()
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)


class CaptureContext(FakeContext):
    def __init__(self, pages, goto):
        super().__init__(pages)
        self._goto = goto
        self.subscribed = []
        self.listeners = {}

    def on(self, event, handler):
        self.subscribed.append(event)
        self.listeners[event] = handler

    async def new_page(self):
        page = CapturePage()
        page.goto = lambda url, **kwargs: self._goto(page, url)
        self.pages.append(page)
        return page


def _recorder():
    handled = []

    async def handle(request):
        handled.append(request)

    return SimpleNamespace(handle=handle), handled


async def test_capture_failure_before_recording_still_cleans_up(logged_in_profile,
                                                                 fake_provider):
    from aistudio._dev.capture import run_capture

    profile = logged_in_profile()
    save_spec(profile, parse_spec("fake:p1"))

    async def failing_goto(page, url):
        raise RuntimeError("net::ERR_PROXY_CONNECTION_FAILED")

    user_tab = FakePage("https://user-tab/")
    context = CaptureContext([user_tab], failing_goto)
    browser = FakeBrowser([context])
    recorder, _ = _recorder()
    with pytest.raises(RuntimeError, match="ERR_PROXY"):
        await run_capture(profile, recorder, playwright_factory=lambda: FakePlaywright(browser))
    assert browser.closed and fake_provider.read_text() == "stopped"
    assert user_tab.closed is False
    assert context.pages[1].closed  # the tab opened for the capture is gone
    BrowserLock(profile.root / "browser.lock").acquire()


async def test_capture_records_only_its_own_tab_of_a_shared_browser(logged_in_profile,
                                                                     fake_provider):
    from aistudio._dev.capture import run_capture

    profile = logged_in_profile()
    save_spec(profile, parse_spec("fake:p1"))
    user_tab = CapturePage()

    async def goto(page, url):
        own = SimpleNamespace(frame=SimpleNamespace(page=page))
        foreign = SimpleNamespace(frame=SimpleNamespace(page=user_tab))
        worker = SimpleNamespace(frame=None)  # a service worker has no page
        for request in (foreign, own, worker):
            context.listeners["requestfinished"](request)
        asyncio.get_running_loop().call_soon(lambda: [h(page) for h in page.handlers["close"]])

    context = CaptureContext([user_tab], goto)
    browser = FakeBrowser([context])
    recorder, handled = _recorder()
    await asyncio.wait_for(
        run_capture(profile, recorder, playwright_factory=lambda: FakePlaywright(browser)), 3)
    assert len(handled) == 1 and handled[0].frame.page is context.pages[1]
    assert "page" not in context.subscribed  # tabs the user opens meanwhile are not tracked
    assert user_tab.closed is False and not user_tab.handlers


def test_login_command_rejects_fresh_for_an_attached_browser_before_opening(aistudio_home):
    from click.testing import CliRunner

    from aistudio.cli.main import cli

    result = CliRunner().invoke(cli, ["login", "--browser", "shardx:x", "--fresh"])
    assert result.exit_code != 0
    assert "Opening" not in result.output and "--fresh" in result.output
