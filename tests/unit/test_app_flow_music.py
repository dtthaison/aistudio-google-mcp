"""``flow_music``: Flow Music songs through the page's own API calls (FakeUi scripts them)."""

import contextlib
import json
from datetime import datetime

import pytest

from aistudio._app.flow_music import flow_music, flow_music_credits
from aistudio._browser.ui import CapturedResponse
from aistudio.exceptions import (
    AuthError,
    GenerationFailedError,
    OverBudgetError,
    ServerError,
    ValidationError,
)
from tests.conftest import FakeUi
from tests.unit.test_flow_music import CLIP, CONV, RETURN, clips, stream

JOB = "ae86b082-2a61-4d36-9357-e20b5a2e9ff7"
M4A = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 16
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "
NOW = datetime(2026, 11, 7, 11, 30, 0)


def ok(text, status=200):
    return CapturedResponse("", status, text, "")


def credits(n):
    return ok(json.dumps({"data": {"tokens_remaining": n * 12.0, "credits_remaining": n}}))


def script(*, before=10620, after=10615, stream_text=None, stream_status=200, clip_rows=None,
           start=None):
    return {
        "GET /billing/credits": [before if isinstance(before, CapturedResponse)
                                 else credits(before), credits(after)],
        "POST /producer/tool-call": [start or ok(json.dumps({"job_id": JOB}))],
        f"GET /messages/{JOB}/stream?last_id=0": [ok(stream_text or stream(RETURN),
                                                     stream_status)],
        "POST /clips": clip_rows or [ok(clips())],
    }


def harness(ui, data=M4A):
    fetched = []

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        harness.window = window
        yield ui

    async def downloader(url):
        fetched.append(url)
        return data

    return {"browser": browser, "downloader": downloader, "now": lambda: NOW}, fetched


async def test_without_a_budget_only_the_price_is_shown(tmp_profile):
    called = []

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        called.append(window)
        yield FakeUi()

    result = await flow_music(tmp_profile, "lofi", browser=browser)
    assert result["status"] == "quoted" and result["credits"] == 5 and called == []
    low = await flow_music(tmp_profile, "lofi", max_credits=4, browser=browser)
    assert low["status"] == "quoted" and called == []


async def test_a_song_is_made_and_saved(tmp_profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ui = FakeUi(responses=script())
    kwargs, fetched = harness(ui)
    started = []
    result = await flow_music(tmp_profile, "upbeat lofi", max_credits=5,
                              on_start=lambda: started.append(1), **kwargs)
    assert started == [1]
    assert result == {
        "status": "done", "clip_id": CLIP, "title": "Untitled", "seconds": 176.9,
        "model": "Lyria 3.5", "credits_spent": 5, "credits_left": 10615,
        "project_url": f"https://www.flowmusic.app/session/{CONV}",
        "files": [{"path": "flow-music-20261107-113000.m4a", "mime_type": "audio/mp4",
                   "bytes": len(M4A), "media_id": CLIP}],
        "warnings": []}
    assert (tmp_path / "flow-music-20261107-113000.m4a").read_bytes() == M4A
    assert fetched == [f"https://storage.googleapis.com/producer-app-public/clips/{CLIP}.m4a"]
    sent = [op for op in ui.ops if op[0] == "music_api" and op[2] == "/producer/tool-call"]
    assert sent[0][3]["part"]["args"]["sound_prompt"] == "upbeat lofi"
    assert ("goto", "https://www.flowmusic.app/session") in ui.ops


async def test_wav_and_an_out_file(tmp_profile, tmp_path):
    ui = FakeUi(responses=script())
    kwargs, fetched = harness(ui, data=WAV)
    out = tmp_path / "song.wav"
    result = await flow_music(tmp_profile, "lofi", wav=True, max_credits=5, out=out, **kwargs)
    assert fetched[0].endswith(".wav") and out.read_bytes() == WAV
    assert result["files"][0]["mime_type"] == "audio/wav" and result["warnings"] == []


async def test_a_wrong_extension_is_warned_about(tmp_profile, tmp_path):
    ui = FakeUi(responses=script())
    kwargs, _ = harness(ui)
    result = await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.mp3",
                              **kwargs)
    assert "holds audio/mp4 data" in result["warnings"][0]


async def test_a_different_length_or_price_is_warned_about(tmp_profile, tmp_path):
    ui = FakeUi(responses=script(after=10610))
    kwargs, _ = harness(ui)
    result = await flow_music(tmp_profile, "lofi", length_s=60, max_credits=5,
                              out=tmp_path / "a.m4a", **kwargs)
    assert result["warnings"] == ["asked for 60 s; Flow Music made 177 s",
                                  "this song cost 10 credits, not the measured 5"]


