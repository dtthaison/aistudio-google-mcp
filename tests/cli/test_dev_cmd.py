import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def fake_capture(counts):
    async def run_capture(profile, recorder, *, browser):
        for method, n in counts.items():
            recorder.counts[method] += n

    return run_capture


def test_capture_json_summary(aistudio_home, monkeypatch, tmp_path):
    monkeypatch.setattr("aistudio.cli.dev_cmd.run_capture",
                        fake_capture({"ListModels": 2, "ListPrompts": 1}))
    out = tmp_path / "fx"
    result = CliRunner().invoke(cli, ["dev", "capture", "--out", str(out), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "out": str(out), "fixtures": 3, "by_method": {"ListModels": 2, "ListPrompts": 1},
    }


def test_capture_prints_checklist(aistudio_home, monkeypatch, tmp_path):
    monkeypatch.setattr("aistudio.cli.dev_cmd.run_capture", fake_capture({}))
    result = CliRunner().invoke(cli, ["dev", "capture", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "throwaway content" in result.stderr
    assert "model picker" in result.stderr
    assert "0 fixtures" in result.stdout


def test_dev_redact_command(aistudio_home, tmp_path):
    fixture = tmp_path / "GetPrompt" / "1.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(json.dumps({
        "method": "GetPrompt", "url": "https://x.google.com/$rpc/S/GetPrompt",
        "http_method": "POST",
        "request": {"headers": {}, "body": "[]"},
        "response": {"status": 200, "headers": {}, "body": '["Test Person"]'},
    }))
    result = CliRunner().invoke(
        cli, ["dev", "redact", str(tmp_path), "--redact", "Test Person", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"files": 1, "changed": 1}
    assert "Test Person" not in fixture.read_text()


def test_capture_passes_extra_redactions(aistudio_home, monkeypatch, tmp_path):
    seen = {}

    async def run_capture(profile, recorder, *, browser):
        seen["text"] = recorder._redactor.text("hello Test Person")

    monkeypatch.setattr("aistudio.cli.dev_cmd.run_capture", run_capture)
    result = CliRunner().invoke(
        cli, ["dev", "capture", "--out", str(tmp_path), "--redact", "Test Person", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert seen["text"] == "hello Redacted Name"
