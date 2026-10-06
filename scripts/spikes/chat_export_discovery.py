"""Throwaway discovery spike: (1) how a Build app's file contents are fetched (export),
(2) how a plain/Search/code-execution/thinking chat is sent (ask), in a minimized window.

Usage: uv run python scripts/spikes/chat_export_discovery.py <app_id>
Records MakerSuite/googleapis traffic (redacted) into tests/fixtures and logs controls.
"""

import asyncio
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from aistudio._auth.login import CHROME_ARGS, IGNORE_DEFAULT_ARGS, default_playwright_factory
from aistudio._browser.ui import RUN_SHORTCUT, VIEWPORT
from aistudio._dev.capture import CaptureRecorder
from aistudio._dev.fixtures import FixtureWriter
from aistudio._dev.redact import Redactor
from aistudio.paths import get_profile, resolve_profile_name

HOSTS = r"^alkalimakersuite-pa\.clients6\.google\.com$|(^|\.)googleapis\.com$"
NOISE = re.compile(r"gstatic|fonts\.|analytics|tagmanager|play\.google|ogs\.|accounts\.google|"
                   r"people-pa|cloudconsole|feedback-pa|waa-pa|youtube|doubleclick|run\.app")
SKIP = re.compile(r"Google Account|Skip to main|navigation menu|What's new|Get API key")
T0 = time.monotonic()


def log(msg):
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}", flush=True)


async def controls(page, title, roles=("textbox", "switch", "checkbox", "combobox", "button")):
    for role in roles:
        names = []
        for el in await page.get_by_role(role).all():
            try:
                if not await el.is_visible():
                    continue
                label = (await el.get_attribute("aria-label")) or (await el.inner_text()) or ""
                label = " ".join(label.split())[:45]
                if label and not SKIP.search(label):
                    extra = ""
                    if role in ("switch", "checkbox"):
                        extra = f"={await el.get_attribute('aria-checked')}"
                    names.append(label + extra)
            except Exception:
                pass
        if names:
            log(f"{title} {role}s: {names[:45]}")


async def minimize(context, page):
    cdp = await context.new_cdp_session(page)
    window = await cdp.send("Browser.getWindowForTarget")
    await cdp.send("Browser.setWindowBounds",
                   {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})


async def first_textarea(page, timeout_s=30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for el in await page.locator("textarea").all():
            if await el.is_visible():
                return el
        await asyncio.sleep(0.5)
    return None


async def wait_run_enabled(page, timeout_s=15):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for el in await page.get_by_role("button", name=re.compile(r"^Run")).all():
            if await el.is_visible() and await el.is_enabled():
                return True
        await asyncio.sleep(0.25)
    return False


async def send(page, text, what):
    box = await first_textarea(page)
    if box is None:
        log(f"{what}: no visible textarea")
        return
    await box.fill(text)
    log(f"{what}: Run enabled={await wait_run_enabled(page)}")
    try:
        async with page.expect_response(lambda r: "MakerSuiteService/GenerateContent" in r.url,
                                        timeout=120000) as info:
            await box.press(RUN_SHORTCUT)
        response = await info.value
        body = await response.text()
        log(f"{what}: GenerateContent status {response.status}, {len(body)} bytes")
    except Exception as exc:
        log(f"{what}: no GenerateContent ({type(exc).__name__})")
    await page.wait_for_timeout(3000)


async def toggle(page, pattern, what):
    for role in ("switch", "checkbox", "button"):
        target = page.get_by_role(role, name=re.compile(pattern, re.I))
        if await target.count() and await target.first.is_visible():
            await target.first.click()
            await page.wait_for_timeout(800)
            log(f"toggled {what} via {role} /{pattern}/")
            return True
    log(f"no control for {what} /{pattern}/")
    return False


async def main(app_id):
    profile = get_profile(resolve_profile_name(None))
    recorder = CaptureRecorder(FixtureWriter(Path("tests/fixtures")), host_pattern=HOSTS,
                               redactor=Redactor(extra=("Son Doan", "Doan, Son")))
    async with default_playwright_factory()() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), channel="chrome", headless=False, args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS, viewport=VIEWPORT,
        )
        pending = set()

        def on_finished(request):
            task = asyncio.ensure_future(recorder.handle(request))
            pending.add(task)
            task.add_done_callback(pending.discard)

        def on_request(request):
            if "MakerSuiteService/" in request.url:
                log(f"-> {request.url.rsplit('/', 1)[-1]}")
            elif request.resource_type in ("fetch", "xhr") and not NOISE.search(request.url):
                parsed = urlparse(request.url)
                log(f"-> other {parsed.hostname}{parsed.path[:60]}")

        context.on("requestfinished", on_finished)
        context.on("request", on_request)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await minimize(context, page)
            log("window minimized")
            # 1. export discovery
            await page.goto(f"https://aistudio.google.com/apps/{app_id}")
            await first_textarea(page)
            await page.wait_for_timeout(4000)
            for name in ("Open src/App.tsx", "Open index.html", "Open package.json"):
                button = page.get_by_role("button", name=name)
                if await button.count():
                    log(f"== clicking {name}")
                    await button.first.click()
                    await page.wait_for_timeout(4000)
            code = page.get_by_role("button", name=re.compile(r"^code$|Code view|<>", re.I))
            if await code.count():
                await code.first.click()
                log("== clicked a code-view button")
                await page.wait_for_timeout(4000)
            await controls(page, "app editor", roles=("button", "tab"))
            # 2. chat discovery
            await page.goto("https://aistudio.google.com/prompts/new_chat")
            await first_textarea(page)
            await page.wait_for_timeout(5000)
            await controls(page, "new chat")
            await send(page, "What is 2+2? Answer in one word.", "chat plain")
            if await toggle(page, r"Grounding with Google Search|Google Search", "Search"):
                await send(page, "Who won the most recent FIFA World Cup? One sentence.",
                           "chat search")
                await toggle(page, r"Grounding with Google Search|Google Search", "Search off")
            if await toggle(page, r"Code execution", "code execution"):
                await send(page, "Compute 17*23 using code.", "chat code")
                await toggle(page, r"Code execution", "code execution off")
            await controls(page, "chat settings", roles=("combobox", "slider", "button"))
            if await toggle(page, r"Thinking level|Thinking mode|thinking", "thinking control"):
                await controls(page, "after thinking control", roles=("option", "menuitem",
                                                                      "radio", "button"))
                low = page.get_by_role("option", name=re.compile(r"^Low", re.I))
                if await low.count():
                    await low.first.click()
                    log("picked thinking Low")
                else:
                    await page.keyboard.press("Escape")
                await send(page, "Say hi.", "chat thinking")
        except Exception as exc:
            log(f"stopped: {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            log(f"recorded: {dict(recorder.counts)}")
            try:
                await context.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