async def test_an_existing_file_is_refused_before_spending(tmp_profile, tmp_path):
    out = tmp_path / "a.m4a"
    out.write_bytes(b"x")
    ui = FakeUi(responses=script())
    kwargs, _ = harness(ui)
    with pytest.raises(ValidationError, match="already exists"):
        await flow_music(tmp_profile, "lofi", max_credits=5, out=out, **kwargs)
    assert ui.ops == []


async def test_signs_in_when_flow_music_has_no_session(tmp_profile, tmp_path):
    responses = script()
    responses["GET /billing/credits"].insert(0, ok('{"detail":"Unauthorized"}', 401))
    ui = FakeUi(responses=responses, visible={("button", "Log in"): [True]})
    kwargs, _ = harness(ui)
    result = await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a",
                              **kwargs)
    assert result["status"] == "done"
    assert ("activate", "button", "Log in", 0) in ui.ops
    assert ("activate", "button", "Continue with Google", 0) in ui.ops


async def test_still_signed_out_is_an_auth_error_and_spends_nothing(tmp_profile, tmp_path):
    unauthorized = ok('{"detail":"Unauthorized"}', 401)
    ui = FakeUi(responses={"GET /billing/credits": [unauthorized, unauthorized]})
    kwargs, _ = harness(ui)
    with pytest.raises(AuthError, match="not signed in") as err:
        await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a", **kwargs)
    assert "nothing was spent" in err.value.hint
    assert not [op for op in ui.ops if op[0] == "music_api" and op[1] == "POST"]


async def test_too_few_credits_is_refused_before_spending(tmp_profile, tmp_path):
    ui = FakeUi(responses=script(before=3))
    kwargs, _ = harness(ui)
    with pytest.raises(OverBudgetError, match="3 credits left"):
        await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a", **kwargs)
    assert not [op for op in ui.ops if op[0] == "music_api" and op[1] == "POST"]


async def test_a_refused_start_names_the_reason(tmp_profile, tmp_path):
    ui = FakeUi(responses=script(start=ok('{"detail":"Not enough credits"}', 400)))
    kwargs, _ = harness(ui)
    with pytest.raises(OverBudgetError, match="Not enough credits"):
        await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a", **kwargs)


async def test_a_failed_song_says_where_to_look(tmp_profile, tmp_path):
    ui = FakeUi(responses=script(stream_text=stream(error={"message": "content policy"})))
    kwargs, _ = harness(ui)
    with pytest.raises(GenerationFailedError, match="content policy") as err:
        await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a", **kwargs)
    assert JOB in err.value.hint and "my-songs" in err.value.hint


async def test_a_slow_song_times_out_with_its_job(tmp_profile, tmp_path):
    ui = FakeUi(responses=script(stream_text=stream(final=False), stream_status=408))
    kwargs, fetched = harness(ui)
    result = await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a",
                              timeout_s=30, **kwargs)
    assert result["status"] == "timeout" and result["job_id"] == JOB and fetched == []


async def test_waits_for_the_clip_to_be_ready(tmp_profile, tmp_path):
    pending = ok(clips(duration={"status": "pending"}))
    ui = FakeUi(responses=script(clip_rows=[pending, ok(clips())]))
    kwargs, _ = harness(ui)
    result = await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a",
                              **kwargs)
    assert result["seconds"] == 176.9
    assert len([op for op in ui.ops if op[:3] == ("music_api", "POST", "/clips")]) == 2


async def test_a_clip_that_never_gets_audio_is_a_server_error(tmp_profile, tmp_path):
    ui = FakeUi(responses=script(clip_rows=[ok(clips(audio=False))] * 20))
    kwargs, _ = harness(ui)
    with pytest.raises(ServerError, match="not ready") as err:
        await flow_music(tmp_profile, "lofi", max_credits=5, out=tmp_path / "a.m4a", **kwargs)
    assert JOB in err.value.hint


async def test_music_credits(tmp_profile):
    ui = FakeUi(responses={"GET /billing/credits": [credits(10615)]})
    kwargs, _ = harness(ui)
    assert await flow_music_credits(tmp_profile, browser=kwargs["browser"]) == {
        "credits": 10615}


@pytest.fixture
def tmp_profile(aistudio_home):
    from aistudio.paths import get_profile

    return get_profile("default")
