import json

from click.testing import CliRunner

from aistudio.cli.main import cli

RESULT = {"app_id": "a1", "run_id": "r1", "url": "https://aistudio.google.com/apps/a1",
          "title": "Pomodoro [Focus] Timer", "status": "done", "steps": 12,
          "files": ["index.html", "src/App.tsx"], "seconds": 151}


def fake(result, calls):
    async def run(profile, *args, **kwargs):
        calls.append((profile.name, args, kwargs))
        return result

    return run


def test_app_create(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.app_cmd.create_app", fake(RESULT, calls))
    res = CliRunner().invoke(cli, ["app", "create", "a pomodoro timer", "--timeout", "300",
                                   "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == RESULT
    assert calls == [("default", ("a pomodoro timer",), {"timeout_s": 300, "visible": True})]


def test_app_prompt_human_output(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.app_cmd.prompt_app", fake(RESULT, calls))
    res = CliRunner().invoke(cli, ["app", "prompt", "a1", "make it blue"])
    assert res.exit_code == 0, res.output
    assert "Pomodoro [Focus] Timer" in res.stdout
    assert "https://aistudio.google.com/apps/a1" in res.stdout
    assert calls[0][1] == ("a1", "make it blue")


def test_timeout_prints_the_result_and_exits_2(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.app_cmd.create_app",
                        fake({**RESULT, "status": "timeout", "steps": None, "files": []}, []))
    res = CliRunner().invoke(cli, ["app", "create", "x", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["status"] == "timeout"


def test_http_backend_cannot_build(logged_in_profile):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["--backend", "http", "app", "create", "x", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_app_group_help_is_not_read_only():
    res = CliRunner().invoke(cli, ["--help"])
    assert "read-only" not in res.stdout
