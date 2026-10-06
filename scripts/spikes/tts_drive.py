"""Throwaway spike: drive the speech page end to end (model, voice, style, text, Run).

Usage: uv run python scripts/spikes/tts_drive.py --headed [--text T] [--voice V] [--style S]
       [--model M] [-o FILE.wav]
Verifies each UI choice reaches the outgoing GenerateContent request and writes the audio to
/tmp/aistudio_tts_drive.wav.
"""

import argparse
import asyncio
import base64
import re
import wave

from aistudio._auth.login import CHROME_ARGS, IGNORE_DEFAULT_ARGS, default_playwright_factory
from aistudio._rpc import wire
from aistudio.paths import get_profile, resolve_profile_name

SPEECH_URL = "https://aistudio.google.com/generate-speech"
MODEL = "gemini-3.8-flash-lite-tts"
VOICE = "Tova"
STYLE = "warm, slow and smiling"
TEXT = "Hello from the command line. <laugh> This is a test."
OUT = "/tmp/aistudio_tts_drive.wav"


async def label(locator) -> str:
    return " ".join(((await locator.inner_text()) or "").split())


async def main(headed: bool, MODEL: str, VOICE: str, STYLE: str, TEXT: str, OUT: str) -> None:
    profile = get_profile(resolve_profile_name(None))
    async with default_playwright_factory()() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), channel="chrome", headless=not headed, args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS, viewport={"width": 1600, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(SPEECH_URL)
            await page.get_by_role("button", name=re.compile("Create new dialog")).first.click(
                timeout=30000)
            text_box = page.get_by_role("textbox", name="Speech block text")
            await text_box.wait_for(timeout=15000)
            print("step: dialog created", flush=True)

            model_button = page.get_by_role("button", name=re.compile(r"gemini-[\w.-]*tts"))
            print(f"step: current model button {await label(model_button.first)!r}", flush=True)
            await model_button.first.click()
            await page.get_by_role("textbox", name="Search").fill(MODEL)
            await page.wait_for_timeout(1500)
            await page.get_by_role("button", name=re.compile(re.escape(MODEL))).first.click()
            await page.wait_for_timeout(1500)
            print(f"step: model now {await label(model_button.first)!r}", flush=True)

            speaker = page.get_by_role("button", name=re.compile(r"^Speaker 1 - "))
            await speaker.first.click()
            await page.get_by_role("textbox", name="Search voices").fill(VOICE)
            await page.wait_for_timeout(1500)
            await page.get_by_role("button", name=VOICE, exact=True).first.click()
            await page.wait_for_timeout(1500)
            if await page.get_by_role("textbox", name="Search voices").count():
                print("step: voice panel still open after click; pressing Escape", flush=True)
                await page.keyboard.press("Escape")
            print(f"step: speaker now {await label(speaker.first)!r}", flush=True)

            await page.get_by_role("button", name="Style", exact=True).first.click()
            await page.get_by_role("textbox", name="Describe the voice style").fill(STYLE)
            await text_box.fill(TEXT)
            print("step: style and text filled", flush=True)

            async with page.expect_response(
                lambda r: "MakerSuiteService/GenerateContent" in r.url, timeout=120000
            ) as info:
                await text_box.press("Meta+Enter")  # Run's shortcut; avoids overlays
            response = await info.value
            sent = wire.decode(response.request.post_data or "")
            print(f"request: model={sent[0]} voice={wire.opt_index(sent, 3, 15, 0, 0, 0)} "
                  f"style={wire.opt_index(sent, 1, 0, 0, 0, 40, 1)!r} "
                  f"text={wire.opt_index(sent, 1, 0, 0, 0, 1)!r}", flush=True)
            body = await response.text()
            if response.status != 200:
                print(f"RESULT status={response.status} body={body[:200]!r}", flush=True)
                return
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

            walk(wire.decode(body))
            with wave.open(OUT, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            print(f"RESULT status=200 headless={not headed} seconds={len(pcm) / 48000:.2f} "
                  f"wav={OUT}", flush=True)
        except Exception as exc:
            print(f"RESULT failed at {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        finally:
            await context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--text", default=TEXT)
    parser.add_argument("--voice", default=VOICE)
    parser.add_argument("--style", default=STYLE)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("-o", "--out", default=OUT)
    a = parser.parse_args()
    asyncio.run(main(a.headed, a.model, a.voice, a.style, a.text, a.out))
