"""``aistudio flow image --prompts-file``: the command around ``flow_image_batch``."""

import json

import pytest
from click.testing import CliRunner

from aistudio.cli.inputs import read_prompts
from aistudio.cli.main import cli
from aistudio.exceptions import ValidationError

DONE_ONE = {"index": 1, "prompt": "a red apple", "status": "done", "credits_spent": 0,
            "media_ids": ["m1"], "files": [{"path": "o/01-a-red-apple-1.jpg",
                                            "mime_type": "image/jpeg", "bytes": 5,
                                            "media_id": "m1"}]}
DONE = {"status": "done", "total": 1, "start_at": 1, "results": [DONE_ONE], "credits_spent": 0}
STOPPED = {
    "status": "stopped", "total": 3, "start_at": 1, "credits_spent": 0, "resume_at": 2,
    "error": {"code": "AGENT_REPLY", "message": "Flow's agent replied without a quote: Which?",
              "hint": "rephrase the prompt"},
    "results": [DONE_ONE,
                {"index": 2, "prompt": "a pear", "status": "failed", "credits_spent": None,
                 "media_ids": [], "files": [], "error": {"code": "AGENT_REPLY", "message": "x"}},
                {"index": 3, "prompt": "a fig", "status": "not_run"}]}
QUOTED = {"status": "quoted", "total": 2, "start_at": 1, "credits_spent": 0, "quote_credits": 4,
          "results": [{"index": 1, "prompt": "a", "status": "quoted", "quote_credits": 2},
                      {"index": 2, "prompt": "b", "status": "quoted", "quote_credits": 2}]}


def fake(result, calls):
    async def run(profile, prompts, **kwargs):
        calls.append((prompts, kwargs))
        return result

    return run


@pytest.fixture
def files(tmp_path):
    folder = tmp_path / "shots"
    folder.mkdir()
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text('"a red apple"\n\n{"prompt": "two\\nlines"}\n', encoding="utf-8")
    return prompts, folder


def test_the_batch_passes_every_option(logged_in_profile, monkeypatch, files):
    logged_in_profile()
    prompts, folder = files
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(DONE, calls))
    res = CliRunner().invoke(cli, [
        "flow", "image", "--prompts-file", str(prompts), "-o", str(folder), "-m", "nano-banana-2.1",
        "--aspect", "9:16", "--count", "2", "--max-credits", "6", "--project", "p1", "--force",
        "--timeout", "300", "--ingredient", "m1", "--character", "Robo", "--start-at", "2",
        "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == DONE
    texts, kwargs = calls[0]
    assert texts == ["a red apple", "two\nlines"]
    assert kwargs == {
        "out": folder, "overwrite": True, "start_at": 2, "model": "nano-banana-2.1",
        "aspect": "9:16", "count": 2, "max_credits": 6, "project": "p1", "timeout_s": 300.0,
        "visible": True, "ingredients": ["m1"], "characters": ["Robo"], "on_start": None}


def test_the_batch_needs_a_folder_and_excludes_a_single_prompt(logged_in_profile, monkeypatch,
                                                               files):
    logged_in_profile()
    prompts, folder = files
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(DONE, calls))
    for args in (["--prompts-file", str(prompts)],
                 ["--prompts-file", str(prompts), "-o", str(folder), "an apple"],
                 ["--prompts-file", str(prompts), "-o", str(folder), "--prompt-file", str(prompts)],
                 ["--prompts-file", str(prompts), "-o", str(folder), "--i-have-rights"],
                 ["an apple", "--start-at", "2"]):
        res = CliRunner().invoke(cli, ["flow", "image", *args, "--json"])
        assert res.exit_code == 1, (args, res.output)
        assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"
    assert calls == []


def test_a_bad_prompts_file_fails_before_the_browser(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(DONE, calls))
    bad = tmp_path / "bad.jsonl"
    bad.write_text('"fine"\nnot json\n')
    res = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(bad), "-o",
                                   str(tmp_path), "--json"])
    assert res.exit_code == 1 and "line 2" in json.loads(res.stdout)["message"]
    assert calls == []


def test_a_finished_batch_lists_each_file(logged_in_profile, monkeypatch, files):
    logged_in_profile()
    prompts, folder = files
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(DONE, []))
    res = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(prompts), "-o",
                                   str(folder)])
    assert res.exit_code == 0, res.output
    assert "1 of 1 prompts done" in res.stdout and "01-a-red-apple-1.jpg" in res.stdout


def test_a_quoted_batch_says_how_to_spend_the_total(logged_in_profile, monkeypatch, files):
    logged_in_profile()
    prompts, folder = files
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(QUOTED, []))
    res = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(prompts), "-o",
                                   str(folder)])
    assert res.exit_code == 0, res.output
    assert "4 credits for 2 prompts" in res.stdout and "--max-credits 4" in res.stdout
    assert "Nothing was spent" in res.stdout


def test_a_stopped_batch_exits_1_and_says_where_to_resume(logged_in_profile, monkeypatch, files):
    logged_in_profile()
    prompts, folder = files
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(STOPPED, []))
    res = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(prompts), "-o",
                                   str(folder)])
    assert res.exit_code == 1
    assert "1 of 3 prompts done" in res.stdout and "--start-at 2" in res.stdout
    assert "Which?" in res.output and "not run" in res.stdout
    res = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(prompts), "-o",
                                   str(folder), "--json"])
    assert res.exit_code == 1 and json.loads(res.stdout) == STOPPED


def test_a_timeout_in_the_batch_exits_2(logged_in_profile, monkeypatch, files):
    logged_in_profile()
    prompts, folder = files
    stopped = {**STOPPED, "error": {"code": "TIMEOUT", "message": "still generating"}}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_image_batch", fake(stopped, []))
    res = CliRunner().invoke(cli, ["flow", "image", "--prompts-file", str(prompts), "-o",
                                   str(folder), "--json"])
    assert res.exit_code == 2


def test_prompts_file_format_carries_multi_line_prompts(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text('﻿"one"\n   \n{"prompt": "two\\n\\nthree"}\n"  four  "\n',
                    encoding="utf-8")
    assert read_prompts(path) == ["one", "two\n\nthree", "four"]


@pytest.mark.parametrize("body, message", [
    ("", "no prompts"),
    ('"ok"\nnot json\n', "line 2"),
    ('"ok"\n42\n', "line 2"),
    ('{"text": "x"}\n', "line 1"),
    ('{"prompt": "x", "model": "y"}\n', "line 1"),
    ('{"prompt": "  "}\n', "line 1"),
    ('{"prompt": 5}\n', "line 1"),
])
def test_prompts_file_errors_name_the_line(tmp_path, body, message):
    path = tmp_path / "p.jsonl"
    path.write_text(body)
    with pytest.raises(ValidationError) as info:
        read_prompts(path)
    assert message in info.value.message
