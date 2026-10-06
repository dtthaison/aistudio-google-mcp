"""Throwaway spike for spec H4: is the BotGuard token bound to the request content?

Usage: uv run python scripts/spikes/route_rewrite.py [profile]
Then type exactly SPIKE_ORIGINAL into a new chat in the browser window and press Run.

Intercepts any MakerSuiteService request whose body contains the marker (the M1 capture
did not record a text chat, so the method name is learned here), swaps the prompt text,
and keeps the token the page minted for the original text.
"""

import asyncio
import sys

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio.paths import get_profile, resolve_profile_name

MARKER = "SPIKE_ORIGINAL"
REPLACEMENT = "Reply with exactly one word: REWRITTEN"


async def main(profile_name: str | None) -> None:
    profile = get_profile(resolve_profile_name(profile_name))
    async with default_playwright_factory()() as playwright:
        context = await launch_persistent(playwright, profile, browser="chrome", headless=False)
        done = asyncio.Event()
        rewritten_urls: set[str] = set()

        async def rewrite(route):
            body = route.request.post_data or ""
            if MARKER not in body:
                await route.continue_()
                return
            method = route.request.url.rsplit("/", 1)[-1]
            print(f"intercepted {method}; rewriting the prompt text", flush=True)
            rewritten_urls.add(route.request.url)
            await route.continue_(post_data=body.replace(MARKER, REPLACEMENT))

        async def on_response(response):
            if response.url in rewritten_urls and not done.is_set():
                text = await response.text()
                method = response.url.rsplit("/", 1)[-1]
                print(f"RESULT method={method} status={response.status} "
                      f"contains_REWRITTEN={'REWRITTEN' in text}", flush=True)
                print(text[:1500], flush=True)
                done.set()

        await context.route("**/*MakerSuiteService/*", rewrite)
        context.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("close", lambda _p: done.set())  # macOS keeps Chrome alive after the window
        await page.goto("https://aistudio.google.com/prompts/new_chat")
        print(f"Type {MARKER} into the chat and press Run.", flush=True)
        try:
            await asyncio.wait_for(done.wait(), timeout=900)
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
