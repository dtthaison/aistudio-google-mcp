import pytest

from aistudio._browser.ui import VIEWPORT, PlaywrightUi, open_browser
from aistudio.exceptions import AuthError, BrowserError
from aistudio.paths import get_profile


class Element:
    def __init__(self, visible):
        self._visible = visible
        self.filled = None

    async def is_visible(self):
        return self._visible

    async def fill(self, text):
        self.filled = text


class Locator:
    def __init__(self, elements):
        self._elements = elements

    async def all(self):
        return self._elements


class Response:
    def __init__(self, url, status=200, text="[]"):
        self.url = url
        self.status = status
        self._text = text
        self.request = type("Req", (), {"post_data": "[1]"})()

    async def text(self):
        return self._text


class Page:
    def __init__(self, elements=(), url="https://aistudio.google.com/apps"):
        self._elements = list(elements)
        self.handlers = {}
        self.url = url

    def locator(self, selector):
        return Locator(self._elements)

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.handlers[event].remove(handler)

    async def goto(self, url):
        pass


async def test_fill_first_uses_the_first_visible_element():
    hidden, shown = Element(False), Element(True)
    await PlaywrightUi(Page([hidden, shown])).fill_first("textarea", "hi", timeout_s=1)
    assert (hidden.filled, shown.filled) == (None, "hi")


async def test_missing_element_is_a_clear_browser_error():
    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(Page([Element(False)])).fill_first("textarea", "hi", timeout_s=0.1)
    assert "The page changed or did not load" in info.value.message


async def test_responses_are_logged_per_method_until_the_block_ends():
    page = Page()
    ui = PlaywrightUi(page)
    async with ui.responses() as log:
        base = "https://x.clients6.google.com/$rpc/s.MakerSuiteService/"
        for handler in page.handlers["response"]:
            handler(Response(base + "CreateApplet", text='["app"]'))
            handler(Response("https://www.gstatic.com/x.js"))
        got = await log.next("CreateApplet", timeout_s=1)
        assert (got.method, got.status, got.text, got.request_body) == (
            "CreateApplet", 200, '["app"]', "[1]")
        assert await log.next("CodeAssistantOffline", timeout_s=0.05) is None
    assert page.handlers == {"response": [], "close": []}


class Cdp:
    def __init__(self, bounds=None):
        self.sent = []
        self.bounds = bounds or {"width": 1600, "height": 1000}

    async def send(self, method, params=None):
        self.sent.append((method, params))
        if method == "Browser.getWindowBounds":
            return {"bounds": self.bounds}
        return {"windowId": 7}


class Context:
    def __init__(self, jar=None, on_cookies=None):
        self.pages = ["page"]
        self.closed = False
        self.cdp = Cdp()
        self.jar, self.on_cookies = jar, on_cookies

    async def cookies(self):
        if self.on_cookies:
            await self.on_cookies()
        if self.jar is None:
            raise RuntimeError("no cookies")
        return self.jar

    async def storage_state(self):
        # Final review M1: opens a helper page and loads every visited origin; never used
        raise AssertionError("the session save must read cookies only")

    async def new_cdp_session(self, page):
        return self.cdp

    async def close(self):
        self.closed = True


class Chromium:
    def __init__(self, context):
        self.context = context
        self.kwargs = None

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.kwargs = kwargs
        return self.context


