"""Throwaway debug spike: why does `app prompt` not start a run on a freshly opened app page?

Usage: uv run python scripts/spikes/build_prompt_debug.py <app_id>
Lists every textarea (visible, placeholder, aria-label), fills the first visible one with a
marker WITHOUT pressing Run, takes screenshots to /tmp, and logs MakerSuite calls.
"""

import asyncio
import sys
import time

from aistudio._auth.login import CHROME_ARGS, IGNORE_DEFAULT_ARGS, default_playwright_factory
from aistudio.paths import get_profile, resolve_profile_name

T0 = time.monotonic()


def log(msg):
    print(f"[{time.monotonic() - T0:5.1f}s] {msg}", flush=True)


async def textareas(page, title):
    rows = []
    for i, el in enumerate(await page.locator("textarea").all()):
        rows.append((i, await el.is_visible(), await el.is_enabled(),
                     (await el.get_attribute("placeholder") or "")[:50],
                     (await el.get_attribute("aria-label") or "")[:40]))
    log(f"{title} textareas (index, visible, enabled, placeholder, aria-label): {rows}")


async def main(app_id):
    profile = get_profile(resolve_profile_name(None))
    async with default_playwright_factory()() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), channel="chrome", headless=False, args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS, viewport={"width": 1600, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("request", lambda r: "MakerSuiteService/" in r.url
                and log(f"-> {r.url.rsplit('/', 1)[-1]}"))
        try:
            await page.goto(f"https://aistudio.google.com/apps/{app_id}")
            await textareas(page, "immediately after goto")
            await page.wait_for_timeout(10000)
            await textareas(page, "after 10s")
            await page.screenshot(path="/tmp/aistudio_prompt_debug_1.png")
            buttons = []
            for el in await page.get_by_role("button").all():
                if await el.is_visible():
                    buttons.append(" ".join(((await el.get_attribute("aria-label"))
                                             or (await el.inner_text()) or "").split())[:30])
            log(f"visible buttons: {buttons[:45]}")
            first = None
            for el in await page.locator("textarea").all():
                if await el.is_visible():
                    first = el
                    break
            if first is not None:
                await first.fill("DEBUG_MARKER_NOT_SENT")
                log("filled the first visible textarea with a marker (Run NOT pressed)")
                await page.wait_for_timeout(1500)
                await page.screenshot(path="/tmp/aistudio_prompt_debug_2.png")
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
