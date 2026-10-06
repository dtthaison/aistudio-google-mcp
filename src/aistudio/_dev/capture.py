"""``aistudio dev capture``: record MakerSuite traffic from a signed-in session (spec §8.1)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aistudio._auth.login import APP_URL, default_playwright_factory, launch_persistent
from aistudio._dev.fixtures import FixtureWriter, rpc_method_name
from aistudio._dev.redact import RawExchange, Redactor
from aistudio.exceptions import BrowserError
from aistudio.paths import Profile

log = logging.getLogger(__name__)

DEFAULT_HOST_PATTERN = r"(^|\.)clients6\.google\.com$|(^|\.)googleapis\.com$"

CHECKLIST = (
    "Use throwaway content: prompt text is kept in the fixtures.",
    "1. Open the model picker and click every filter chip. Open one model card, "
    "then star and unstar a model.",
    "2. Open the library, then open a prompt.",
    "3. Chat with a Gemini model four times: plain, with Search, with code execution, "
    "with thinking. Then chat once with a Gemma model.",
    "4. Save, rename, and delete a prompt.",
    "5. Count tokens.",
    "6. Generate an image, then edit it. Generate speech, then transcribe a short audio file.",
    "7. Generate a video, then a song with a music model.",
    "Close the browser window (or press Ctrl-C) when done.",
)


class CaptureRecorder:
    """Turns finished Playwright requests into redacted fixture files."""

    def __init__(
        self,
        writer: FixtureWriter,
        *,
        host_pattern: str = DEFAULT_HOST_PATTERN,
        on_write: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._writer = writer
        self._hosts = re.compile(host_pattern)
        self._redactor = Redactor()
        self._on_write = on_write
        self.counts: Counter[str] = Counter()

    def wants(self, url: str, http_method: str) -> bool:
        return http_method != "OPTIONS" and bool(self._hosts.search(urlparse(url).hostname or ""))

    async def handle(self, request: Any) -> Path | None:
        if not self.wants(request.url, request.method):
            return None
        try:
            exchange = await self._exchange(request)
        except Exception as exc:  # browser closed mid-request, target gone, ...
            log.warning("could not record %s: %s", rpc_method_name(request.url), exc)
            return None
        if exchange is None:
            return None
        record = self._redactor.exchange(exchange)
        path = self._writer.write(record)
        self.counts[record["method"]] += 1
        if self._on_write:
            self._on_write(path, record["method"])
        return path

    async def _exchange(self, request: Any) -> RawExchange | None:
        response = await request.response()
        if response is None:
            return None
        try:
            body = await response.body()
        except Exception:  # redirects and evicted streams have no body
            body = b""
        return RawExchange(
            url=request.url,
            http_method=request.method,
            request_headers=await request.all_headers(),
            request_body=request.post_data_buffer or b"",
            status=response.status,
            response_headers=await response.all_headers(),
            response_body=body,
        )


async def run_capture(
    profile: Profile,
    recorder: CaptureRecorder,
    *,
    browser: str = "chrome",
    playwright_factory: Callable[[], Any] | None = None,
) -> None:
    """Open the profile's Chrome headed and record until the window is closed."""
    if not profile.chrome_dir.exists():
        raise BrowserError(
            f"profile {profile.name!r} has no browser session", hint='run "aistudio login" first'
        )
    factory = playwright_factory or default_playwright_factory()
    async with factory() as playwright:
        context = await launch_persistent(playwright, profile, browser=browser, headless=False)
        closed = asyncio.Event()
        pending: set[asyncio.Task[Path | None]] = set()

        def on_finished(request: Any) -> None:
            task = asyncio.ensure_future(recorder.handle(request))
            pending.add(task)
            task.add_done_callback(pending.discard)

        context.on("close", lambda _context: closed.set())
        context.on("requestfinished", on_finished)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(APP_URL, wait_until="commit")
        try:
            await closed.wait()
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not closed.is_set():
                await context.close()
