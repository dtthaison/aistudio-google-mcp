import pytest

from aistudio._browser.ui import VIEWPORT, PlaywrightUi, open_browser
from aistudio.exceptions import BrowserError
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
    def __init__(self, elements=()):
        self._elements = list(elements)
        self.handlers = []

    def locator(self, selector):
        return Locator(self._elements)

    def on(self, event, handler):
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)


async def test_fill_first_uses_the_first_visible_element():
    hidden, shown = Element(False), Element(True)
    await PlaywrightUi(Page([hidden, shown])).fill_first("textarea", "hi", timeout_s=1)
    assert (hidden.filled, shown.filled) == (None, "hi")


async def test_missing_element_is_a_clear_browser_error():
    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(Page([Element(False)])).fill_first("textarea", "hi", timeout_s=0.1)
    assert "AI Studio page changed or did not load" in info.value.message


async def test_responses_are_logged_per_method_until_the_block_ends():
    page = Page()
    ui = PlaywrightUi(page)
    async with ui.responses() as log:
        base = "https://x.clients6.google.com/$rpc/s.MakerSuiteService/"
        for handler in page.handlers:
            handler(Response(base + "CreateApplet", text='["app"]'))
            handler(Response("https://www.gstatic.com/x.js"))
        got = await log.next("CreateApplet", timeout_s=1)
        assert (got.method, got.status, got.text, got.request_body) == (
            "CreateApplet", 200, '["app"]', "[1]")
        assert await log.next("CodeAssistantOffline", timeout_s=0.05) is None
    assert page.handlers == []


class Cdp:
    def __init__(self):
        self.sent = []

    async def send(self, method, params=None):
        self.sent.append((method, params))
        return {"windowId": 7}


class Context:
    def __init__(self):
        self.pages = ["page"]
        self.closed = False
        self.cdp = Cdp()

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


async def test_visible_window_is_not_minimized(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = Context()
    async with open_browser(profile, window="visible",
                            playwright_factory=lambda: Playwright(Chromium(context))):
        pass
    assert context.cdp.sent == []


async def test_open_browser_requires_a_browser_session(aistudio_home):
    with pytest.raises(BrowserError) as info:
        async with open_browser(get_profile("default"), playwright_factory=lambda: None):
            pass
    assert info.value.hint == 'run "aistudio login" first'
