import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def test_app_export_command(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []

    async def fake(client, app_ref, dest, *, force=False):
        calls.append((app_ref, dest, force))
        return {"app_id": "a1", "title": "Quotes [v2]", "dir": str(dest), "count": 2,
                "bytes": 30, "files": [{"path": "index.html", "bytes": 13},
                                       {"path": "src/App.tsx", "bytes": 17}]}

    monkeypatch.setattr("aistudio.cli.app_cmd.export_app", fake)
    dest = tmp_path / "out"
    res = CliRunner().invoke(cli, ["app", "export", "a1", str(dest), "--force"])
    assert res.exit_code == 0, res.output
    assert "Exported 2 files" in res.stdout and "Quotes [v2]" in res.stdout
    assert calls == [("a1", dest, True)]
    js = CliRunner().invoke(cli, ["app", "export", "a1", str(dest), "--json"])
    assert json.loads(js.stdout)["count"] == 2
