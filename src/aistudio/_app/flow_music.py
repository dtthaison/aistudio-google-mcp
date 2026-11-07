"""Google Flow Music (flowmusic.app) songs: the Compose request, sent by the signed-in page.

Flow Music has no quote step: Generate spends at once. So, like the paid Flow commands, a run
spends only with ``--max-credits`` covering the measured price; without it, it reports the price.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.flow import _check_output
from aistudio._browser.ui import open_browser
from aistudio._flow.download import download_music
from aistudio._flow.music import (
    SESSION_URL,
    SITE,
    SONG_PRICE,
    Clip,
    clip_of,
    credits_left,
    find_model,
    job_id,
    song_of,
    song_request,
)
from aistudio._media.save import free_name, write_file
from aistudio.exceptions import (
    AIStudioError,
    AuthError,
    OverBudgetError,
    ServerError,
    ValidationError,
)
from aistudio.paths import Profile

SIGN_IN_WAIT_MS = 8000  # "Continue with Google" returns signed in after a few seconds (live)
LOAD_WAIT_MS = 3000  # the page refreshes an expired access token on load
GOOGLE_SAVE = "/__api/auth/google/save"  # the page's hand-over of the Google sign-in
GOOGLE_SAVE_TIMEOUT_S = 20.0
CLIP_POLLS = 20
CLIP_POLL_MS = 3000
LENGTH_SLACK_S = 10


async def flow_music(
    profile: Profile,
    prompt: str,
    *,
    lyrics: str | None = None,
    length_s: int | None = None,
    bpm: int | None = None,
    seed: int | None = None,
    title: str | None = None,
    model: str | None = None,
    wav: bool = False,
    max_credits: int | None = None,
    out: Path | None = None,
    overwrite: bool = False,
    timeout_s: float = 600.0,
    visible: bool = False,
    browser: Callable[..., Any] = open_browser,
    downloader: Callable[[str], Any] = download_music,
    now: Callable[[], datetime] = datetime.now,
    on_start: Callable[[], None] | None = None,
) -> dict[str, Any]:
    chosen = find_model(model)
    body = song_request(prompt, lyrics=lyrics, length_s=length_s, bpm=bpm, seed=seed,
                        title=title, model=chosen)
    if max_credits is not None and max_credits < 0:
        raise ValidationError("--max-credits must be 0 or more")
    _check_output(out, 1, overwrite)
    if max_credits is None or max_credits < SONG_PRICE:
        return {"status": "quoted", "credits": SONG_PRICE, "count": 1, "declined": False,
                "plan": f"1 song with {chosen} (Flow Music's measured price; it shows none "
                        "before generating)",
                "model": chosen}
    if on_start:
        on_start()
    warnings: list[str] = []
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        before = await _signed_in_credits(ui)
        if before is not None and before < SONG_PRICE:
            raise OverBudgetError(f"Flow Music has {before} credits left; a song costs "
                                  f"{SONG_PRICE}", hint="credits refill daily and monthly")
        status, text = await ui.music_api("POST", "/producer/tool-call", body)
        if status != 200:
            raise _api_error("starting the song", status, text)
        job = job_id(text)
        # spent from here on: every failure names where the song can be found
        try:
            status, text = await ui.music_api("GET", f"/messages/{job}/stream?last_id=0",
                                              timeout_s=timeout_s)
            song = song_of(text) if status == 200 else None
            if song is None:
                if status not in (200, 408):
                    raise _api_error("following the song", status, text)
                return {"status": "timeout", "job_id": job, "credits_spent": None,
                        "project_url": SESSION_URL, "files": [], "warnings": warnings}
            clip = await _ready_clip(ui, song.clip_id)
            status, text = await ui.music_api("GET", "/billing/credits")
            left = credits_left(text) if status == 200 else None
        except AIStudioError as exc:
            exc.hint = _with_place(exc.hint, f"job {job}")
            raise
    session_url = f"{SESSION_URL}/{song.conversation_id}" if song.conversation_id else SESSION_URL
    url = clip.wav_url if wav else clip.audio_url
    if not url:
        raise ServerError(f"Flow Music made song {clip.clip_id} but gave no "
                          f"{'wav' if wav else 'audio'} link",
                          hint=f"download it in Flow Music: {session_url}")
    try:
        data = await downloader(url)
    except AIStudioError as exc:
        exc.hint = _with_place(exc.hint, session_url)
        raise
    mime, ext = _audio_type(data)
    if out is not None:
        path = out
        if out.suffix.lower() != ext:
            warnings.append(f"{out} holds {mime} data; its extension says otherwise")
    else:
        path = free_name(Path(".") / f"flow-music-{now():%Y%m%d-%H%M%S}{ext}")
    saved = write_file(path, data, overwrite=overwrite and path == out)
    if length_s is not None and clip.seconds is not None \
            and abs(clip.seconds - length_s) > LENGTH_SLACK_S:
        warnings.append(f"asked for {length_s} s; Flow Music made {clip.seconds:.0f} s")
    spent = before - left if before is not None and left is not None else None
    if spent is not None and spent != SONG_PRICE:
        warnings.append(f"this song cost {spent} credits, not the measured {SONG_PRICE}")
    return {
        "status": "done",
        "clip_id": clip.clip_id,
        "title": clip.title,
        "seconds": None if clip.seconds is None else round(clip.seconds, 1),
        "model": chosen,
        "credits_spent": spent,
        "credits_left": left,
        "project_url": session_url,
        "files": [{"path": str(saved), "mime_type": mime, "bytes": len(data),
                   "media_id": clip.clip_id}],
        "warnings": warnings,
    }


async def flow_music_credits(profile: Profile, *, visible: bool = False,
                             browser: Callable[..., Any] = open_browser) -> dict[str, Any]:
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        credits = await _signed_in_credits(ui)
    return {"credits": credits}


async def _signed_in_credits(ui: Any) -> int | None:
    """Open Flow Music signed in and read its credits; signs in with the profile's Google
    account when the site has no session (an existing Flow Music account goes straight in)."""
    await _load_session(ui)
    status, text = await ui.music_api("GET", "/billing/credits")
    if status == 401:
        await ui.goto(SITE)
        await ui.pause(LOAD_WAIT_MS)
        if await ui.visible("button", "Log in", exact=True):
            await ui.activate("button", "Log in", exact=True)
            await ui.pause(1500)
            await ui.activate("button", "Continue with Google", exact=True)
            await ui.pause(SIGN_IN_WAIT_MS)
        await _load_session(ui)
        status, text = await ui.music_api("GET", "/billing/credits")
    if status == 401:
        raise AuthError("Flow Music (flowmusic.app) is not signed in",
                        hint="run the command with --visible and sign in there with "
                             "\"Continue with Google\"; nothing was spent")
    if status != 200:
        raise _api_error("reading Flow Music credits", status, text)
    return credits_left(text)


async def _load_session(ui: Any) -> None:
    """Open a Flow Music session page and let it finish signing in: on load the page hands the
    Google sign-in to the server (``POST /auth/google/save``). Songs started from code before
    that reply failed at once ("Error while streaming parts", 2026-11-07, nothing spent); the
    UI's own Generate, pressed after it, worked."""
    async with ui.responses(GOOGLE_SAVE) as log:
        await ui.goto(SESSION_URL)
        await log.next("save", timeout_s=GOOGLE_SAVE_TIMEOUT_S)
    await ui.pause(LOAD_WAIT_MS)


