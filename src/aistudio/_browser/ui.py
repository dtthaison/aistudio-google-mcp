"""A narrow UI-automation interface over a Playwright page (spec §5.4 page driving).

Page drivers (BuildPage, ...) talk to ``Ui``; ``PlaywrightUi`` is the thin real implementation
and tests use a scripted fake. Generation requests carry a content-bound BotGuard token, so they
must be sent by AI Studio's own page; a minimized window is accepted, headless is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from urllib.parse import parse_qs, urlparse

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio._auth.storage import (
    account_info,
    filter_state,
    load_state,
    missing_cookies,
    save_state,
)
from aistudio.exceptions import AIStudioError, AuthError, BrowserError
from aistudio.paths import Profile

TYPE_LIMIT = 200  # longer text is inserted at once (typing 2 KB at 10 ms/key is slow)
VIEWPORT = {"width": 1600, "height": 1000}  # narrower hides parts of AI Studio's UI
RUN_SHORTCUT = "Meta+Enter" if sys.platform == "darwin" else "Control+Enter"
RPC_MARKER = "MakerSuiteService/"
CONTROL_TIMEOUT_MS = 30_000
Name = str | re.Pattern[str]


T = TypeVar("T")

def _describe(role: str, name: Name) -> str:
    return f"{role} {name.pattern if isinstance(name, re.Pattern) else repr(name)}"


@dataclass(frozen=True)
class CapturedResponse:
    method: str
    status: int
    text: str
    request_body: str


class ResponseLog(Protocol):
    async def next(self, method: str, *, timeout_s: float) -> CapturedResponse | None: ...


class Ui(Protocol):
    async def goto(self, url: str) -> None: ...

    async def fill_first(self, selector: str, text: str, *, timeout_s: float = 30.0) -> None: ...

    async def press_first(self, selector: str, key: str) -> None: ...

    async def wait_enabled(self, role: str, name: Name, *, timeout_s: float = 30.0) -> None: ...

    async def click(self, role: str, name: Name, *, exact: bool = False, nth: int = 0) -> None: ...

    async def fill(self, role: str, name: Name, text: str) -> None: ...

    async def activate(self, role: str, name: Name, *, exact: bool = False,
                       nth: int = 0) -> None: ...

    async def press(self, role: str, name: Name, key: str) -> None: ...

    async def key(self, key: str) -> None: ...

    async def visible(self, role: str, name: Name, *, exact: bool = False) -> bool: ...

    async def labels(self, role: str, name: Name) -> list[str]: ...

    async def enabled(self, role: str, name: Name) -> list[bool]: ...

    async def has_text(self, text: str) -> bool: ...

    async def pause(self, ms: int) -> None: ...

    def responses(self, marker: str = ...) -> contextlib.AbstractAsyncContextManager[ResponseLog]:
        ...

    async def type_text(self, selector: str, text: str) -> None: ...

    async def text_of(self, selector: str) -> str: ...

    async def inner_text(self, role: str, name: Name, *, exact: bool = False,
                         nth: int = 0) -> str: ...

    async def checked(self, role: str, name: Name, *, nth: int = 0) -> bool: ...

    async def wait_for(self, selector: str, *, timeout_s: float = 30.0) -> None: ...

    def current_url(self) -> str: ...

    async def dispatch_click(self, role: str, name: Name, *, exact: bool = False) -> None: ...

    async def batch_rpc(self, rpcid: str, args_json: str) -> tuple[int, str]: ...

    async def choose_file(self, role: str, name: Name, path: Any) -> None: ...

    async def set_files(self, selector: str, paths: list[str]) -> None: ...

    async def hover_text(self, text: str) -> None: ...

    async def click_text(self, text: str) -> None: ...

    async def dispatch_click_near(self, role: str, name: Name, text: str) -> None: ...


_BATCH_RPC_JS = """async ([rpcid, args]) => {
  const g = window.WIZ_global_data || {};
  const params = new URLSearchParams({
    rpcids: rpcid, 'source-path': location.pathname, bl: g.cfb2h || '', 'f.sid': g.FdrFJe || '',
    hl: 'en', rt: 'c'});
  const body = new URLSearchParams({'f.req': JSON.stringify([[[rpcid, args, null, 'generic']]]),
                                    at: g.SNlM0e || ''});
  const r = await fetch('/_/AiSandboxAngularFrontend/data/batchexecute?' + params, {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
    body});
  return [r.status, await r.text()];
}"""


def _methods(url: str) -> list[str]:
    """The RPCs a response answers: batchexecute's ``rpcids`` (comma-separated when batched),
    else the URL's last segment."""
    parsed = urlparse(url)
    rpc = parse_qs(parsed.query).get("rpcids")
    if rpc:
        return [name for name in rpc[0].split(",") if name]
    return [parsed.path.rsplit("/", 1)[-1]]


