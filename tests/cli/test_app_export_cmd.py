import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def test_app_export_command(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []

    async def fake(client, app_ref, dest, *, force=False, progress=None):
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


def test_app_export_shows_progress_only_for_people(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()

    async def fake(client, app_ref, dest, *, force=False, progress=None):
        for done in range(1, 13):
            if progress:
                progress(done, 12)
        return {"app_id": "a1", "title": "T", "dir": str(dest), "count": 12, "bytes": 1,
                "files": []}

    monkeypatch.setattr("aistudio.cli.app_cmd.export_app", fake)
    human = CliRunner().invoke(cli, ["app", "export", "a1", str(tmp_path / "o")])
    assert "Downloaded 10/12 files" in human.stderr and "Downloaded 12/12 files" in human.stderr
    assert "Downloaded 3/12" not in human.stderr
    js = CliRunner().invoke(cli, ["app", "export", "a1", str(tmp_path / "p"), "--json"])
    assert js.stderr == ""


def test_app_export_announces_and_keeps_reporting(logged_in_profile, monkeypatch, tmp_path):
    # Deferred minor #24: a small app on a slow link printed nothing for minutes.
    logged_in_profile()
    ticks = iter([0.0, 1.0, 7.0, 8.0, 20.0])
    monkeypatch.setattr("aistudio.cli.app_cmd._now", lambda: next(ticks))

    async def fake(client, app_ref, dest, *, force=False, progress=None):
        for done in range(0, 5):
            progress(done, 4)
        return {"app_id": "a1", "title": "T", "dir": str(dest), "count": 4, "bytes": 1,
                "files": []}

    monkeypatch.setattr("aistudio.cli.app_cmd.export_app", fake)
    res = CliRunner().invoke(cli, ["app", "export", "a1", str(tmp_path / "o")])
    lines = res.stderr.splitlines()
    assert lines == ["Downloading 4 files…", "Downloaded 2/4 files", "Downloaded 4/4 files"]


def test_app_export_counts_one_file(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()

    async def fake(client, app_ref, dest, *, force=False, progress=None):
        progress(0, 1)
        progress(1, 1)
        return {"app_id": "a1", "title": "T", "dir": str(dest), "count": 1, "bytes": 1,
                "files": []}

    monkeypatch.setattr("aistudio.cli.app_cmd.export_app", fake)
    res = CliRunner().invoke(cli, ["app", "export", "a1", str(tmp_path / "o")])
    assert res.stderr.splitlines() == ["Downloading 1 file…", "Downloaded 1/1 file"]
