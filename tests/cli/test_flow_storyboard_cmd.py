import json
from pathlib import Path

from click.testing import CliRunner

from aistudio.cli.main import cli

SHOT = {"id": "s1", "join": "cut", "kind": "video", "mode": "start_frame",
        "model": "veo-3.1-lite", "duration": 8, "aspect": "16:9", "status": "pending",
        "steps": ["keyframe", "clip"], "quote": 10, "quote_basis": "flow", "keyframe": None,
        "clip": None, "file": None, "credits_spent": None}
QUOTED = {"status": "quoted", "storyboard": "scene.yaml", "state": "scene.state.json",
          "assets": "scene.assets", "project": "p", "credits_spent": 0,
          "credits_spent_total": 0, "credits_left": None,
          "quote": {"credits": 10, "pending_shots": 1, "basis": "b"}, "shots": [SHOT]}
DONE = {**QUOTED, "status": "done", "credits_spent": 10, "credits_spent_total": 10,
        "credits_left": 90, "files": [{"path": "scene.mp4"}],
        "shots": [{**SHOT, "status": "done", "quote": None, "quote_basis": None}]}
MEDIA = "11111111-2222-4333-8444-555555555555"


def fake(result, calls):
    async def run(profile, path, **kwargs):
        calls.append((path, kwargs))
        return result

    return run


def board(tmp_path) -> Path:
    path = tmp_path / "scene.yaml"
    path.write_text("shots: []\n")  # the use case is replaced: only the path matters
    return path


def test_storyboard_passes_every_option(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_storyboard", fake(DONE, calls))
    path = board(tmp_path)
    res = CliRunner().invoke(cli, [
        "flow", "storyboard", str(path), "--max-credits", "10", "--keyframes", "-o",
        str(tmp_path / "o.mp4"), "--force", "--state", str(tmp_path / "s.json"), "--assets",
        str(tmp_path / "a"), "--adopt", f"s2={MEDIA}", "--timeout", "300", "--visible",
        "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == DONE
    assert calls[0][0] == path
    assert calls[0][1] == {
        "max_credits": 10, "keyframes": True, "out": tmp_path / "o.mp4", "overwrite": True,
        "state_path": tmp_path / "s.json", "assets_dir": tmp_path / "a",
        "adopt": {"s2": MEDIA}, "timeout_s": 300.0, "visible": True, "on_progress": None}


def test_defaults_spend_nothing_and_a_quote_says_how_to_go_on(logged_in_profile, monkeypatch,
                                                              tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_storyboard", fake(QUOTED, calls))
    res = CliRunner().invoke(cli, ["flow", "storyboard", str(board(tmp_path))])
    assert res.exit_code == 0, res.output
    kwargs = calls[0][1]
    assert kwargs["max_credits"] is None and kwargs["keyframes"] is False
    assert kwargs["out"] is None and kwargs["adopt"] == {}
    assert "Nothing was spent" in res.stdout and "--max-credits 10" in res.stdout
    assert "s1" in res.stdout and "keyframe + clip" in res.stdout


def test_a_failed_run_exits_1_and_a_timeout_exits_2(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    failed = {**QUOTED, "status": "failed",
              "error": {"code": "GENERATION_FAILED", "message": "Flow failed it",
                        "hint": "nothing charged"}}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_storyboard", fake(failed, []))
    res = CliRunner().invoke(cli, ["flow", "storyboard", str(board(tmp_path)), "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "GENERATION_FAILED"
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_storyboard",
                        fake({**failed, "status": "timeout"}, []))
    res = CliRunner().invoke(cli, ["flow", "storyboard", str(board(tmp_path))])
    assert res.exit_code == 2
    assert "Flow failed it" in res.output


def test_a_bad_adopt_value_is_a_usage_error(logged_in_profile, tmp_path):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["flow", "storyboard", str(board(tmp_path)), "--adopt", "s2"])
    assert res.exit_code == 2 and "SHOT=MEDIA-ID" in res.output


def test_an_invalid_file_is_reported_with_every_problem(logged_in_profile, tmp_path):
    logged_in_profile()
    path = tmp_path / "bad.yaml"
    path.write_text("shots:\n  - prompt: ''\n  - join: extend\n    prompt: x\n    mode: x\n")
    res = CliRunner().invoke(cli, ["flow", "storyboard", str(path), "--json"])
    assert res.exit_code == 1
    message = json.loads(res.stdout)["message"]
    assert "problems" in message and message.count("\n- ") >= 2
