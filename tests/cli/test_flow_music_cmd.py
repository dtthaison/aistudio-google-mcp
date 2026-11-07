import json

from click.testing import CliRunner

from aistudio.cli.main import cli
from tests.cli.test_flow_cmd import fake

MUSIC_DONE = {"status": "done", "clip_id": "c1", "title": "Morning", "seconds": 176.9,
              "model": "Lyria 3.5", "credits_spent": 5, "credits_left": 10615,
              "project_url": "https://www.flowmusic.app/session/s1",
              "files": [{"path": "a.m4a", "mime_type": "audio/mp4", "bytes": 9,
                         "media_id": "c1"}], "warnings": []}


def test_flow_music_passes_every_option(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music", fake(MUSIC_DONE, calls))
    words = tmp_path / "lyrics.txt"
    words.write_text("[Verse]\nSun", encoding="utf-8")
    out = tmp_path / "a.m4a"
    res = CliRunner().invoke(cli, ["flow", "music", "lofi", "--lyrics-file", str(words),
                                   "--length", "90", "--bpm", "92", "--seed", "7",
                                   "--title", "Morning", "-m", "lyria 3 pro", "--wav",
                                   "--max-credits", "5", "-o", str(out), "--force",
                                   "--timeout", "120", "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == MUSIC_DONE
    assert calls[0] == (("lofi",), {
        "lyrics": "[Verse]\nSun", "overwrite": True, "timeout_s": 120.0, "visible": True,
        "on_start": None, "length_s": 90, "bpm": 92, "seed": 7, "title": "Morning",
        "model": "Lyria 3 Pro", "wav": True, "max_credits": 5, "out": out})


def test_flow_music_renders_the_song_and_the_quote(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music", fake(MUSIC_DONE, []))
    res = CliRunner().invoke(cli, ["flow", "music", "lofi", "--max-credits", "5"])
    assert res.exit_code == 0, res.output
    assert 'Made "Morning", 177 s: https://www.flowmusic.app/session/s1' in res.stdout
    assert "Wrote a.m4a (9 bytes)" in res.stdout and "Spent 5 credits; 10615 left" in res.stdout
    quoted = {"status": "quoted", "credits": 5, "count": 1, "declined": False, "plan": "p",
              "model": "Lyria 3.5"}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music", fake(quoted, []))
    res = CliRunner().invoke(cli, ["flow", "music", "lofi"])
    assert "costs 5 credits" in res.stdout and "--max-credits 5" in res.stdout


def test_flow_music_times_out_with_exit_2(logged_in_profile, monkeypatch):
    logged_in_profile()
    timeout = {"status": "timeout", "job_id": "j", "credits_spent": None,
               "project_url": "https://www.flowmusic.app/session", "files": [], "warnings": []}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music", fake(timeout, []))
    res = CliRunner().invoke(cli, ["flow", "music", "lofi", "--max-credits", "5"])
    assert res.exit_code == 2 and "Still running" in res.stdout


def test_flow_music_refuses_two_lyrics_sources(logged_in_profile, tmp_path):
    logged_in_profile()
    words = tmp_path / "l.txt"
    words.write_text("x", encoding="utf-8")
    res = CliRunner().invoke(cli, ["flow", "music", "lofi", "--lyrics", "a",
                                   "--lyrics-file", str(words)])
    assert res.exit_code != 0 and "not both" in res.output


def test_flow_credits_music(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music_credits",
                        fake({"credits": 10615}, []))
    res = CliRunner().invoke(cli, ["flow", "credits", "--music"])
    assert res.exit_code == 0, res.output
    assert "10615 Flow Music credits left" in res.stdout
