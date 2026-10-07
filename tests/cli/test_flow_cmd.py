import json

from click.testing import CliRunner

from aistudio.cli.main import cli

DONE = {"status": "done", "files": [{"path": "a.mp4", "mime_type": "video/mp4", "bytes": 9,
                                     "media_id": "m1"}],
        "credits_spent": 10, "credits_left": 1040, "model": "veo-3.1-lite", "project": "p"}
QUOTED = {"status": "quoted", "credits": 10, "count": 1, "plan": "… Veo 3.1 - Lite …",
          "project": "p"}


def fake(result, calls):
    async def run(profile, *args, **kwargs):
        calls.append((args, kwargs))
        return result

    return run


def test_flow_video_passes_every_option(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(DONE, calls))
    out = tmp_path / "a.mp4"
    res = CliRunner().invoke(cli, ["flow", "video", "an apple", "-m", "veo-3.1-lite",
                                   "--aspect", "9:16", "--count", "1", "--duration", "8",
                                   "--max-credits", "10", "--project", "p1", "-o", str(out),
                                   "--force", "--timeout", "300", "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == DONE
    assert calls[0] == (("video", "an apple"), {
        "model": "veo-3.1-lite", "aspect": "9:16", "count": 1, "duration": 8, "max_credits": 10,
        "project": "p1", "out": out, "overwrite": True, "timeout_s": 300.0, "visible": True})


def test_a_quote_tells_people_how_to_spend(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(QUOTED, []))
    res = CliRunner().invoke(cli, ["flow", "image", "an apple"])
    assert res.exit_code == 0, res.output
    assert "Flow quotes 10 credits" in res.stdout and "--max-credits 10" in res.stdout
    assert "Nothing was spent" in res.stdout


def test_a_timeout_exits_2(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate",
                        fake({"status": "timeout", "media_ids": ["m1"], "credits_spent": 10,
                              "project_url": "u"}, []))
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--max-credits", "10", "--json"])
    assert res.exit_code == 2


def test_image_has_no_duration_and_credits_works(logged_in_profile, monkeypatch):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["flow", "image", "x", "--duration", "8"])
    assert res.exit_code == 2  # click usage error: no such option
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_credits", fake({"credits": 1040}, []))
    res = CliRunner().invoke(cli, ["flow", "credits", "--json"])
    assert json.loads(res.stdout) == {"credits": 1040}
