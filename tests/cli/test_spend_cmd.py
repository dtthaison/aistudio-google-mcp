import json

import pytest
from click.testing import CliRunner

from aistudio._app import spend
from aistudio.cli.main import cli
from aistudio.exceptions import GenerationFailedError, ValidationError, WaitTimeoutError
from aistudio.paths import get_profile
from tests.cli.test_flow_cmd import DONE, QUOTED, fake
from tests.cli.test_spend_guard import commands


def log(profile="default"):
    return spend.read_entries(get_profile(profile))


def test_a_paid_flow_run_is_logged(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(DONE, []))
    res = CliRunner().invoke(cli, ["flow", "video", "an apple prompt", "--max-credits", "10",
                                   "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == DONE  # the result is untouched
    (got,) = log()
    assert (got["command"], got["status"], got["credits_spent"], got["max_credits"]) == (
        "flow video", "done", 10, 10)
    assert got["profile"] == "default" and got["media_ids"] == ["m1"]
    assert "apple" not in json.dumps(got)


@pytest.mark.parametrize("args", [["flow", "music", "lofi"],
                                  ["flow", "music", "generate", "lofi"]])
def test_a_paid_song_is_logged_whichever_way_it_is_called(logged_in_profile, monkeypatch, args):
    # `flow music PROMPT` is routed to the `generate` subcommand: both spellings must be logged.
    logged_in_profile()
    song = {"status": "done", "clip_id": "c1", "credits_spent": 5, "files": []}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_music", fake(song, []))
    res = CliRunner().invoke(cli, [*args, "--max-credits", "5", "--json"])
    assert res.exit_code == 0, res.output
    (got,) = log()
    assert (got["command"], got["unit"], got["credits_spent"]) == (
        "flow music generate", "flow_music_credits", 5)


def test_a_quote_is_not_logged(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(QUOTED, []))
    assert CliRunner().invoke(cli, ["flow", "video", "x", "--json"]).exit_code == 0
    assert log() == []


def test_a_timeout_is_logged_and_still_exits_2(logged_in_profile, monkeypatch):
    logged_in_profile()
    timeout = {"status": "timeout", "media_ids": ["m1"], "credits_spent": 10, "project_url": "u"}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(timeout, []))
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--max-credits", "10", "--json"])
    assert res.exit_code == 2
    assert log()[0]["status"] == "timeout"


def test_a_failed_paid_run_is_logged_and_the_error_is_unchanged(logged_in_profile, monkeypatch):
    logged_in_profile()

    async def boom(profile, *args, **kwargs):
        raise GenerationFailedError("Flow failed m9: filter with a private prompt", hint="h")

    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", boom)
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--max-credits", "10", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["code"] == "GENERATION_FAILED"
    (got,) = log()
    assert got["status"] == "failed" and got["error_code"] == "GENERATION_FAILED"
    assert got["credits_spent"] is None and got["max_credits"] == 10
    assert "private" not in json.dumps(got)


def test_a_wait_timeout_error_is_logged_as_timeout(logged_in_profile, monkeypatch):
    logged_in_profile()

    async def slow(profile, *args, **kwargs):
        raise WaitTimeoutError("still generating")

    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", slow)
    res = CliRunner().invoke(cli, ["flow", "image", "x", "--json"])
    assert res.exit_code == 2
    assert log()[0]["status"] == "timeout"


def test_a_refusal_before_anything_was_sent_is_not_logged(logged_in_profile, monkeypatch):
    logged_in_profile()

    async def refused(profile, *args, **kwargs):
        raise ValidationError("nothing to generate")

    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", refused)
    assert CliRunner().invoke(cli, ["flow", "image", "x", "--json"]).exit_code == 1
    assert log() == []


def test_generate_and_the_profile_option(logged_in_profile, monkeypatch):
    logged_in_profile("work")
    result = {"files": [{"path": "a.jpg", "mime_type": "image/jpeg", "bytes": 3}],
              "model": "gemini-3.1-flash-lite-image"}

    async def run(profile, *args, on_start=None, **kwargs):
        return result

    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_image", run)
    res = CliRunner().invoke(cli, ["-p", "work", "generate", "image", "a cat", "--json"])
    assert res.exit_code == 0, res.output
    (got,) = log("work")
    assert got["profile"] == "work" and got["usd_estimate"] == 0.034
    assert log("default") == []


def test_a_broken_log_never_changes_the_result(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(DONE, []))

    def explode(profile, entry):
        raise OSError("disk full")

    monkeypatch.setattr(spend, "append_entry", explode)
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--max-credits", "10", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.stdout) == DONE
    assert "could not write the spend log" in res.stderr and "disk full" in res.stderr


def test_an_unwritable_log_only_warns(logged_in_profile, monkeypatch):
    profile = logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(DONE, []))
    profile.spend_log.mkdir()  # a folder where the file should be
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--max-credits", "10", "--json"])
    assert res.exit_code == 0 and json.loads(res.stdout) == DONE
    assert "could not write the spend log" in res.stderr


