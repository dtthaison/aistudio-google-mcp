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
