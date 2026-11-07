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


LIBRARY_PAGE = 20  # the Songs page asks for 20 at a time (live 2026-11-07)


@dataclasses.dataclass(frozen=True)
class LibrarySong:
    """One song of the user's Flow Music library, as ``GET /clips/auth-user`` and
    ``POST /clips`` describe it. ``model`` is ``None``: the records seen name none."""

    clip_id: str
    title: str
    created: str | None
    seconds: float | None
    model: str | None
    status: str  # "done", "in_progress" or "failed"
    has_lyrics: bool
    prompt: str | None
    lyrics: str | None
    audio_url: str | None
    wav_url: str | None
    session_id: str | None


def library_path(offset: int) -> str:
    return (f"/clips/auth-user?limit={LIBRARY_PAGE}&offset={offset}&filter=generations"
            "&include_disliked=false")


def check_clip_id(value: str) -> str:
    """The song id in canonical form; the server answers anything else with HTTP 422."""
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise ValidationError(f"{value!r} is not a Flow Music song id",
                              hint='ids look like 3f2c15b6-1166-4111-8937-3ecf40ad6f83; see '
                                   '"aistudio flow music list"') from None


def _status(row: dict[str, Any], seconds: float | None, audio: str | None) -> str:
    for field in ("duration", "lyrics"):
        record = row.get(field)
        state = str(record.get("status") or "").lower() if isinstance(record, dict) else ""
        if "fail" in state or "error" in state:
            return "failed"
    return "done" if seconds is not None and audio else "in_progress"


def _song_of(row: Any, where: str) -> LibrarySong:
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
        raise SchemaDriftError(where, ("id",), "a song record")
    duration = row.get("duration")
    seconds = None
    value = duration.get("value") if isinstance(duration, dict) else None
    if isinstance(duration, dict) and duration.get("status") == "completed" \
            and isinstance(value, (str, int, float)):
        try:
            seconds = round(float(value), 1)
        except ValueError:  # a non-numeric string
            seconds = None
    lyrics_record = row.get("lyrics")
    body = lyrics_record.get("value") if isinstance(lyrics_record, dict) else None
    text = body.get("text") if isinstance(body, dict) else None
    lyrics = text if isinstance(text, str) and text.strip() else None
    operation = row.get("operation")
    operation = operation if isinstance(operation, dict) else {}
    audio, wav = row.get("audio_url"), row.get("wav_url")
    audio = audio if isinstance(audio, str) and audio else None
    prompt, model = operation.get("sound_prompt"), operation.get("model")
    session, created = operation.get("conversation_id"), row.get("created_at")
    return LibrarySong(
        clip_id=row["id"], title=str(row.get("title") or "Untitled"),
        created=created if isinstance(created, str) else None, seconds=seconds,
        model=model if isinstance(model, str) and model else None,
        status=_status(row, seconds, audio),
        has_lyrics=lyrics is not None and lyrics.strip().lower() != "[instrumental]",
        prompt=prompt if isinstance(prompt, str) else None, lyrics=lyrics,
        audio_url=audio, wav_url=wav if isinstance(wav, str) and wav else None,
        session_id=session if isinstance(session, str) and session else None)


def library_page(text: str) -> list[LibrarySong]:
    """One page of the user's songs (``{"clips": [...]}``), deleted ones left out."""
    try:
        rows = json.loads(text)["clips"]
    except (ValueError, KeyError, TypeError):
        rows = None
    if not isinstance(rows, list):
        raise SchemaDriftError("GET /clips/auth-user", ("clips",), "a list of songs")
    songs = [_song_of(row, "GET /clips/auth-user") for row in rows]
    return [song for song, row in zip(songs, rows, strict=True) if not row.get("deleted_at")]


def clip_record(text: str, clip_id: str) -> LibrarySong | None:
    """The song ``POST /clips`` describes, or ``None`` for an id the server does not know (it
    answers ``{"clips": {id: null}}``)."""
    try:
        row = json.loads(text)["clips"][clip_id]
    except (ValueError, KeyError, TypeError):
        raise SchemaDriftError("POST /clips", ("clips", clip_id), "the clip record") from None
    if row is None:
        return None
    song = _song_of(row, "POST /clips")
    return None if row.get("deleted_at") else song


def song_matches(song: LibrarySong, search: str) -> bool:
    needle = search.strip().lower()
    return not needle or any(needle in (field or "").lower()
                             for field in (song.title, song.prompt, song.lyrics))
