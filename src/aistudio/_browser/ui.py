"""A narrow UI-automation interface over a Playwright page (spec §5.4 page driving).

Page drivers (BuildPage, ...) talk to ``Ui``; ``PlaywrightUi`` is the thin real implementation
and tests use a scripted fake. Generation requests carry a content-bound BotGuard token, so they
must be sent by AI Studio's own page; a minimized window is accepted, headless is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio.exceptions import BrowserError
from aistudio.paths import Profile

VIEWPORT = {"width": 1600, "height": 1000}  # narrower hides parts of AI Studio's UI
RUN_SHORTCUT = "Meta+Enter" if sys.platform == "darwin" else "Control+Enter"
RPC_MARKER = "MakerSuiteService/"


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

    def responses(self) -> contextlib.AbstractAsyncContextManager[ResponseLog]: ...


class _QueueLog:
    def __init__(self) -> None:
        self._queues: defaultdict[str, asyncio.Queue[CapturedResponse]] = defaultdict(
            asyncio.Queue
        )

    def put(self, response: CapturedResponse) -> None:
        self._queues[response.method].put_nowait(response)

    async def next(self, method: str, *, timeout_s: float) -> CapturedResponse | None:
        try:
            return await asyncio.wait_for(self._queues[method].get(), max(timeout_s, 0))
        except asyncio.TimeoutError:
            return None


class PlaywrightUi:
    def __init__(self, page: Any) -> None:
        self._page = page

    async def goto(self, url: str) -> None:
        await self._page.goto(url)

    async def _first_visible(self, selector: str, timeout_s: float) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            for element in await self._page.locator(selector).all():
                if await element.is_visible():
                    return element
            if loop.time() >= deadline:
                raise BrowserError(
                    f"AI Studio page changed or did not load: no visible {selector!r}",
                    hint="re-run; if it persists, the AI Studio UI changed and aistudio "
                         "needs an update",
                )
            await asyncio.sleep(0.25)

    async def fill_first(self, selector: str, text: str, *, timeout_s: float = 30.0) -> None:
        await (await self._first_visible(selector, timeout_s)).fill(text)

    async def press_first(self, selector: str, key: str) -> None:
        await (await self._first_visible(selector, 5.0)).press(key)

    @contextlib.asynccontextmanager
    async def responses(self) -> AsyncIterator[ResponseLog]:
        log = _QueueLog()
        pending: set[asyncio.Task[None]] = set()

        async def store(response: Any) -> None:
            try:
                text = await response.text()  # a streamed response resolves when it ends
            except Exception:
                text = ""
            method = response.url.split("?", 1)[0].rsplit("/", 1)[-1]
            log.put(CapturedResponse(method, response.status, text,
                                     response.request.post_data or ""))

        def on_response(response: Any) -> None:
            if RPC_MARKER in response.url:
                task = asyncio.ensure_future(store(response))
                pending.add(task)
                task.add_done_callback(pending.discard)

        self._page.on("response", on_response)
        try:
            yield log
        finally:
            self._page.remove_listener("response", on_response)
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


@contextlib.asynccontextmanager
async def open_browser(
    profile: Profile,
    *,
    window: str = "minimized",
    playwright_factory: Callable[[], Any] | None = None,
) -> AsyncIterator[Ui]:
    """The profile's Chrome at a desktop viewport; ``window`` is minimized, visible or headless."""
    if not profile.chrome_dir.exists():
        raise BrowserError(
            f"profile {profile.name!r} has no browser session", hint='run "aistudio login" first'
        )
    factory = playwright_factory or default_playwright_factory()
    async with factory() as playwright:
        context = await launch_persistent(playwright, profile, browser="chrome",
                                          headless=window == "headless", viewport=VIEWPORT)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if window == "minimized":
                await _minimize(context, page)
            yield PlaywrightUi(page)
        finally:
            try:
                await context.close()
            except Exception:  # the user may have closed the window already
                pass