async def _ready_clip(ui: Any, clip_id: str) -> Clip:
    clip = None
    for _ in range(CLIP_POLLS):
        status, text = await ui.music_api("POST", "/clips", {"clip_ids": [clip_id]})
        if status == 200:
            clip = clip_of(text, clip_id)
            if clip.ready:
                return clip
        await ui.pause(CLIP_POLL_MS)
    if clip is not None and clip.audio_url:
        return clip  # links, but no length yet: still worth saving
    raise ServerError(f"Flow Music made song {clip_id} but its audio was not ready in time",
                      retriable=True)


def _api_error(what: str, status: int, text: str) -> AIStudioError:
    if status == 401:
        return AuthError(f"Flow Music refused the sign-in while {what}",
                         hint="re-run; if it persists, sign in with --visible")
    detail = text.strip()[:200]
    if status == 402 or "credit" in detail.lower():
        return OverBudgetError(f"Flow Music refused {what}: {detail or f'HTTP {status}'}")
    if status == 0:
        return ServerError(f"Flow Music did not answer while {what}", retriable=True)
    return ServerError(f"{what} failed (HTTP {status}){': ' + detail if detail else ''}",
                       retriable=status >= 500 or status == 429)


def _with_place(hint: str | None, place: str) -> str:
    note = f"the song may still be made: look in Flow Music's Songs ({SITE}/library/my-songs; " \
           f"{place})"
    return f"{note} ({hint})" if hint else note


def _audio_type(data: bytes) -> tuple[str, str]:
    if data[4:8] == b"ftyp":
        return "audio/mp4", ".m4a"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", ".wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg", ".mp3"
    return "application/octet-stream", ".bin"
