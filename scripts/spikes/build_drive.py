"""Throwaway spike: discover how Build creates and updates an app, in a minimized browser.

Usage: uv run python scripts/spikes/build_drive.py

1. Minimizes the Chrome window (CDP) to test whether BotGuard accepts a background browser.
2. On /apps, types a small idea and starts a build; logs every MakerSuite call for ~3 minutes.
3. Sends one update prompt in the app editor; logs again.
4. Opens the code view and logs which calls read files.
All MakerSuite / googleapis traffic is recorded, redacted, into tests/fixtures.
"""

import asyncio
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from aistudio._auth.login import CHROME_ARGS, IGNORE_DEFAULT_ARGS, default_playwright_factory
from aistudio._dev.capture import CaptureRecorder
from aistudio._dev.fixtures import FixtureWriter
from aistudio._dev.redact import Redactor
from aistudio.paths import get_profile, resolve_profile_name

APPS_URL = "https://aistudio.google.com/apps"
IDEA = "A simple counter web page with one big button that increments a number."
UPDATE = "Make the button blue."
HOSTS = r"^alkalimakersuite-pa\.clients6\.google\.com$|(^|\.)googleapis\.com$"
START_RPC = "MakerSuiteService/CodeAssistantOffline"
SKIP = re.compile(r"Google Account|Skip to main|navigation menu|What's new|Settings|Get API key")
NOISE = re.compile(r"gstatic|googleusercontent|fonts\.|analytics|tagmanager|play\.google|ogs\.|"
                   r"accounts\.google|people-pa|cloudconsole|feedback-pa|waa-pa|youtube|doubleclick")
T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}", flush=True)


async def controls(page, title: str) -> None:
    for role in ("textbox", "button", "tab"):
        names = []
        for el in await page.get_by_role(role).all():
            try:
                if not await el.is_visible():
                    continue
                label = (await el.get_attribute("aria-label")) or (await el.inner_text()) or ""
                label = " ".join(label.split())[:50]
                if label and not SKIP.search(label):
                    names.append(label)
            except Exception:
                pass
        log(f"{title} {role}s: {names[:40]}")


async def window_state(context, page, state: str) -> None:
    cdp = await context.new_cdp_session(page)
    info = await cdp.send("Browser.getWindowForTarget")
    await cdp.send("Browser.setWindowBounds",
                   {"windowId": info["windowId"], "bounds": {"windowState": state}})


async def prompt_box(page):
    boxes = [b for b in await page.locator("textarea").all() if await b.is_visible()]
    return boxes[0] if boxes else None


async def start(page, text: str, what: str):
    box = await prompt_box(page)
    if box is None:
        log(f"{what}: no visible text box")
        return None
    await box.fill(text)
    for trigger in ("shortcut", "button"):
        try:
            async with page.expect_response(lambda r: START_RPC in r.url, timeout=60000) as info:
                if trigger == "shortcut":
                    await box.press("Meta+Enter")
                else:
                    button = page.get_by_role("button", name=re.compile(r"^(Build|Run|Send)", re.I))
                    await button.first.click(timeout=10000)
            response = await info.value
            log(f"{what}: CodeAssistantOffline via {trigger} -> status {response.status}")
            return response.status
        except Exception as exc:
            log(f"{what}: no CodeAssistantOffline via {trigger} ({type(exc).__name__})")
    return None


async def watch(seconds: int, what: str) -> None:
    log(f"{what}: watching traffic for {seconds}s")
    await asyncio.sleep(seconds)


async def main() -> None:
    profile = get_profile(resolve_profile_name(None))
    recorder = CaptureRecorder(FixtureWriter(Path("tests/fixtures")), host_pattern=HOSTS,
                               redactor=Redactor(extra=("Son Doan", "Doan, Son")))
    async with default_playwright_factory()() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), channel="chrome", headless=False, args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS, viewport={"width": 1600, "height": 1000},
        )
        pending: set = set()

        def on_finished(request):
            task = asyncio.ensure_future(recorder.handle(request))
            pending.add(task)
            task.add_done_callback(pending.discard)

        def on_request(request):
            if "MakerSuiteService/" in request.url:
                log(f"-> {request.url.rsplit('/', 1)[-1]}")
            elif not NOISE.search(request.url) and request.resource_type in ("fetch", "xhr"):
                parsed = urlparse(request.url)
                first = parsed.path.strip("/").split("/", 1)[0]
                log(f"-> other {parsed.hostname}/{first}")

        def on_response(response):
            if "MakerSuiteService/" in response.url and response.status != 200:
                log(f"<- {response.url.rsplit('/', 1)[-1]} status {response.status}")

        context.on("requestfinished", on_finished)
        context.on("request", on_request)
        context.on("response", on_response)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            try:
                await window_state(context, page, "minimized")
                log("window minimized")
            except Exception as exc:
                log(f"minimize failed: {type(exc).__name__}: {exc}")
            await page.goto(APPS_URL)
            await page.wait_for_timeout(8000)
            await controls(page, "apps page")
            status = await start(page, IDEA, "create (minimized)")
            if status != 200:
                log("restoring the window and retrying create once")
                await window_state(context, page, "normal")
                await page.goto(APPS_URL)
                await page.wait_for_timeout(6000)
                status = await start(page, IDEA, "create (visible)")
            if status == 200:
                await watch(180, "create")
                log(f"url now: {urlparse(page.url).path}")
                await controls(page, "app editor")
                status = await start(page, UPDATE, "update")
                if status == 200:
                    await watch(150, "update")
                code = page.get_by_role("tab", name=re.compile(r"code", re.I))
                if not await code.count():
                    code = page.get_by_role("button", name=re.compile(r"^code", re.I))
                if await code.count():
                    await code.first.click(timeout=10000)
                    log("opened the code view")
                    await page.wait_for_timeout(15000)
                    await controls(page, "code view")
                else:
                    log("no code tab/button found")
        except Exception as exc:
            log(f"stopped: {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            log(f"recorded fixtures: {dict(recorder.counts)}")
            try:
                await context.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
