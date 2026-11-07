"""Flow Music's Compose request and its replies, from the shapes seen live (2026-11-07)."""

import json
import uuid

import httpx
import pytest

from aistudio._flow.download import download_music
from aistudio._flow.music import (
    Clip,
    Song,
    clip_of,
    credits_left,
    events,
    find_model,
    job_id,
    lyrics_id_of,
    lyrics_text,
    song_of,
    song_request,
)
from aistudio.exceptions import (
    GenerationFailedError,
    NetworkError,
    SchemaDriftError,
    ServerError,
    ValidationError,
)

CLIP = "3f2c15b6-1166-4111-8937-3ecf40ad6f83"
CONV = "657edc6d-37c4-4d7a-ab51-c67d23011e3e"
CALL = {"tool_name": "audio__create_song", "args": {"sound_prompt": "lofi"},
        "tool_call_id": "a8", "id": None, "part_kind": "tool-call"}
RETURN = {"tool_name": "audio__create_song",
          "content": {"status": "success", "operation_id": "bf94", "clip_id": CLIP,
                      "title": "Untitled", "operation_id_b": None, "clip_id_b": None,
                      "a_b_test_id": None, "estimated_time": 35.0},
          "tool_call_id": "a8", "timestamp": "2026-11-07T04:27:58Z", "part_kind": "tool-return"}


def stream(*parts, final=True, error=None):
    blocks = ['id: 1-0\nevent: begin\ndata: {"data": {}}',
              f'id: 2-0\nevent: conversation_id\ndata: {json.dumps({"id": CONV})}',
              f'id: 3-0\nevent: message\ndata: {json.dumps({"parts": [CALL], "kind": "response"})}',
              ": ping - 2026-11-07 04:27:53"]
    for part in parts:
        blocks.append(f"id: 4-0\nevent: message\ndata: {json.dumps({'parts': [part]})}")
    if error is not None:
        blocks.append(f"id: 5-0\nevent: error\ndata: {json.dumps(error)}")
    if final:
        blocks.append("id: 6-0\nevent: final\ndata: {}")
    return "\n\n".join(blocks) + "\n\n"


def ids():
    values = iter(["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"])
    return lambda: uuid.UUID(next(values))


def test_an_instrumental_request_matches_compose():
    body = song_request(" upbeat lofi ", ids=ids())
    assert body["part"] == {
        "tool_name": "audio__create_song",
        "args": {"sound_prompt": "upbeat lofi", "lyrics_id": "", "title": "Untitled",
                 "seed": None, "image_id": None},
        "part_kind": "tool-call", "tool_call_id": "11111111-1111-4111-8111-111111111111"}
    context = body["client_context"]
    assert context["selected_model"] == "Lyria 3.5" and context["lyrics_id_map"] == {}
    assert context["song_queue"] == [] and context["client_platform"] == "web"


def test_lyrics_bpm_seed_and_length_match_a_captured_compose_request():
    """The exact args Compose sent (blocked probe, 2026-11-07), lyrics id included."""
    body = song_request("upbeat lofi beat with soft piano",
                        lyrics="[Verse]\nSun on the window, coffee in hand", length_s=180,
                        bpm=92, seed=42, ids=ids())
    lid = "91f8c544-f694-5146-b99e-a5a2cdc9a08c"
    assert body["part"]["args"] == {"sound_prompt": "upbeat lofi beat with soft piano, 92 bpm",
                                    "lyrics_id": lid, "title": "Untitled", "seed": 42,
                                    "image_id": None}
    assert body["client_context"]["lyrics_id_map"] == {
        lid: "[Verse]\nSun on the window, coffee in hand\n[End - 3:00]"}


def test_title_and_model_go_where_compose_puts_them():
    body = song_request("lofi", title=" Morning ", model="Lyria 3 Pro", ids=ids())
    assert body["part"]["args"]["title"] == "Morning"
    assert body["client_context"]["selected_model"] == "Lyria 3 Pro"


def test_lyrics_ids_are_compose_s_uuid5s():
    """Ids Compose computed for these texts (captured live)."""
    assert lyrics_id_of("[Verse]\nSun on the window, coffee in hand") == \
        "f2512176-16b8-5346-a30f-c9f96cde8fbd"
    assert lyrics_id_of("[Instrumental]\n[End - 3:00]") == "96c04733-14d0-580a-81d8-693ad2b3b521"


