"""``flow_music_list``, ``flow_music_get`` and ``flow_music_download``: the Flow Music library
through the page's own API calls (FakeUi scripts them). All free: nothing here posts a song."""

import json

import pytest

from aistudio._app.flow_music import flow_music_download, flow_music_get, flow_music_list
from aistudio.exceptions import AuthError, NotFoundError, ServerError, ValidationError
from tests.conftest import FakeUi
from tests.unit.test_app_flow_music import M4A, WAV, credits, ok
from tests.unit.test_app_flow_music import harness as music_harness
from tests.unit.test_flow_music_library import BASE, A, B, C, listing, row

PAGE = "/clips/auth-user?limit=20&offset={}&filter=generations&include_disliked=false"


def library(*pages):
    responses = {"GET /billing/credits": [credits(10615)] * 3}
    for n, page in enumerate(pages):
        responses["GET " + PAGE.format(20 * n)] = [ok(page)]
    return responses


def harness(ui, data=M4A):
    kwargs, fetched = music_harness(ui, data)
    del kwargs["now"]  # downloads are named after the song, not the time
    return kwargs, fetched


@pytest.fixture
def tmp_profile(aistudio_home):
    from aistudio.paths import get_profile

    return get_profile("default")


async def test_list_returns_every_song_with_the_fields_a_script_needs(tmp_profile):
    failed = row(B, title="Broken", duration={"status": "failed"}, audio=False)
    ui = FakeUi(responses=library(listing(row(A, lyrics="[Verse]\nhi"), failed), listing()))
    kwargs, _ = harness(ui)
    result = await flow_music_list(tmp_profile, browser=kwargs["browser"])
    assert result["count"] == 2
    assert result["songs"][0] == {
        "clip_id": A, "title": "Morning", "created": "2026-11-07T04:27:58.504133Z",
        "seconds": 176.9, "model": None, "status": "done", "has_lyrics": True}
    assert result["songs"][1]["status"] == "failed" and result["songs"][1]["seconds"] is None
    assert all(op[1] == "GET" for op in ui.ops if op[0] == "music_api")


async def test_list_follows_the_pages_until_an_empty_one(tmp_profile):
    first = listing(*[row(f"{n:08d}-1111-4111-8111-111111111111") for n in range(20)])
    ui = FakeUi(responses=library(first, listing(row(A)), listing()))
    kwargs, _ = harness(ui)
    result = await flow_music_list(tmp_profile, browser=kwargs["browser"])
    assert result["count"] == 21
    asked = [op[2] for op in ui.ops if op[0] == "music_api" and "clips" in op[2]]
    assert asked == [PAGE.format(0), PAGE.format(20), PAGE.format(40)]


async def test_list_stops_when_the_server_ignores_the_offset(tmp_profile):
    page = listing(row(A))
    responses = {"GET /billing/credits": [credits(1)],
                 **{"GET " + PAGE.format(n): [ok(page)] for n in range(0, 400, 20)}}
    ui = FakeUi(responses=responses)
    kwargs, _ = harness(ui)
    result = await flow_music_list(tmp_profile, browser=kwargs["browser"])
    assert result["count"] == 1


async def test_list_search_filters_by_title_prompt_or_lyrics(tmp_profile):
    page = listing(row(A, title="Morning"), row(B, title="Night", prompt="slow jazz"))
    ui = FakeUi(responses=library(page, listing()))
    kwargs, _ = harness(ui)
    result = await flow_music_list(tmp_profile, search="JAZZ", browser=kwargs["browser"])
    assert [s["clip_id"] for s in result["songs"]] == [B]
    assert result["count"] == 1


async def test_list_signed_out_is_an_auth_error(tmp_profile):
    unauthorized = ok('{"detail":"Unauthorized"}', 401)
    ui = FakeUi(responses={"GET /billing/credits": [unauthorized, unauthorized]})
    kwargs, _ = harness(ui)
    with pytest.raises(AuthError, match="not signed in"):
        await flow_music_list(tmp_profile, browser=kwargs["browser"])


async def test_list_server_error_names_the_step(tmp_profile):
    ui = FakeUi(responses={"GET /billing/credits": [credits(1)],
                           "GET " + PAGE.format(0): [ok("boom", 500)]})
    kwargs, _ = harness(ui)
    with pytest.raises(ServerError, match="listing your songs"):
        await flow_music_list(tmp_profile, browser=kwargs["browser"])


def clip_reply(clip_id=A, **kwargs):
    return ok(json.dumps({"clips": {clip_id: row(clip_id, **kwargs)}}))


def one_clip(**kwargs):
    return {"GET /billing/credits": [credits(1)], "POST /clips": [clip_reply(**kwargs)]}


def unknown_clip():
    return {"GET /billing/credits": [credits(1)],
            "POST /clips": [ok(json.dumps({"clips": {C: None}}))]}


async def test_get_shows_the_prompt_and_the_lyrics(tmp_profile):
    ui = FakeUi(responses=one_clip(lyrics="[Verse]\nhi"))
    kwargs, _ = harness(ui)
    result = await flow_music_get(tmp_profile, A.upper(), browser=kwargs["browser"])
    assert result["clip_id"] == A and result["prompt"] == "upbeat lofi beat with soft piano"
    assert result["lyrics"] == "[Verse]\nhi" and result["has_lyrics"]
    assert result["audio_url"] == f"{BASE}/{A}.m4a" and result["wav_url"] == f"{BASE}/{A}.wav"
    assert result["project_url"] == f"https://www.flowmusic.app/session/{B}"
    assert ("music_api", "POST", "/clips", {"clip_ids": [A]}) in ui.ops