class Playwright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_open_browser_minimizes_a_desktop_sized_window_and_closes(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = Context()
    chromium = Chromium(context)
    async with open_browser(profile, playwright_factory=lambda: Playwright(chromium)) as ui:
        assert isinstance(ui, PlaywrightUi)
    assert chromium.kwargs["viewport"] == VIEWPORT
    assert chromium.kwargs["headless"] is False
    assert context.cdp.sent[-1] == (
        "Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "minimized"}})
    assert context.closed


async def test_a_visible_window_follows_the_screen_like_normal_chrome(aistudio_home):
    # User report: a fixed 1600x1000 page was larger than a smaller screen, so pop-ups on the
    # right were cut off. Visible windows get no fixed viewport and open maximized.
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = Context()
    chromium = Chromium(context)
    async with open_browser(profile, window="visible",
                            playwright_factory=lambda: Playwright(chromium)):
        pass
    assert chromium.kwargs.get("no_viewport") is True and "viewport" not in chromium.kwargs
    assert context.cdp.sent[-1] == (
        "Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "maximized"}})


async def test_open_browser_requires_a_browser_session(aistudio_home):
    with pytest.raises(BrowserError) as info:
        async with open_browser(get_profile("default"), playwright_factory=lambda: None):
            pass
    assert info.value.hint == 'run "aistudio login" first'


class Button:
    def __init__(self, states):
        self._states = list(states)

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]


class RolePage(Page):
    def __init__(self, button):
        super().__init__()
        self._button = button

    def get_by_role(self, role, name=None, exact=False):
        return Locator([self._button])


async def test_wait_enabled_returns_once_the_button_enables():
    await PlaywrightUi(RolePage(Button([False, False, True]))).wait_enabled(
        "button", "Send", timeout_s=2)


async def test_wait_enabled_times_out_with_a_browser_error():
    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(RolePage(Button([False]))).wait_enabled("button", "Send", timeout_s=0.1)
    assert "'Send'" in info.value.message


async def test_a_closed_window_ends_the_wait_with_a_browser_error():
    page = Page()
    async with PlaywrightUi(page).responses() as log:
        for handler in page.handlers["close"]:
            handler(page)
        with pytest.raises(BrowserError) as info:
            await log.next("StreamCodeAssistantOfflineGeneration", timeout_s=5)
    assert "closed" in info.value.message


async def test_a_signed_out_profile_is_an_auth_error_not_a_ui_change():
    page = Page([Element(False)], url="https://accounts.google.com/v3/signin/identifier")
    with pytest.raises(AuthError) as info:
        await PlaywrightUi(page).fill_first("textarea", "hi", timeout_s=5)
    assert info.value.hint == 'run "aistudio login"'


async def test_navigation_failures_are_browser_errors():
    class Broken(Page):
        async def goto(self, url):
            raise RuntimeError("net::ERR_INTERNET_DISCONNECTED")

    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(Broken()).goto("https://aistudio.google.com/apps")
    assert "ERR_INTERNET_DISCONNECTED" in info.value.message




def cookie(name, value):
    return {"name": name, "value": value, "domain": ".google.com", "path": "/", "expires": -1,
            "httpOnly": True, "secure": True, "sameSite": "Lax"}


FRESH = [cookie("SID", "new-sid"), cookie("SAPISID", "new-sapisid")]


async def run_browser(profile, context):
    profile.chrome_dir.mkdir(parents=True, exist_ok=True)
    async with open_browser(profile, playwright_factory=lambda: Playwright(Chromium(context))):
        pass
    assert context.closed  # a failed save never keeps Chrome open


async def test_a_browser_run_saves_the_refreshed_session(aistudio_home):
    # Live: browser runs rotate Google's cookies, and the stored copy (used by plain HTTP
    # commands) went stale hours after a login: "Request had invalid authentication credentials".
    import json

    from aistudio._auth.storage import AccountInfo, save_state
    from aistudio.paths import get_profile

    profile = get_profile("default")
    origin = {"origin": "https://aistudio.google.com", "localStorage": []}
    save_state(profile, {"cookies": [cookie("SID", "old")], "origins": [origin]},
               AccountInfo(email="me@example.com", authuser=1))
    other = {**cookie("track", "x"), "domain": ".example.com"}
    await run_browser(profile, Context(jar=[*FRESH, other]))
    saved = json.loads(profile.storage_state.read_text())
    assert {c["name"]: c["value"] for c in saved["cookies"]} == {"SID": "new-sid",
                                                                 "SAPISID": "new-sapisid"}
    assert saved["origins"] == [origin]  # only cookies are refreshed
    assert saved["aistudio"] == {"email": "me@example.com", "authuser": 1}


async def test_a_signed_out_browser_never_overwrites_the_session(logged_in_profile, caplog):
    import logging

    profile = logged_in_profile()
    before = profile.storage_state.read_text()
    await run_browser(profile, Context(jar=[cookie("NID", "x")]))
    assert profile.storage_state.read_text() == before
    with caplog.at_level(logging.DEBUG, logger="aistudio._browser.ui"):
        await run_browser(profile, Context(jar=None))  # reading fails: the run still ends
    assert profile.storage_state.read_text() == before
    assert "could not save the browser session (RuntimeError)" in caplog.text


async def test_the_session_save_never_creates_or_repairs_a_file(aistudio_home):
    from aistudio.paths import get_profile

    fresh_profile = get_profile("new")
    await run_browser(fresh_profile, Context(jar=FRESH))
    assert not fresh_profile.storage_state.exists()  # never logged in: nothing to refresh
    broken = get_profile("broken")
    broken.ensure()
    broken.storage_state.write_text("not json")
    await run_browser(broken, Context(jar=FRESH))
    assert broken.storage_state.read_text() == "not json"


async def test_a_logout_during_the_run_is_not_undone(logged_in_profile):
    # Final review M2: another session's "auth logout" while Chrome runs must stay logged out.
    profile = logged_in_profile()

    async def logout():
        profile.storage_state.unlink()

    await run_browser(profile, Context(jar=FRESH, on_cookies=logout))
    assert not profile.storage_state.exists()


async def test_a_hanging_cookie_read_does_not_hang_the_run(logged_in_profile, monkeypatch):
    import asyncio

    from aistudio._browser import ui as ui_module

    monkeypatch.setattr(ui_module, "SESSION_SAVE_TIMEOUT_S", 0.01)
    profile = logged_in_profile()
    before = profile.storage_state.read_text()

    async def hang():
        await asyncio.sleep(60)

    await asyncio.wait_for(run_browser(profile, Context(jar=FRESH, on_cookies=hang)), 5)
    assert profile.storage_state.read_text() == before


async def test_an_unreadable_file_is_a_clear_error(tmp_path):
    # Final review M10: the file vanished after the size check.
    from aistudio.exceptions import ValidationError

    with pytest.raises(ValidationError) as info:
        await PlaywrightUi(Page()).set_files("input[type=file]", [str(tmp_path / "gone.mp4")],
                                             {".mp4": "audio/mp4"})
    assert "cannot read" in info.value.message


async def test_set_files_labels_given_types(tmp_path):
    # Live: AI Studio's audio input takes audio/* only; Playwright labels .mp4/.webm video/*.
    clip, other = tmp_path / "talk.mp4", tmp_path / "talk.wav"
    clip.write_bytes(b"mp4 bytes")
    other.write_bytes(b"RIFF")
    given = []

    class Input:
        async def set_input_files(self, files, timeout=None):
            given.append(files)

    class FilePage(Page):
        def locator(self, selector):
            return type("L", (), {"first": Input()})()

    ui = PlaywrightUi(FilePage())
    await ui.set_files("input[type=file]", [str(clip)], {".mp4": "audio/mp4"})
    assert given[-1] == [{"name": "talk.mp4", "mimeType": "audio/mp4", "buffer": b"mp4 bytes"}]
    await ui.set_files("input[type=file]", [str(other)], {".mp4": "audio/mp4"})
    assert given[-1] == [str(other)]  # nothing to relabel: Playwright's own type
    await ui.set_files("input[type=file]", [str(clip), str(other)], {".mp4": "audio/mp4"})
    first, second = given[-1]
    assert first["mimeType"] == "audio/mp4" and second["name"] == "talk.wav"
    assert second["mimeType"].startswith("audio/")  # the platform's guess for .wav
