import json

from click.testing import CliRunner

from aistudio.cli.main import cli

CHANGE = {"path": "src/App.tsx", "status": "changed", "binary": False, "added": 1, "removed": 1,
          "diff": "--- a/src/App.tsx\n+++ b/src/App.tsx\n@@ -1 +1 @@\n-one [x]\n+two\n"}
GONE = {"path": "old.txt", "status": "removed", "binary": False, "added": 0, "removed": 1,
        "diff": "--- a/old.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"}
BIN = {"path": "logo.png", "status": "added", "binary": True, "added": 0, "removed": 0, "diff": ""}


def turn_result(files, **extra):
    return {
        "app_id": "a1", "title": "Quotes [v2]", "mode": "turn", "turn": 2, "turns": 3,
        "prompt": "add a file", "saved": "2026-10-06T10:13:26Z", "against": None,
        "summary": {"added": sum(f["status"] == "added" for f in files),
                    "removed": sum(f["status"] == "removed" for f in files),
                    "changed": sum(f["status"] == "changed" for f in files),
                    "lines_added": sum(f["added"] for f in files),
                    "lines_removed": sum(f["removed"] for f in files)},
        "files": files, **extra,
    }


def run(logged_in_profile, monkeypatch, result, args):
    logged_in_profile()
    calls = []

    async def fake(client, app_ref, *, turn=None, against=None):
        calls.append((app_ref, turn, against))
        return result

    monkeypatch.setattr("aistudio.cli.app_cmd.diff_app", fake)
    return CliRunner().invoke(cli, ["app", "diff", *args]), calls


def test_diff_prints_a_summary_and_each_unified_diff(logged_in_profile, monkeypatch):
    res, calls = run(logged_in_profile, monkeypatch, turn_result([CHANGE, GONE, BIN]), ["a1"])
    assert res.exit_code == 0, res.output
    assert calls == [("a1", None, None)]
    out = res.stdout
    assert 'Quotes [v2]  [a1]' in out and 'Turn 2 of 3' in out and "add a file" in out
    assert "3 files changed: 1 added, 1 removed, 1 changed; +1 -2 lines" in out
    assert "-one [x]" in out and "+two" in out and "@@ -1 +1 @@" in out
    assert "src/App.tsx (changed, +1 -1)" in out
    assert "logo.png (added): binary file, no line diff" in out
    assert out.index("src/App.tsx") < out.index("old.txt") < out.index("logo.png")


def test_diff_passes_turn_and_json_through(logged_in_profile, monkeypatch):
    result = turn_result([CHANGE])
    res, calls = run(logged_in_profile, monkeypatch, result, ["a1", "--turn", "2", "--json"])
    assert calls == [("a1", 2, None)]
    assert json.loads(res.stdout) == result


def test_diff_against_a_folder(logged_in_profile, monkeypatch, tmp_path):
    result = turn_result([CHANGE], mode="folder", turn=None, turns=None, prompt=None, saved=None,
                         against=str(tmp_path))
    res, calls = run(logged_in_profile, monkeypatch, result,
                     ["a1", "--against", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert calls == [("a1", None, tmp_path)]
    assert f"compared with {tmp_path}" in res.stdout
    assert "turn 2" not in res.stdout


def test_diff_with_nothing_changed_says_so(logged_in_profile, monkeypatch):
    res, _ = run(logged_in_profile, monkeypatch, turn_result([]), ["a1"])
    assert res.exit_code == 0
    assert "No file changed" in res.stdout


def test_turn_must_be_positive_and_cannot_mix_with_against(logged_in_profile, monkeypatch,
                                                            tmp_path):
    res, calls = run(logged_in_profile, monkeypatch, turn_result([]), ["a1", "--turn", "0"])
    assert res.exit_code != 0 and calls == []
    res, calls = run(logged_in_profile, monkeypatch, turn_result([]),
                     ["a1", "--turn", "1", "--against", str(tmp_path)])
    assert res.exit_code != 0 and calls == []


def test_against_must_exist(logged_in_profile, monkeypatch, tmp_path):
    res, calls = run(logged_in_profile, monkeypatch, turn_result([]),
                     ["a1", "--against", str(tmp_path / "nope")])
    assert res.exit_code != 0 and calls == []
