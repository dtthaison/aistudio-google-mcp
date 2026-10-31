import json

from click.testing import CliRunner

from aistudio.cli.main import cli

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
TOOL = {"id": "aa11", "name": "Chart Maker", "description": "Image to chart",
        "version_id": "v1", "kind": "mine", "author": None, "uses": None, "tags": []}


def fake(result, calls):
    async def run(profile, *args, **kwargs):
        calls.append((args, kwargs))
        return result

    return run


def test_list_passes_its_filters(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    result = {"tools": [TOOL], "count": 1, "project": PID}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tools", fake(result, calls))
    res = CliRunner().invoke(cli, ["flow", "tool", "list", "--kind", "mine", "--search", "chart",
                                   "--project", PID, "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == result
    assert calls == [((), {"kind": "mine", "search": "chart", "project": PID, "visible": False})]


def test_list_prints_a_line_per_tool(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tools",
                        fake({"tools": [TOOL], "count": 1, "project": PID}, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "list"])
    assert res.exit_code == 0, res.output
    assert "aa11" in res.stdout and "Chart Maker" in res.stdout


def test_get_warns_when_a_tool_may_spend(logged_in_profile, monkeypatch):
    logged_in_profile()
    result = {**TOOL, "project": PID, "files": [{"path": "App.tsx", "mime_type": None, "bytes": 3}],
              "flow_calls": ["Flow.generate.image"], "may_spend_credits": True}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_get", fake(result, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "get", "aa11"])
    assert res.exit_code == 0, res.output
    assert "App.tsx" in res.stdout and "may spend Flow credits" in res.output


def test_export_passes_the_folder_and_force(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    result = {"id": "aa11", "name": "Chart Maker", "path": str(tmp_path), "written": 7,
              "project": PID}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_export", fake(result, calls))
    res = CliRunner().invoke(cli, ["flow", "tool", "export", "aa11", str(tmp_path), "--force",
                                   "--json"])
    assert res.exit_code == 0, res.output
    assert calls == [(("aa11", tmp_path), {"force": True, "allow_remix": False, "project": None,
                                         "visible": False})]


TID = "5c0e7a12-3b4d-4e6f-8a9b-0c1d2e3f4a5b"
BUILT = {"status": "done", "id": TID, "name": "Chart Maker", "version_id": "v9",
         "url": "https://flow.google.com/x", "project": PID, "seconds": 95,
         "files": [{"path": "App.tsx", "mime_type": None, "bytes": 3}],
         "flow_calls": ["Flow.download"], "may_spend_credits": False}


def test_create_reads_a_description_file(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_create", fake(BUILT, calls))
    spec = tmp_path / "spec.txt"
    spec.write_text("an image to chart maker", encoding="utf-8")
    res = CliRunner().invoke(cli, ["flow", "tool", "create", "--prompt-file", str(spec),
                                   "--timeout", "300", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == BUILT
    assert calls == [(("an image to chart maker",),
                      {"project": None, "timeout_s": 300.0, "visible": False})]


def test_create_prints_the_tool_and_its_link(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_create", fake(BUILT, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "create", "x"])
    assert res.exit_code == 0, res.output
    assert "Chart Maker" in res.stdout and "https://flow.google.com/x" in res.stdout
    assert "may spend" not in res.output


def test_create_warns_when_the_tool_may_spend(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_create",
                        fake({**BUILT, "may_spend_credits": True}, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "create", "x"])
    assert "may spend Flow credits" in res.output


def test_a_build_timeout_exits_2_with_the_link(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_create",
                        fake({"status": "timeout", "id": TID, "project": PID,
                              "url": "https://flow.google.com/x"}, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "create", "x"])
    assert res.exit_code == 2
    assert "https://flow.google.com/x" in res.stdout


def test_edit_passes_the_tool_and_the_change(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_edit", fake(BUILT, calls))
    res = CliRunner().invoke(cli, ["flow", "tool", "edit", "a02d", "add a title", "--json"])
    assert res.exit_code == 0, res.output
    assert calls == [(("a02d", "add a title"),
                      {"project": None, "timeout_s": 600.0, "visible": False})]


def test_get_passes_allow_remix(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    result = {**TOOL, "project": PID, "files": [], "flow_calls": [], "may_spend_credits": False,
              "warnings": ["Flow added Remix of Whisk to your tools"]}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_get", fake(result, calls))
    res = CliRunner().invoke(cli, ["flow", "tool", "get", "bb22", "--allow-remix"])
    assert res.exit_code == 0, res.output
    assert calls == [(("bb22",), {"allow_remix": True, "project": None, "visible": False})]
    assert "Remix of Whisk" in res.output


def test_export_passes_allow_remix(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    result = {"id": "bb22", "name": "Whisk", "path": str(tmp_path), "written": 7, "project": PID}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_export", fake(result, calls))
    res = CliRunner().invoke(cli, ["flow", "tool", "export", "bb22", str(tmp_path),
                                   "--allow-remix", "--json"])
    assert res.exit_code == 0, res.output
    assert calls[0][1]["allow_remix"] is True


USED = {"id": TID, "name": "Chart Maker", "project": PID, "url": "https://flow.google.com/x",
        "may_spend_credits": False,
        "steps": [{"step": "PNG", "control": 3, "label": "PNG", "kind": "button", "value": None}],
        "files": [{"path": "out/bear_chart.png", "bytes": 9,
                   "name": "bear_chart.png"}],
        "controls": [{"n": 1, "kind": "button", "label": "EN", "value": None, "min": None,
                      "max": None, "accept": None, "options": None, "disabled": False},
                     {"n": 2, "kind": "range", "label": "Chart Width (Stitches)", "value": "60",
                      "min": "20", "max": "200", "accept": None, "options": None,
                      "disabled": False}]
                    + [{"n": n, "kind": "button", "label": "Mark row as completed", "value": None,
                        "min": None, "max": None, "accept": None, "options": None,
                        "disabled": False} for n in range(3, 63)],
        "saved_to_flow": [], "text": "Chart Maker"}


def test_use_passes_the_steps_and_options(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_use", fake(USED, calls))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    res = CliRunner().invoke(cli, ["flow", "tool", "use", "a02d", "1=bear.png", "Chart Width=40",
                                   "PNG", "-o", str(tmp_path), "--allow-spend", "--force",
                                   "--step-timeout", "30", "--drop", str(clip), "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == USED
    assert calls == [(("a02d", ["1=bear.png", "Chart Width=40", "PNG"], tmp_path),
                      {"allow_remix": False, "allow_spend": True, "force": True, "project": None,
                       "visible": False, "step_timeout_s": 30.0, "drop": clip})]


def test_use_lists_controls_and_folds_repeats(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_use", fake({**USED, "steps": [],
                                                                     "files": []}, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "use", "a02d"])
    assert res.exit_code == 0, res.output
    assert "2  range" in res.stdout and "Chart Width (Stitches) = 60  [20-200]" in res.stdout
    assert "3-62  button  Mark row as completed  (x60)" in res.stdout
    assert res.stdout.count("Mark row as completed") == 1


def test_use_prints_saved_files(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_use", fake(USED, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "use", "a02d", "PNG"])
    assert res.exit_code == 0, res.output
    assert "Saved out/bear_chart.png (9 bytes)" in res.stdout


def test_use_prints_what_the_tool_saved_to_flow(logged_in_profile, monkeypatch):
    logged_in_profile()
    data = {**USED, "files": [], "saved_to_flow": [
        {"media_id": "m1", "kind": "video", "path": "out/flow-video.mp4", "bytes": 5}]}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_use", fake(data, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "use", "a02d", "PNG"])
    assert res.exit_code == 0, res.output
    assert "Saved out/flow-video.mp4 (5 bytes; video m1 in the Flow project)" in res.stdout
    assert "saved no files" not in res.stdout


def test_tool_help_says_when_to_build_one_and_the_steps():
    res = CliRunner().invoke(cli, ["flow", "tool", "--help"])
    assert res.exit_code == 0
    assert "Build a tool when it needs Flow itself" in res.output
    assert "aistudio flow tool use ID 1=value" in res.output


def test_create_help_gives_spec_tips_that_keep_the_tool_usable_from_the_cli():
    res = CliRunner().invoke(cli, ["flow", "tool", "create", "--help"])
    assert "text labels" in res.output and "Flow.download" in res.output


def test_use_prints_how_long_a_wait_step_took(logged_in_profile, monkeypatch):
    logged_in_profile()
    data = {**USED, "steps": [{"step": "@Make video", "wait": "control", "waited_s": 96.0},
                              *USED["steps"]]}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_tool_use", fake(data, []))
    res = CliRunner().invoke(cli, ["flow", "tool", "use", "a02d", "@Make video", "PNG"])
    assert res.exit_code == 0, res.output
    assert "@Make video: ready after 96 s" in res.stdout


def test_use_help_explains_wait_steps():
    res = CliRunner().invoke(cli, ["flow", "tool", "use", "--help"])
    assert "@LABEL" in res.output and "?TEXT" in res.output


def test_create_help_asks_for_a_ready_signal_the_cli_can_wait_on():
    res = CliRunner().invoke(cli, ["flow", "tool", "create", "--help"])
    assert "ready" in res.output and "@LABEL" in res.output
