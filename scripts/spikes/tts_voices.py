"""Throwaway spike: list every voice in the speech page's voice panel. Generates nothing.

Usage: uv run python scripts/spikes/tts_voices.py
"""

import asyncio
import re

from aistudio._auth.login import CHROME_ARGS, IGNORE_DEFAULT_ARGS, default_playwright_factory
from aistudio.paths import get_profile, resolve_profile_name

SPEECH_URL = "https://aistudio.google.com/generate-speech"
FAVORITE = re.compile(r"^(?:Favorite|Remove from favorites) (.+)$")


async def voice_names(page) -> list[str]:
    names = []
    for el in await page.get_by_role("button", name=FAVORITE).all():
        label = (await el.get_attribute("aria-label")) or (await el.inner_text()) or ""
        match = FAVORITE.match(" ".join(label.split()))
        if match and await el.is_visible():
            names.append(match.group(1))
    return names


async def main() -> None:
    profile = get_profile(resolve_profile_name(None))
    async with default_playwright_factory()() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), channel="chrome", headless=True, args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS, viewport={"width": 1600, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(SPEECH_URL)
            await page.get_by_role("button", name=re.compile("Create new dialog")).first.click(
                timeout=30000)
            await page.get_by_role("button", name=re.compile(r"^Speaker 1 - ")).first.click()
            await page.get_by_role("textbox", name="Search voices").wait_for(timeout=15000)
            print(f"initially visible: {await voice_names(page)}", flush=True)
            view_all = page.get_by_role("button", name=re.compile(r"^View all \d+"))
            print(f"'View all' buttons: {await view_all.count()}", flush=True)
            if await view_all.count():
                await view_all.first.click()
                await page.wait_for_timeout(1500)
                print(f"after first View all: {await voice_names(page)}", flush=True)
                back = page.get_by_role("button", name=re.compile(r"back|arrow_back", re.I))
                print(f"back buttons: {await back.count()}", flush=True)
            search = page.get_by_role("textbox", name="Search voices")
            await search.fill("a")
            await page.wait_for_timeout(1500)
            print(f"search 'a': {await voice_names(page)}", flush=True)
            await search.fill("")
            await page.wait_for_timeout(1000)
            filters = page.get_by_role("button", name=re.compile(r"^Language"))
            if await filters.count():
                await filters.first.click()
                await page.wait_for_timeout(1000)
                options = [" ".join((await o.inner_text()).split())
                           for o in await page.get_by_role("option").all()]
                print(f"Language options: {options[:40]}", flush=True)
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
