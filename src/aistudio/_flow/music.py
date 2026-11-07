"""Google Flow Music (flowmusic.app): the Compose panel's song request and its replies.

Flow Music is a separate site from Google Flow, with its own credits. Its JSON API lives at
``https://www.flowmusic.app/__api`` and takes the Supabase access token from the site's
``sb-sb-auth-token`` cookies as a Bearer header (findings "Flow Music"). Compose's Generate
button sends ``POST /producer/tool-call`` with the ``audio__create_song`` tool; the reply is a
``job_id`` whose ``GET /messages/<job_id>/stream`` events end in a ``tool-return`` that names the
new clip, and ``POST /clips`` gives that clip's public audio links.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any

from aistudio.exceptions import GenerationFailedError, SchemaDriftError, ValidationError

SITE = "https://www.flowmusic.app"
SESSION_URL = f"{SITE}/session"
MODELS = ("Lyria 3.5", "Lyria 3 Pro")  # GET /__api/models (live 2026-11-07); 3.5 is the default
DEFAULT_MODEL = "Lyria 3.5"
SONG_PRICE = 5  # Flow Music credits for one Compose song on Lyria 3.5 (measured 2026-11-07)
MAX_LENGTH_S = 600  # a sanity bound only; Flow Music's own Length box shows m:ss
TOOL = "audio__create_song"
LYRICS_NAMESPACE = uuid.UUID("b8f9e3a1-7c2d-4f5e-9a8b-1c3d5e7f9a2b")  # from the site's code


@dataclasses.dataclass(frozen=True)
class Song:
    """What the tool-return names: the new clip, and the session that holds it."""

    clip_id: str
    title: str
    conversation_id: str | None


@dataclasses.dataclass(frozen=True)
class Clip:
    clip_id: str
    title: str
    seconds: float | None
    audio_url: str | None  # .m4a (audio/mp4)
    wav_url: str | None
    ready: bool


def find_model(name: str | None) -> str:
    if name is None:
        return DEFAULT_MODEL
    for model in MODELS:
        if model.lower() == name.strip().lower():
            return model
    raise ValidationError(f"{name!r} is not a Flow Music model",
                          hint="choose one of: " + ", ".join(MODELS))


def lyrics_text(lyrics: str | None, length_s: int | None) -> str | None:
    """The lyrics Compose sends: the text (or ``[Instrumental]``) and an ``[End - m:ss]`` line
    for a chosen length. ``None``: an instrumental of Flow Music's own length."""
    body = (lyrics or "").strip()
    if length_s is None:
        return body or None
    if not 1 <= length_s <= MAX_LENGTH_S:
        raise ValidationError(f"--length must be 1 to {MAX_LENGTH_S} seconds")
    minutes, seconds = divmod(length_s, 60)
    return f"{body or '[Instrumental]'}\n[End - {minutes}:{seconds:02d}]"


def lyrics_id_of(text: str) -> str:
    """Compose's id for a lyrics text: a UUIDv5 of the text in the site's own namespace
    (``lyricsIdFromLyricsText``); the server fails the job ("Error while streaming parts")
    for any other id (live 2026-11-07)."""
    return str(uuid.uuid5(LYRICS_NAMESPACE, text))


