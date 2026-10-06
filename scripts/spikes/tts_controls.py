"""Throwaway spike: map the AI Studio speech page's controls (voice picker, style box).

Usage: uv run python scripts/spikes/tts_controls.py [profile]
Opens the speech page, clicks "Create new dialog", lists visible controls, then opens anything
that looks like a voice or model picker and lists its options. Generates nothing.
"""

import asyncio
import re
import sys

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio.paths import get_profile, resolve_profile_name

SPEECH_URL = "https://aistudio.google.com/generate-speech"
ROLES = ("textbox", "combobox", "listbox", "button", "radio", "slider", "switch", "tab")
SKIP = re.compile(r"Google Account|Skip to main|navigation|What's new|Settings|Get API key")


async def visible_names(page, role):
    names = []
    for el in await page.get_by_role(role).all():
        try:
            if not await el.is_visible():
                continue
            label = (await el.get_attribute("aria-label")) or (await el.inner_text()) or ""
            label = " ".join(label.split())[:60]
            if label and not SKIP.search(label):
                names.append(label)
        except Exception:
            pass
    return names


async def dump(page, title):
    print(f"--- {title}", flush=True)
    for role in ROLES:
        names = await visible_names(page, role)
        if names:
            print(f"{role}: {names[:30]}", flush=True)


async def main(profile_name):
    profile = get_profile(resolve_profile_name(profile_name))
    async with default_playwright_factory()() as playwright:
        context = await launch_persistent(playwright, profile, browser="chrome", headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(SPEECH_URL)
            await page.wait_for_timeout(8000)
            create = page.get_by_role("button", name=re.compile(r"Create new dialog", re.I))
            if await create.count():
                await create.first.click()
                await page.wait_for_timeout(4000)
            await dump(page, "after Create new dialog")
            for pattern in (r"voice", r"speaker", r"model", r"style"):
                for role in ("combobox", "button"):
                    picker = page.get_by_role(role, name=re.compile(pattern, re.I))
                    if not await picker.count() or not await picker.first.is_visible():
                        continue
                    label = " ".join(((await picker.first.inner_text()) or "").split())[:60]
                    print(f"=== opening {role} matching /{pattern}/: {label!r}", flush=True)
                    await picker.first.click()
                    await page.wait_for_timeout(2500)
                    options = await visible_names(page, "option")
                    print(f"options ({len(options)}): {options[:60]}", flush=True)
                    if not options:
                        await dump(page, f"after opening /{pattern}/")
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(1000)
                    break
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