def test_a_length_without_lyrics_is_an_instrumental_marker():
    assert lyrics_text(None, 180) == "[Instrumental]\n[End - 3:00]"
    assert lyrics_text(None, 125) == "[Instrumental]\n[End - 2:05]"
    assert lyrics_text("  ", None) is None


@pytest.mark.parametrize("kwargs", [{"length_s": 0}, {"length_s": 601}, {"bpm": 0}])
def test_out_of_range_options_are_refused(kwargs):
    with pytest.raises(ValidationError):
        song_request("lofi", **kwargs)


def test_an_empty_prompt_is_refused():
    with pytest.raises(ValidationError, match="empty"):
        song_request("   ")


def test_models_match_case_insensitively():
    assert find_model(None) == "Lyria 3.5"
    assert find_model("lyria 3 pro") == "Lyria 3 Pro"
    with pytest.raises(ValidationError, match="not a Flow Music model"):
        find_model("lyria-2")


def test_job_id():
    assert job_id('{"job_id":"ae86"}') == "ae86"
    with pytest.raises(SchemaDriftError):
        job_id('{"detail":"x"}')


def test_events_skip_pings_and_parse_data():
    names = [name for name, _ in events(stream(RETURN))]
    assert names == ["begin", "conversation_id", "message", "message", "final"]


def test_the_song_comes_from_the_tool_return():
    assert song_of(stream(RETURN)) == Song(CLIP, "Untitled", CONV)


def test_a_stream_still_running_has_no_song():
    assert song_of(stream(final=False)) is None


def test_a_stream_that_ends_without_a_song_failed():
    with pytest.raises(GenerationFailedError, match="without a song"):
        song_of(stream())


def test_an_error_event_fails():
    with pytest.raises(GenerationFailedError, match="content policy"):
        song_of(stream(error={"message": "content policy"}))


def test_a_tool_return_that_did_not_succeed_fails():
    bad = {**RETURN, "content": {"status": "error", "message": "blocked"}}
    with pytest.raises(GenerationFailedError, match="blocked"):
        song_of(stream(bad))


def clips(duration=None, audio=True):
    row = {"id": CLIP, "title": "Untitled",
           "duration": duration or {"status": "completed", "value": "176.93866666666668"},
           "audio_url": f"https://storage.googleapis.com/producer-app-public/clips/{CLIP}.m4a"
           if audio else None,
           "wav_url": f"https://storage.googleapis.com/producer-app-public/clips/{CLIP}.wav"}
    return json.dumps({"clips": {CLIP: row}})


def test_a_finished_clip():
    clip = clip_of(clips(), CLIP)
    assert clip == Clip(CLIP, "Untitled", 176.93866666666668,
                        f"https://storage.googleapis.com/producer-app-public/clips/{CLIP}.m4a",
                        f"https://storage.googleapis.com/producer-app-public/clips/{CLIP}.wav",
                        ready=True)


def test_a_clip_without_its_length_is_not_ready():
    assert not clip_of(clips(duration={"status": "pending"}), CLIP).ready
    assert not clip_of(clips(audio=False), CLIP).ready


def test_a_missing_clip_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        clip_of('{"clips": {}}', CLIP)


def test_credits_left():
    text = '{"data":{"tokens_remaining":127440.0,"credits_remaining":10620.0}}'
    assert credits_left(text) == 10620
    assert credits_left('{"detail":"Unauthorized"}') is None


async def test_download_music_allows_only_the_clip_bucket():
    for url in ("https://storage.googleapis.com/other-bucket/x.m4a",
                "http://storage.googleapis.com/producer-app-public/clips/x.m4a",
                "https://evil.example/producer-app-public/clips/x.m4a"):
        with pytest.raises(ValidationError, match="refusing"):
            await download_music(url)


async def test_download_music_fetches_without_cookies():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"data")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await download_music(
            "https://storage.googleapis.com/producer-app-public/clips/a.m4a", client=client)
    assert data == b"data" and "cookie" not in seen[0].headers


async def test_download_music_errors():
    def refused(request):
        return httpx.Response(403)

    def broken(request):
        raise httpx.ConnectError("down")

    url = "https://storage.googleapis.com/producer-app-public/clips/a.m4a"
    async with httpx.AsyncClient(transport=httpx.MockTransport(refused)) as client:
        with pytest.raises(ServerError, match="HTTP 403"):
            await download_music(url, client=client)
    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(NetworkError):
            await download_music(url, client=client)