async def _collapsed(locator: Any) -> str:
    return " ".join(((await locator.inner_text()) or "").split())


async def _aria_checked(locator: Any) -> bool:
    """aria-checked radios and native ``<input type=radio>`` alike (Flow uses both)."""
    return bool(await locator.is_checked())


def _window_closed() -> BrowserError:
    return BrowserError(
        "the browser window was closed before aistudio finished",
        hint="re-run and leave the (minimized) Chrome window open until aistudio is done",
    )


class _QueueLog:
    def __init__(self) -> None:
        self._queues: defaultdict[str, asyncio.Queue[CapturedResponse]] = defaultdict(
            asyncio.Queue
        )
        self._closed = asyncio.Event()

    def put(self, response: CapturedResponse) -> None:
        self._queues[response.method].put_nowait(response)

    def close(self) -> None:
        self._closed.set()

    async def next(self, method: str, *, timeout_s: float) -> CapturedResponse | None:
        """The next response for ``method``; None on timeout; BrowserError if the page closed."""
        queue = self._queues[method]
        if not queue.empty():
            return queue.get_nowait()
        if self._closed.is_set():
            raise _window_closed()
        getter = asyncio.ensure_future(queue.get())
        closer = asyncio.ensure_future(self._closed.wait())
        try:
            done, _ = await asyncio.wait({getter, closer}, timeout=max(timeout_s, 0),
                                         return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (getter, closer):
                if not task.done():
                    task.cancel()
        if getter in done:
            return getter.result()
        if closer in done:
            raise _window_closed()
        return None


class PlaywrightUi:
    def __init__(self, page: Any) -> None:
        self._page = page

    def _check_signed_in(self) -> None:
        host = urlparse(getattr(self._page, "url", "") or "").hostname or ""
        if host.startswith("accounts."):
            raise AuthError("Chrome is signed out: the site redirected to Google sign-in",
                            hint='run "aistudio login"')

    async def goto(self, url: str) -> None:
        try:
            await self._page.goto(url)
        except Exception as exc:  # playwright errors: timeouts, network, closed target
            raise BrowserError(f"could not open {url}: {exc}",
                               hint="check your connection and re-run") from exc
        self._check_signed_in()

    async def _first_visible(self, selector: str, timeout_s: float) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            self._check_signed_in()
            for element in await self._page.locator(selector).all():
                if await element.is_visible():
                    return element
            if loop.time() >= deadline:
                raise BrowserError(
                    f"The page changed or did not load: no visible {selector!r}",
                    hint="re-run; if it persists, the site's UI changed and aistudio "
                         "needs an update",
                )
            await asyncio.sleep(0.25)

    async def fill_first(self, selector: str, text: str, *, timeout_s: float = 30.0) -> None:
        element = await self._first_visible(selector, timeout_s)
        try:
            await element.fill(text)
        except Exception as exc:
            raise BrowserError(f"could not type into {selector!r}: {exc}") from exc

    async def press_first(self, selector: str, key: str) -> None:
        element = await self._first_visible(selector, 5.0)
        try:
            await element.press(key)
        except Exception as exc:
            raise BrowserError(f"could not press {key} in {selector!r}: {exc}") from exc

    async def wait_enabled(self, role: str, name: Name, *, timeout_s: float = 30.0) -> None:
        """Wait until a visible control is enabled (AI Studio disables Send while loading)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            for element in await self._page.get_by_role(role, name=name, exact=True).all():
                if await element.is_visible() and await element.is_enabled():
                    return
            if loop.time() >= deadline:
                raise BrowserError(
                    f"The page's {name!r} {role} did not become available "
                    f"within {timeout_s:.0f}s",
                    hint="re-run; if it persists, the site's UI changed",
                )
            await asyncio.sleep(0.25)

    def _role(self, role: str, name: Name, exact: bool = False) -> Any:
        return self._page.get_by_role(role, name=name, exact=exact)

    async def _act(self, role: str, name: Name, run: Callable[[], Any]) -> None:
        self._check_signed_in()
        try:
            await run()
        except Exception as exc:  # playwright timeouts: the control is missing or hidden
            raise BrowserError(
                f"The page changed or did not load: {_describe(role, name)} not found "
                f"({type(exc).__name__})",
                hint="re-run; if it persists, the site's UI changed and aistudio needs an "
                     "update",
            ) from exc

    async def click(self, role: str, name: Name, *, exact: bool = False, nth: int = 0) -> None:
        locator = self._role(role, name, exact).nth(nth)
        await self._act(role, name, lambda: locator.click(timeout=CONTROL_TIMEOUT_MS))

    async def fill(self, role: str, name: Name, text: str) -> None:
        locator = self._role(role, name).first
        await self._act(role, name, lambda: locator.fill(text, timeout=CONTROL_TIMEOUT_MS))

    async def press(self, role: str, name: Name, key: str) -> None:
        locator = self._role(role, name).first
        await self._act(role, name, lambda: locator.press(key, timeout=CONTROL_TIMEOUT_MS))

    async def activate(self, role: str, name: Name, *, exact: bool = False, nth: int = 0) -> None:
        """Focus a control and press Enter; unlike a click, a covering overlay cannot catch it."""
        locator = self._role(role, name, exact).nth(nth)
        await self._act(role, name, lambda: locator.press("Enter", timeout=CONTROL_TIMEOUT_MS))

    async def key(self, key: str) -> None:
        await self._read(lambda: self._page.keyboard.press(key))

    async def visible(self, role: str, name: Name, *, exact: bool = False) -> bool:
        locator = self._role(role, name, exact)

        async def read() -> bool:
            return await locator.count() > 0 and await locator.first.is_visible()

        return await self._read(read)

    async def has_text(self, text: str) -> bool:
        """Whether an element with exactly this text is on the page (e.g. a tile's caption)."""
        return await self._read(lambda: self._page.get_by_text(text, exact=True).count()) > 0

    async def enabled(self, role: str, name: Name) -> list[bool]:
        """For each control with exactly this name, in page order (the order ``nth`` uses):
        visible and not disabled (Flow keeps answered permission radios, aria-disabled)."""
        async def read() -> list[bool]:
            out: list[bool] = []
            for element in await self._role(role, name, True).all():
                out.append(await element.is_visible()
                           and await element.get_attribute("aria-disabled") != "true"
                           and await element.is_enabled())
            return out

        return await self._read(read)

    async def labels(self, role: str, name: Name) -> list[str]:
        async def read() -> list[str]:
            out: list[str] = []
            for element in await self._role(role, name).all():
                if await element.is_visible():
                    text = await element.get_attribute("aria-label") or await element.inner_text()
                    out.append(" ".join((text or "").split()))
            return out

        return await self._read(read)

    async def _read(self, run: Callable[[], Awaitable[T]]) -> T:
        """Run a page read; a window closed mid-read becomes a BrowserError, not a raw error."""
        try:
            return await run()
        except AIStudioError:
            raise
        except Exception as exc:  # playwright errors: a closed window, or a slow page
            if "closed" in str(exc).lower():
                raise _window_closed() from exc
            raise BrowserError("the page did not show what aistudio expected in time",
                               hint="re-run; if it persists, add --visible to watch it") from exc

    async def pause(self, ms: int) -> None:
        await self._page.wait_for_timeout(ms)

    async def type_text(self, selector: str, text: str) -> None:
        """Type into a rich-text editor, which ignores ``fill`` (Flow's prompt box)."""
        element = await self._first_visible(selector, 30.0)
        try:
            await element.focus()
            if len(text) > TYPE_LIMIT:  # long prompts: one input event, not one per key
                await self._page.keyboard.insert_text(text)
            else:
                await self._page.keyboard.type(text, delay=10)
        except Exception as exc:
            raise BrowserError(f"could not type into {selector!r}: {exc}") from exc

    async def text_of(self, selector: str) -> str:
        """The last visible match's text, collapsed; "" when nothing matches."""
        async def read() -> str:
            found = ""
            for element in await self._page.locator(selector).all():
                if await element.is_visible():
                    found = " ".join((await element.inner_text()).split())
            return found

        return await self._read(read)

    async def inner_text(self, role: str, name: Name, *, exact: bool = False,
                         nth: int = 0) -> str:
        locator = self._role(role, name, exact).nth(nth)
        return await self._read(lambda: _collapsed(locator))

    async def checked(self, role: str, name: Name, *, nth: int = 0) -> bool:
        locator = self._role(role, name).nth(nth)
        return await self._read(lambda: _aria_checked(locator))

    async def wait_for(self, selector: str, *, timeout_s: float = 30.0) -> None:
        await self._first_visible(selector, timeout_s)

    def current_url(self) -> str:
        return str(getattr(self._page, "url", "") or "")

    async def batch_rpc(self, rpcid: str, args_json: str) -> tuple[int, str]:
        """A token-less batchexecute call made by the page itself, with the page's own session:
        reads (as29s carries the links the project list leaves out) and free writes (renames,
        photo characters, stitching). Calls that need reCAPTCHA stay page-driven."""
        return await self._read(lambda: self._page.evaluate(_BATCH_RPC_JS, [rpcid, args_json]))

    async def dispatch_click(self, role: str, name: Name, *, exact: bool = False) -> None:
        """A click event on the control itself: some Flow controls ignore keyboard activation."""
        locator = self._role(role, name, exact).first
        await self._act(role, name, lambda: locator.dispatch_event("click",
                                                                   timeout=CONTROL_TIMEOUT_MS))

    async def set_files(self, selector: str, paths: list[str]) -> None:
        """Give a page's file input these files (AI Studio's upload input; no chooser needed)."""
        async def run() -> None:
            await self._page.locator(selector).first.set_input_files(
                paths, timeout=CONTROL_TIMEOUT_MS)

        await self._act("file input", selector, run)

    async def choose_file(self, role: str, name: Name, path: Any) -> None:
        """Answer the file chooser that clicking this control opens (Flow's Upload media)."""
        locator = self._role(role, name).first

        async def run() -> None:
            async with self._page.expect_file_chooser(timeout=CONTROL_TIMEOUT_MS) as chooser:
                await locator.dispatch_event("click")
            await (await chooser.value).set_files(str(path))

        await self._act(role, name, run)

    async def _tile_point(self, text: str, dx: float, dy: float) -> tuple[float, float]:
        caption = self._page.get_by_text(text, exact=True).first
        # A tile below the window has a box too, but the mouse cannot reach it (live).
        await caption.scroll_into_view_if_needed(timeout=CONTROL_TIMEOUT_MS)
        box = await caption.bounding_box(timeout=CONTROL_TIMEOUT_MS)
        if box is None:
            raise RuntimeError(f"{text!r} is not on screen")
        return box["x"] + dx, box["y"] + dy

    async def hover_text(self, text: str) -> None:
        """Hover the media tile captioned ``text`` (its image sits above the caption)."""
        async def run() -> None:
            await self._page.mouse.move(*await self._tile_point(text, 20, -100))

        await self._act("text", text, run)

    async def click_text(self, text: str) -> None:
        """A real mouse click on the media tile captioned ``text`` (opens Flow's editor)."""
        async def run() -> None:
            await self._page.mouse.click(*await self._tile_point(text, 60, -120))

        await self._act("text", text, run)

    async def dispatch_click_near(self, role: str, name: Name, text: str) -> None:
        """Click the control inside the tile captioned exactly ``text`` (the tile's own ⋮)."""
        caption = self._page.get_by_text(text, exact=True).first
        tile = caption.locator("xpath=ancestor::*[.//button[@aria-label]][1]")

        async def run() -> None:
            await tile.get_by_role(role, name=name, exact=True).first.dispatch_event(
                "click", timeout=CONTROL_TIMEOUT_MS)

        await self._act(role, name, run)

    @contextlib.asynccontextmanager
    async def responses(self, marker: str = RPC_MARKER) -> AsyncIterator[ResponseLog]:
        log = _QueueLog()
        pending: set[asyncio.Task[None]] = set()

        async def store(response: Any) -> None:
            try:
                text = await response.text()  # a streamed response resolves when it ends
            except Exception:
                text = ""
            for method in _methods(response.url):
                log.put(CapturedResponse(method, response.status, text,
                                         response.request.post_data or ""))

        def on_response(response: Any) -> None:
            if marker in response.url:
                task = asyncio.ensure_future(store(response))
                pending.add(task)
                task.add_done_callback(pending.discard)

        def on_close(_page: Any) -> None:
            log.close()

        self._page.on("response", on_response)
        self._page.on("close", on_close)
        try:
            yield log
        finally:
            self._page.remove_listener("response", on_response)
            self._page.remove_listener("close", on_close)
            for task in pending:
                task.cancel()


async def _minimize(context: Any, page: Any) -> None:
    try:
        cdp = await context.new_cdp_session(page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds",
                       {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
    except Exception:
        pass  # cosmetic: a visible window works the same


async def _maximize(context: Any, page: Any) -> None:
    try:
        cdp = await context.new_cdp_session(page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds",
                       {"windowId": window["windowId"], "bounds": {"windowState": "maximized"}})
    except Exception:
        pass  # cosmetic


async def _save_session(profile: Profile, context: Any) -> None:
    """Google rotates the session cookies while Chrome runs; copy them to the storage state
    that HTTP commands use, so those do not fall back to stale ones. Only a signed-in state
    replaces a saved one, and a failed save never fails the run."""
    try:
        if not profile.storage_state.exists():
            return
        account = account_info(load_state(profile))
        state = await context.storage_state()
        if missing_cookies(filter_state(state)):
            return
        save_state(profile, state, account)
    except Exception:
        pass


@contextlib.asynccontextmanager
async def open_browser(
    profile: Profile,
    *,
    window: str = "minimized",
    playwright_factory: Callable[[], Any] | None = None,
) -> AsyncIterator[Ui]:
    """The profile's Chrome; ``window`` is minimized or headless (desktop viewport) or visible
    (maximized, page sized by the window)."""
    if not profile.chrome_dir.exists():
        raise BrowserError(
            f"profile {profile.name!r} has no browser session", hint='run "aistudio login" first'
        )
    factory = playwright_factory or default_playwright_factory()
    async with factory() as playwright:
        # A visible window is for watching: the page follows the real window like normal Chrome
        # (a fixed 1600x1000 page cut off pop-ups on smaller screens). Hidden runs keep the
        # desktop viewport that page driving relies on.
        context = await launch_persistent(playwright, profile, browser="chrome",
                                          headless=window == "headless",
                                          viewport=None if window == "visible" else VIEWPORT)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if window == "minimized":
                await _minimize(context, page)
            elif window == "visible":
                await _maximize(context, page)
            yield PlaywrightUi(page)
        finally:
            await _save_session(profile, context)
            try:
                await context.close()
            except Exception:  # the user may have closed the window already
                pass
