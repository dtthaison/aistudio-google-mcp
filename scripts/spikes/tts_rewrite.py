"""Throwaway spike (spec H4, TTS): is the BotGuard token bound to the request content?

Usage: uv run python scripts/spikes/tts_rewrite.py [profile]

Opens the AI Studio speech page in the profile's Chrome, tries to type a marker and press Run
itself (falls back to asking the user), swaps the marker for different text before the request
leaves the browser, and reports whether Google accepted it.
Audio goes to /tmp/aistudio_tts_spike.wav.
"""

import asyncio
import base64
import os
import re
import sys
import wave

from aistudio._auth.login import CHROME_ARGS, IGNORE_DEFAULT_ARGS, default_playwright_factory
from aistudio._rpc import wire
from aistudio.paths import get_profile, resolve_profile_name

MARKER = "SPIKE_ORIGINAL"
SWAPPED = "This sentence was swapped by the command line tool."
SPEECH_URL = "https://aistudio.google.com/generate-speech"
OUT = "/tmp/aistudio_tts_spike.wav"


def pcm_from(response_text: str) -> bytes:
    pcm = b""

    def walk(x):
        nonlocal pcm
        if isinstance(x, list):
            if (len(x) == 2 and isinstance(x[0], str) and x[0].startswith("audio/")
                    and isinstance(x[1], str)):
                pcm += base64.b64decode(x[1])
            else:
                for v in x:
                    walk(v)

    walk(wire.decode(response_text))
    return pcm


async def describe_controls(page) -> None:
    for role in ("textbox", "button"):
        names = []
        for el in await page.get_by_role(role).all():
            try:
                if await el.is_visible():
                    label = (await el.get_attribute("aria-label")) or (await el.inner_text())
                    names.append((label or "").strip().replace("\n", " ")[:40])
            except Exception:
                pass
        print(f"visible {role}s: {names[:25]}", flush=True)


async def try_to_run(page) -> bool:
    boxes = [b for b in await page.locator("textarea").all() if await b.is_visible()]
    if not boxes:
        create = page.get_by_role("button", name=re.compile(r"Create new dialog", re.I))
        if await create.count():
            print("clicking 'Create new dialog'", flush=True)
            await create.first.click()
            await page.wait_for_timeout(4000)
            await describe_controls(page)
            boxes = [b for b in await page.locator("textarea").all() if await b.is_visible()]
    if not boxes:
        return False
    await boxes[-1].fill(MARKER)
    run = page.get_by_role("button", name=re.compile(r"^\s*Run", re.I))
    if await run.count():
        await run.first.click()
        return True
    await boxes[-1].press("Meta+Enter")
    return True


async def main(profile_name: str | None) -> None:
    profile = get_profile(resolve_profile_name(profile_name))
    async with default_playwright_factory()() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), channel="chrome", headless=False, args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS, no_viewport=True,
            service_workers="block",  # routes do not see requests a service worker handles
        )
        done = asyncio.Event()
        swapped_urls: set[str] = set()

        async def rewrite(route):
            body = route.request.post_data or ""
            print(f"ROUTE saw {route.request.url.rsplit('/', 1)[-1]}", flush=True)
            if os.environ.get("SPIKE_MODE") == "control":
                print("CONTROL: re-sending the original bytes unchanged", flush=True)
                swapped_urls.add(route.request.url)
                await route.continue_(post_data=body)
                return
            try:
                req = wire.decode(body)
                original = req[1][0][0][0][1]
                req[1][0][0][0][1] = SWAPPED
                new_body = wire.encode(req)
            except (ValueError, IndexError, TypeError) as exc:
                print(f"could not restructure the body ({exc}); passing it through", flush=True)
                await route.continue_()
                return
            print(f"swapping text {original[:30]!r} -> {SWAPPED!r}", flush=True)
            swapped_urls.add(route.request.url)
            await route.continue_(post_data=new_body)

        def on_request(request):
            if "MakerSuiteService/" in request.url:
                body = request.post_data or ""
                print(f"REQUEST {request.url.rsplit('/', 1)[-1]} "
                      f"from_service_worker={request.service_worker is not None} "
                      f"marker={MARKER in body}", flush=True)

        async def on_response(response):
            if response.url not in swapped_urls or done.is_set():
                return
            text = await response.text()
            if response.status != 200:
                print(f"RESULT rejected status={response.status} body={text[:300]!r}", flush=True)
            else:
                pcm = pcm_from(text)
                with wave.open(OUT, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(24000)
                    w.writeframes(pcm)
                print(f"RESULT accepted status=200 audio_seconds={len(pcm) / 48000:.2f} "
                      f"wav={OUT}", flush=True)
            done.set()

        await context.route("**/*MakerSuiteService/GenerateContent*", rewrite)
        context.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        context.on("request", on_request)
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("close", lambda _p: done.set())
        try:
            await page.goto(SPEECH_URL)
            await page.wait_for_timeout(8000)
            print(f"page: {page.url}", flush=True)
            await describe_controls(page)
            automated = await try_to_run(page)
        except Exception as exc:
            automated = False
            print(f"automation failed: {type(exc).__name__}: {exc}", flush=True)
            if done.is_set():
                print("RESULT window closed before any generation", flush=True)
                return
        print(f"automation {'pressed Run' if automated else 'found no text box'}", flush=True)
        try:
            await asyncio.wait_for(done.wait(), timeout=40 if automated else 1)
        except TimeoutError:
            print("ACTION NEEDED: in the browser, create a speech prompt, type any short text "
                  "and press Run", flush=True)
            try:
                await asyncio.wait_for(done.wait(), timeout=300)
            except TimeoutError:
                print("RESULT timeout: no request carrying the marker was seen", flush=True)
        try:
            await context.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