def test_spend_lists_entries_and_totals_as_json(logged_in_profile):
    profile = logged_in_profile()
    spend.append_entry(profile, {
        "ts": "2026-11-07T10:00:00Z", "command": "flow video", "unit": "flow_credits",
        "status": "done", "credits_spent": 10, "model": "veo-3.1-lite", "profile": "default"})
    res = CliRunner().invoke(cli, ["spend", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["count"] == 1 and data["totals"]["flow_credits"]["credits_spent"] == 10


def test_spend_filters_by_since_and_command(logged_in_profile):
    profile = logged_in_profile()
    for ts, command in (("2020-01-01T00:00:00Z", "flow video"),
                        ("2099-01-01T00:00:00Z", "flow video"),
                        ("2099-01-01T00:00:00Z", "generate image")):
        spend.append_entry(profile, {"ts": ts, "command": command, "unit": "flow_credits",
                                     "status": "done", "credits_spent": 1})
    data = json.loads(CliRunner().invoke(
        cli, ["spend", "--since", "2021-01-01", "--command", "flow", "--json"]).stdout)
    assert data["count"] == 1
    bad = CliRunner().invoke(cli, ["spend", "--since", "soon", "--json"])
    assert bad.exit_code == 1 and json.loads(bad.stdout)["code"] == "VALIDATION_ERROR"


def test_spend_prints_a_table_and_totals(logged_in_profile):
    profile = logged_in_profile()
    spend.append_entry(profile, {
        "ts": "2026-11-07T10:00:00Z", "command": "flow music", "unit": "flow_music_credits",
        "status": "done", "credits_spent": 5, "model": "Lyria 3.5"})
    spend.append_entry(profile, {
        "ts": "2026-11-07T10:05:00Z", "command": "flow video", "unit": "flow_credits",
        "status": "failed", "credits_spent": None, "max_credits": 10})
    res = CliRunner().invoke(cli, ["spend"])
    assert res.exit_code == 0, res.output
    out = res.stdout
    assert "flow music" in out and "Lyria 3.5" in out and "failed" in out
    assert "Flow Music credits: 5" in out
    assert "Flow credits: 0" in out and "1 unverified" in out
    assert "AI Studio" in out


def test_spend_with_an_empty_log(logged_in_profile):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["spend"])
    assert res.exit_code == 0 and "No spending logged" in res.stdout


@pytest.mark.parametrize("path", sorted(spend.SPENDING_COMMANDS))
def test_every_spending_command_is_a_real_command(path):
    assert f"aistudio {path}" in {p for p, _ in commands(cli)}


def test_the_list_covers_the_paid_commands():
    assert {"flow video", "flow image", "flow extend", "flow edit", "flow music generate",
            "flow storyboard", "flow character create", "flow character body",
            "flow character voice",
            "flow tool use", "generate speech", "generate image", "generate music",
            "transcribe", "app create", "app prompt"} == set(spend.SPENDING_COMMANDS)