async def test_get_an_unknown_song_is_not_found(tmp_profile):
    ui = FakeUi(responses=unknown_clip())
    kwargs, _ = harness(ui)
    with pytest.raises(NotFoundError, match="has no song") as err:
        await flow_music_get(tmp_profile, C, browser=kwargs["browser"])
    assert "flow music list" in err.value.hint


async def test_get_refuses_a_bad_id_before_opening_the_browser(tmp_profile):
    ui = FakeUi()
    kwargs, _ = harness(ui)
    with pytest.raises(ValidationError, match="not a Flow Music song id"):
        await flow_music_get(tmp_profile, "nope", browser=kwargs["browser"])
    assert ui.ops == []


async def test_download_saves_the_m4a(tmp_profile, tmp_path):
    ui = FakeUi(responses=one_clip())
    kwargs, fetched = harness(ui)
    out = tmp_path / "s.m4a"
    result = await flow_music_download(tmp_profile, A, out=out, **kwargs)
    assert fetched == [f"{BASE}/{A}.m4a"] and out.read_bytes() == M4A
    assert result["files"] == [{"path": str(out), "mime_type": "audio/mp4",
                                "bytes": len(M4A), "media_id": A}]
    assert result["clip_id"] == A and result["title"] == "Morning"
    assert result["project_url"] == f"https://www.flowmusic.app/session/{B}"
    posts = [op for op in ui.ops if op[0] == "music_api" and op[1] == "POST"]
    assert [p[2] for p in posts] == ["/clips"]  # a lookup; nothing is created


async def test_download_wav(tmp_profile, tmp_path):
    ui = FakeUi(responses=one_clip())
    kwargs, fetched = harness(ui, data=WAV)
    result = await flow_music_download(tmp_profile, A, wav=True, out=tmp_path / "s.wav",
                                       **kwargs)
    assert fetched == [f"{BASE}/{A}.wav"]
    assert result["files"][0]["mime_type"] == "audio/wav"


async def test_download_default_name_is_the_song_id_in_the_current_folder(
        tmp_profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui = FakeUi(responses=one_clip())
    kwargs, _ = harness(ui)
    result = await flow_music_download(tmp_profile, A, **kwargs)
    assert result["files"][0]["path"] == f"flow-music-{A}.m4a"
    assert (tmp_path / f"flow-music-{A}.m4a").exists()


async def test_download_never_overwrites_the_default_name(tmp_profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / f"flow-music-{A}.m4a").write_bytes(b"mine")
    ui = FakeUi(responses=one_clip())
    kwargs, _ = harness(ui)
    result = await flow_music_download(tmp_profile, A, **kwargs)
    assert result["files"][0]["path"] == f"flow-music-{A}-2.m4a"
    assert (tmp_path / f"flow-music-{A}.m4a").read_bytes() == b"mine"


async def test_download_refuses_an_existing_out_before_any_browser_work(tmp_profile, tmp_path):
    out = tmp_path / "s.m4a"
    out.write_bytes(b"mine")
    ui = FakeUi(responses=one_clip())
    kwargs, fetched = harness(ui)
    with pytest.raises(ValidationError, match="already exists"):
        await flow_music_download(tmp_profile, A, out=out, **kwargs)
    assert ui.ops == [] and fetched == [] and out.read_bytes() == b"mine"


async def test_download_overwrites_with_force(tmp_profile, tmp_path):
    out = tmp_path / "s.m4a"
    out.write_bytes(b"mine")
    ui = FakeUi(responses=one_clip())
    kwargs, _ = harness(ui)
    await flow_music_download(tmp_profile, A, out=out, overwrite=True, **kwargs)
    assert out.read_bytes() == M4A


async def test_download_warns_when_the_extension_does_not_match(tmp_profile, tmp_path):
    ui = FakeUi(responses=one_clip())
    kwargs, _ = harness(ui)
    result = await flow_music_download(tmp_profile, A, out=tmp_path / "s.mp3", **kwargs)
    assert "audio/mp4" in result["warnings"][0]


async def test_download_of_a_song_still_running_is_refused(tmp_profile, tmp_path):
    ui = FakeUi(responses=one_clip(duration={"status": "pending"}, audio=False))
    kwargs, fetched = harness(ui)
    with pytest.raises(ValidationError, match="in progress") as err:
        await flow_music_download(tmp_profile, A, out=tmp_path / "s.m4a", **kwargs)
    assert "flow music list" in err.value.hint and fetched == []


async def test_download_of_a_failed_song_is_refused(tmp_profile, tmp_path):
    ui = FakeUi(responses=one_clip(duration={"status": "failed"}, audio=False))
    kwargs, fetched = harness(ui)
    with pytest.raises(ValidationError, match="failed"):
        await flow_music_download(tmp_profile, A, out=tmp_path / "s.m4a", **kwargs)
    assert fetched == []


async def test_download_without_a_wav_link_says_so(tmp_profile, tmp_path):
    record = row()
    record["wav_url"] = None
    ui = FakeUi(responses={"GET /billing/credits": [credits(1)],
                           "POST /clips": [ok(json.dumps({"clips": {A: record}}))]})
    kwargs, fetched = harness(ui)
    with pytest.raises(ServerError, match="no wav link"):
        await flow_music_download(tmp_profile, A, wav=True, out=tmp_path / "s.wav", **kwargs)
    assert fetched == []


async def test_download_unknown_song_is_not_found(tmp_profile, tmp_path):
    ui = FakeUi(responses=unknown_clip())
    kwargs, _ = harness(ui)
    with pytest.raises(NotFoundError):
        await flow_music_download(tmp_profile, C, out=tmp_path / "s.m4a", **kwargs)
