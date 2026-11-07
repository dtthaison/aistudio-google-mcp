"""``aistudio flow music list|get|download``, and that ``flow music PROMPT`` still generates."""

import json

from click.testing import CliRunner

from aistudio.cli.main import cli
from tests.cli.test_flow_cmd import fake
from tests.cli.test_flow_music_cmd import MUSIC_DONE

SONG_ID = "3f2c15b6-1166-4111-8937-3ecf40ad6f83"
SONGS = {"songs": [
    {"clip_id": SONG_ID, "title": "Morning", "created": "2026-11-07T04:27:58.504133Z",
     "seconds": 176.9, "model": None, "status": "done", "has_lyrics": False},
    {"clip_id": "22222222-2222-4222-8222-222222222222", "title": "Broken", "created": None,
     "seconds": None, "model": "Lyria 3 Pro", "status": "failed", "has_lyrics": True}],
    "count": 2}


def test_list_passes_options_and_prints_json(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_list", fake(SONGS, calls))
    res = CliRunner().invoke(cli, ["flow", "music", "list", "--search", "lofi", "--visible",
                                   "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == SONGS
    assert calls[0] == ((), {"search": "lofi", "visible": True})


def test_list_renders_a_line_per_song(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_list", fake(SONGS, []))
    res = CliRunner().invoke(cli, ["flow", "music", "list"])
    assert res.exit_code == 0, res.output
    lines = res.stdout.splitlines()
    assert lines[0].split() == [SONG_ID, "done", "2:57", "Morning"]
    assert "failed" in lines[1] and "Broken" in lines[1]


def test_list_with_no_songs_says_so(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_list",
                        fake({"songs": [], "count": 0}, []))
    res = CliRunner().invoke(cli, ["flow", "music", "list"])
    assert res.exit_code == 0 and "No songs" in res.stdout


def test_download_passes_options(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    result = {"clip_id": SONG_ID, "title": "Morning", "seconds": 176.9, "project_url": "u",
              "files": [{"path": "a.wav", "mime_type": "audio/wav", "bytes": 9,
                         "media_id": SONG_ID}], "warnings": []}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_download", fake(result, calls))
    out = tmp_path / "a.wav"
    res = CliRunner().invoke(cli, ["flow", "music", "download", SONG_ID, "-o", str(out), "--wav",
                                   "--force", "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == result
    assert calls[0] == ((SONG_ID,), {"wav": True, "out": out, "overwrite": True,
                                     "visible": True})


def test_download_prints_where_it_wrote(logged_in_profile, monkeypatch):
    logged_in_profile()
    result = {"clip_id": SONG_ID, "title": "Morning", "seconds": 176.9, "project_url": "u",
              "files": [{"path": "a.m4a", "mime_type": "audio/mp4", "bytes": 9,
                         "media_id": SONG_ID}], "warnings": ["careful"]}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_download", fake(result, []))
    res = CliRunner().invoke(cli, ["flow", "music", "download", SONG_ID])
    assert res.exit_code == 0, res.output
    assert "Wrote a.m4a (9 bytes)" in res.stdout and "Warning: careful" in res.output


def test_get_prints_the_prompt_and_lyrics(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    detail = {**SONGS["songs"][0], "has_lyrics": True, "prompt": "upbeat lofi",
              "lyrics": "[Verse]\nSun", "audio_url": "https://x/a.m4a",
              "wav_url": "https://x/a.wav", "project_url": "https://www.flowmusic.app/session/s"}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_get", fake(detail, calls))
    res = CliRunner().invoke(cli, ["flow", "music", "get", SONG_ID])
    assert res.exit_code == 0, res.output
    assert calls[0] == ((SONG_ID,), {"visible": False})
    for text in ("Morning", "upbeat lofi", "[Verse]", "Sun", "2:57"):
        assert text in res.stdout
    res = CliRunner().invoke(cli, ["flow", "music", "get", SONG_ID, "--json"])
    assert json.loads(res.stdout) == detail


def test_a_prompt_still_generates_with_or_without_the_generate_word(logged_in_profile,
                                                                    monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music", fake(MUSIC_DONE, calls))
    for args in (["lofi", "--max-credits", "5"], ["--max-credits", "5", "lofi"],
                 ["generate", "lofi", "--max-credits", "5"],
                 ["lofi", "-m", "Lyria 3.5", "--max-credits", "5"]):
        res = CliRunner().invoke(cli, ["flow", "music", *args])
        assert res.exit_code == 0, (args, res.output)
    assert [c[0] for c in calls] == [("lofi",)] * 4
    assert all(c[1]["max_credits"] == 5 for c in calls)


def test_the_library_commands_never_take_a_budget(logged_in_profile):
    for name in ("list", "get", "download"):
        res = CliRunner().invoke(cli, ["flow", "music", name, "--help"])
        assert res.exit_code == 0 and "--max-credits" not in res.stdout


def test_music_help_names_the_subcommands(logged_in_profile):
    res = CliRunner().invoke(cli, ["flow", "music", "--help"])
    assert res.exit_code == 0
    for word in ("list", "get", "download", "generate"):
        assert word in res.stdout