def song_request(prompt: str, *, lyrics: str | None = None, length_s: int | None = None,
                 bpm: int | None = None, seed: int | None = None, title: str | None = None,
                 model: str = DEFAULT_MODEL, ids: Any = uuid.uuid4) -> dict[str, Any]:
    """The body of ``POST /producer/tool-call`` as Compose builds it (live 2026-11-07): BPM is
    added to the sound prompt, the lyrics travel in ``lyrics_id_map``, the model in
    ``selected_model``."""
    sound = prompt.strip()
    if not sound:
        raise ValidationError("nothing to generate: the music prompt is empty")
    if bpm is not None:
        if not 1 <= bpm <= 400:
            raise ValidationError("--bpm must be 1 to 400")
        sound = f"{sound}, {bpm} bpm"
    text = lyrics_text(lyrics, length_s)
    lyrics_id = lyrics_id_of(text) if text is not None else ""
    return {
        "part": {
            "tool_name": TOOL,
            "args": {"sound_prompt": sound, "lyrics_id": lyrics_id,
                     "title": (title or "").strip() or "Untitled", "seed": seed,
                     "image_id": None},
            "part_kind": "tool-call",
            "tool_call_id": str(ids()),
        },
        "client_context": {
            "song_queue": [],
            "selected_model": model,
            "lyrics_id_map": {lyrics_id: text} if text is not None else {},
            "ghostwriter_version": "standard",
            "onboarding_variant": None,
            "client_platform": "web",
            "clarifying_questions": {"supports_question_options": True,
                                     "supports_multi_question_options": True},
        },
    }


def job_id(text: str) -> str:
    try:
        value = json.loads(text)["job_id"]
    except (ValueError, KeyError, TypeError):
        value = None
    if not isinstance(value, str) or not value:
        raise SchemaDriftError("producer/tool-call", ("job_id",), "a job id")
    return value


def events(text: str) -> list[tuple[str, Any]]:
    """Server-sent events as (event, data); comments (``: ping``) are skipped."""
    out: list[tuple[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if not data:
            continue
        try:
            out.append((name, json.loads("\n".join(data))))
        except ValueError:
            out.append((name, "\n".join(data)))
    return out


def song_of(text: str) -> Song | None:
    """The finished song from a job stream, or ``None`` while it has not ended.

    Raises GenerationFailedError for an ``error`` event or a tool-return that did not succeed."""
    conversation = None
    song = None
    ended = False
    for name, data in events(text):
        if name == "conversation_id" and isinstance(data, dict):
            conversation = data.get("id")
        elif name == "error":
            detail = data.get("message") or data.get("detail") if isinstance(data, dict) else data
            raise GenerationFailedError(f"Flow Music failed: {detail or 'an error event'}")
        elif name == "message" and isinstance(data, dict):
            for part in data.get("parts") or []:
                if part.get("part_kind") != "tool-return" or part.get("tool_name") != TOOL:
                    continue
                content = part.get("content") or {}
                if not isinstance(content, dict) or content.get("status") != "success":
                    reason = (content.get("message") or content.get("status")
                              if isinstance(content, dict) else content)
                    raise GenerationFailedError(f"Flow Music did not make the song: {reason}")
                clip = content.get("clip_id")
                if not isinstance(clip, str) or not clip:
                    raise SchemaDriftError("messages stream", ("content", "clip_id"), "a clip id")
                song = Song(clip, str(content.get("title") or "Untitled"), None)
        elif name == "final":
            ended = True
    if song is None:
        if ended:
            raise GenerationFailedError("Flow Music ended the job without a song")
        return None
    return dataclasses.replace(song, conversation_id=conversation)


def clip_of(text: str, clip_id: str) -> Clip:
    try:
        row = json.loads(text)["clips"][clip_id]
    except (ValueError, KeyError, TypeError):
        raise SchemaDriftError("POST /clips", ("clips", clip_id), "the clip record") from None
    duration = row.get("duration") or {}
    seconds = None
    if not isinstance(duration, dict):
        duration = {}
    value = duration.get("value")
    if duration.get("status") == "completed" and isinstance(value, (str, int, float)):
        try:
            seconds = float(value)
        except ValueError:  # a non-numeric string
            seconds = None
    audio, wav = row.get("audio_url"), row.get("wav_url")
    return Clip(clip_id, str(row.get("title") or "Untitled"), seconds,
                audio if isinstance(audio, str) else None,
                wav if isinstance(wav, str) else None,
                ready=bool(audio) and seconds is not None)


def credits_left(text: str) -> int | None:
    try:
        value = json.loads(text)["data"]["credits_remaining"]
    except (ValueError, KeyError, TypeError):
        return None
    return int(value) if isinstance(value, (int, float)) else None
