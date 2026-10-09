import re

import pytest

from aistudio._browser.ui import PlaywrightUi
from aistudio.exceptions import AuthError, BrowserError


class El:
    def __init__(self, visible=True, label=None, text="", fail=None):
        self._visible, self._label, self._text, self._fail = visible, label, text, fail
        self.actions = []

    async def is_visible(self):
        return self._visible

    async def get_attribute(self, name):
        return self._label if name == "aria-label" else None

    async def inner_text(self):
        return self._text

    async def click(self, timeout=None):
        if self._fail:
            raise self._fail
        self.actions.append("click")

    async def fill(self, text, timeout=None):
        if self._fail:
            raise self._fail
        self.actions.append(("fill", text))

    async def press(self, key, timeout=None):
        self.actions.append(("press", key))


class Loc:
    def __init__(self, elements):
        self._elements = elements

    def nth(self, i):
        return self._elements[i]

    @property
    def first(self):
        return self._elements[0]

    async def count(self):
        return len(self._elements)

    async def all(self):
        return self._elements

    async def evaluate_all(self, script):
        # what the page script returns: visible elements' aria-label, else their text
        return [e._label or e._text for e in self._elements if e._visible]


class Keyboard:
    def __init__(self):
        self.pressed, self.typed, self.inserted = [], [], []

    async def press(self, key):
        self.pressed.append(key)

    async def type(self, text, delay=None):
        self.typed.append(text)

    async def insert_text(self, text):
        self.inserted.append(text)


class Page:
    def __init__(self, by_role, url="https://aistudio.google.com/generate-speech"):
        self._by_role = by_role
        self.url = url
        self.keyboard = Keyboard()
        self.asked = []

    def get_by_role(self, role, name=None, exact=False):
        self.asked.append((role, name, exact))
        return Loc(self._by_role.get(role, []))


async def test_click_fill_press_and_key_reach_the_page():
    box, button = El(), El()
    page = Page({"textbox": [box], "button": [El(), button]})
    ui = PlaywrightUi(page)
    await ui.click("button", re.compile("^Speaker"), nth=1)
    await ui.fill("textbox", "Speech block text", "hi")
    await ui.press("textbox", "Speech block text", "Meta+Enter")
    await ui.key("Escape")
    assert button.actions == ["click"]
    assert box.actions == [("fill", "hi"), ("press", "Meta+Enter")]
    assert page.keyboard.pressed == ["Escape"]


async def test_activate_presses_enter_on_the_chosen_control():
    # Keyboard activation: an overlay covering the control cannot intercept it (voice cards).
    first, second = El(), El()
    page = Page({"button": [first, second]})
    await PlaywrightUi(page).activate("button", "Tova", exact=True, nth=1)
    assert second.actions == [("press", "Enter")]
    assert first.actions == []
    assert page.asked[-1] == ("button", "Tova", True)


async def test_a_missing_control_is_a_browser_error_naming_it():
    page = Page({"textbox": [El(fail=TimeoutError("Timeout 30000ms exceeded"))]})
    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(page).fill("textbox", "Speech block text", "hi")
    assert "Speech block text" in info.value.message
    assert "page changed" in info.value.message and "AI Studio" not in info.value.message


async def test_role_actions_on_a_signed_out_page_are_auth_errors():
    page = Page({"button": [El()]}, url="https://accounts.google.com/signin")
    with pytest.raises(AuthError):
        await PlaywrightUi(page).click("button", "Style")


async def test_visible_and_labels_read_only_visible_elements():
    page = Page({"button": [El(label="Favorite Tova"), El(visible=False, label="Favorite Hidden"),
                            El(text="  Remove from \n favorites Fola ")]})
    ui = PlaywrightUi(page)
    assert await ui.visible("button", "Favorite Tova", exact=True) is True
    assert await ui.labels("button", re.compile("Favorite")) == [
        "Favorite Tova", "Remove from favorites Fola"]
    assert await PlaywrightUi(Page({})).visible("button", "nope") is False


class BrokenLoc(Loc):
    async def count(self):
        raise Exception("Target page, context or browser has been closed")

    async def all(self):
        raise Exception("Target page, context or browser has been closed")

    async def evaluate_all(self, script):
        raise Exception("Target page, context or browser has been closed")


class BrokenKeyboard:
    async def press(self, key):
        raise Exception("Target page, context or browser has been closed")


async def test_reads_on_a_closed_window_are_browser_errors():
    page = Page({})
    page.get_by_role = lambda role, name=None, exact=False: BrokenLoc([])
    page.keyboard = BrokenKeyboard()
    ui = PlaywrightUi(page)
    for read in (lambda: ui.visible("button", "Tova"), lambda: ui.labels("button", "Tova"),
                 lambda: ui.key("Escape")):
        with pytest.raises(BrowserError) as info:
            await read()
        assert "closed" in info.value.message


async def test_text_checked_and_url_reads():
    class Box(El):
        async def inner_text(self):
            return "  Omni 1.1 Flash \n arrow_drop_down "

        async def get_attribute(self, name):
            return None

        async def is_checked(self):  # native radios have no aria-checked (live: Flow's spending)
            return True

    page = Page({"button": [Box()], "radio": [El(), Box()]})
    ui = PlaywrightUi(page)
    assert await ui.inner_text("button", "Video generation default model") == (
        "Omni 1.1 Flash arrow_drop_down")
    assert await ui.checked("radio", "16:9", nth=1) is True
    assert ui.current_url() == page.url


async def test_read_errors_say_what_happened():
    class Slow(Loc):
        async def count(self):
            raise TimeoutError("Timeout 30000ms exceeded")

    page = Page({})
    page.get_by_role = lambda role, name=None, exact=False: Slow([])
    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(page).visible("button", "Tova")
    assert "in time" in info.value.message and "closed" not in info.value.message


def test_batched_rpcids_are_split():
    from aistudio._browser.ui import _methods

    assert _methods("https://flow.google.com/_/X/data/batchexecute?rpcids=jwpduf,ngNC2&bl=1") == [
        "jwpduf", "ngNC2"]
    assert _methods("https://a/$rpc/x.MakerSuiteService/GenerateContent") == ["GenerateContent"]


async def test_long_prompts_are_inserted_not_typed():
    class Editor(El):
        async def focus(self):
            self.actions.append("focus")

    editor = Editor()
    page = Page({})
    page.locator = lambda selector: Loc([editor])
    ui = PlaywrightUi(page)
    await ui.type_text(".ProseMirror", "short")
    await ui.type_text(".ProseMirror", "x" * 2000)
    assert page.keyboard.typed == ["short"] and page.keyboard.inserted == ["x" * 2000]


async def test_a_tile_below_the_window_is_scrolled_into_view_before_hovering():
    # Live (plan E review): with more media, a tile sat below the 1000 px viewport and the hover
    # never reached it, so its ⋮ menu never appeared.
    class Caption:
        def __init__(self):
            self.steps = []

        @property
        def first(self):
            return self

        async def scroll_into_view_if_needed(self, timeout=None):
            self.steps.append("scroll")

        async def bounding_box(self, timeout=None):
            self.steps.append("measure")
            return {"x": 100, "y": 400 if "scroll" in self.steps else 1667}

    class Mouse:
        def __init__(self):
            self.moved = []

        async def move(self, x, y):
            self.moved.append((x, y))

    caption = Caption()
    page = Page({})
    page.mouse = Mouse()
    page.get_by_text = lambda text, exact=False: caption
    await PlaywrightUi(page).hover_text("apple.jpg")
    assert caption.steps == ["scroll", "measure"] and page.mouse.moved == [(120, 300)]


async def test_enabled_reads_aria_disabled_in_page_order():
    class Radio(El):
        def __init__(self, disabled):
            super().__init__()
            self._disabled = disabled

        async def get_attribute(self, name):
            return "true" if name == "aria-disabled" and self._disabled else None

        async def is_enabled(self):
            return True

    page = Page({"radio": [Radio(True), Radio(False)]})
    assert await PlaywrightUi(page).enabled("radio", "Approve") == [False, True]
    assert page.asked[-1] == ("radio", "Approve", True)



async def test_set_files_reaches_the_input():
    class Input:
        def __init__(self):
            self.files = None

        @property
        def first(self):
            return self

        async def set_input_files(self, files, timeout=None):
            self.files = files

    box = Input()
    page = Page({})
    page.locator = lambda selector: box
    await PlaywrightUi(page).set_files("input[type=file]", ["/a.png", "/b.png"])
    assert box.files == ["/a.png", "/b.png"]
