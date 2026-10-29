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
        "project": "p1", "out": out, "overwrite": True, "timeout_s": 300.0, "visible": True,
        "start_frame": None, "end_frame": None, "ingredients": [], "i_have_rights": False,
        "characters": [], "on_start": None})


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


def test_flow_lists_and_download(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = {}

    def record(name, result):
        async def run(profile, *args, **kwargs):
            calls[name] = (args, kwargs)
            return result
        return run

    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_projects",
                        record("projects", {"projects": [{"id": "p1", "title": "aistudio",
                                                          "created": None}], "count": 1}))
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_media",
                        record("media", {"media": [], "count": 0, "project": "p1"}))
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_download",
                        record("download", {"files": [{"path": "x.mp4", "mime_type": "video/mp4",
                                                       "bytes": 9, "media_id": "m1"}],
                                            "project": "p1"}))
    assert CliRunner().invoke(cli, ["flow", "projects", "--json"]).exit_code == 0
    res = CliRunner().invoke(cli, ["flow", "media", "--project", "p1", "--type", "video", "--json"])
    assert res.exit_code == 0 and calls["media"][1]["kind"] == "video"
    res = CliRunner().invoke(cli, ["flow", "download", "m1", "-o", str(tmp_path / "x.mp4"),
                                   "--json"])
    assert res.exit_code == 0 and calls["download"][0] == ("m1",)


def test_flow_video_attachments_and_rights(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(QUOTED, calls))
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--start-frame", "a.jpg",
                                   "--end-frame", "m2", "--ingredient", "m1", "--ingredient",
                                   "b.png", "--i-have-rights", "--json"])
    assert res.exit_code == 0, res.output
    kwargs = calls[0][1]
    assert (kwargs["start_frame"], kwargs["end_frame"], kwargs["ingredients"],
            kwargs["i_have_rights"]) == ("a.jpg", "m2", ["m1", "b.png"], True)


def test_flow_character_and_extend(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_character",
                        fake({"status": "done", "character_id": "c1", "files": [],
                              "credits_spent": 0, "credits_left": 1020, "model": "Nano Banana 2.1",
                              "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "character", "create", "Robot", "a robot", "--json"])
    assert res.exit_code == 0 and calls[-1][0] == ("Robot", "a robot")
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_extend",
                        fake({"status": "timeout", "media_ids": ["m2"], "credits_spent": 10,
                              "project_url": "u"}, calls))
    res = CliRunner().invoke(cli, ["flow", "extend", "m1", "rolls off", "--max-credits", "10",
                                   "--json"])
    assert res.exit_code == 2 and calls[-1][0] == ("m1", "rolls off")
    assert calls[-1][1]["max_credits"] == 10


def test_flow_image_has_no_frames(logged_in_profile):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["flow", "image", "x", "--start-frame", "a.jpg"])
    assert res.exit_code == 2  # no such option on images


def test_unknown_credits_say_unknown(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate",
                        fake({**DONE, "credits_left": None}, []))
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--max-credits", "10"])
    assert "credits left unknown" in res.stdout and "None" not in res.stdout


def test_character_create_passes_name_photo_and_rights(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_character",
                        fake({"status": "done", "character_id": "c7", "name": "Apple",
                              "files": [], "credits_spent": 0, "credits_left": 1010,
                              "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "character", "create", "Apple", "--photo", "m3",
                                   "--i-have-rights", "--json"])
    assert res.exit_code == 0, res.output
    assert calls[-1] == (("Apple", None), {
        "photo": "m3", "i_have_rights": True, "personality": None, "model": None,
        "max_credits": None,
        "project": None, "out": None, "overwrite": False, "visible": False})
    res = CliRunner().invoke(cli, ["flow", "character", "create", "Apple", "--photo", "m3"])
    assert res.exit_code == 0 and "c7" in res.stdout and "Apple" in res.stdout


def test_character_list_and_rename(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_characters", fake(
        {"characters": [{"id": "c1", "name": "Paper boat", "media_id": "i1", "created": None},
                        {"id": "c4", "name": "Sketch", "media_id": None, "created": None},
                        {"id": "c5", "name": "", "media_id": "i5", "created": None}],
         "count": 3, "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "character", "list"])
    assert res.exit_code == 0, res.output
    assert "c1  Paper boat" in res.stdout and "Sketch  (no image)" in res.stdout
    assert "c5  (unnamed)" in res.stdout
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_rename_character",
                        fake({"character_id": "c1", "name": "Boat", "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "character", "rename", "c1", "Boat", "--project", "p2"])
    assert res.exit_code == 0 and calls[-1][0] == ("c1", "Boat")
    assert calls[-1][1]["project"] == "p2" and "Boat" in res.stdout


STITCHED = {"status": "done", "files": [{"path": "s.mp4", "mime_type": "video/mp4", "bytes": 9}],
            "clips": [], "project": "p"}


def test_scene_commands(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_stitch", fake(STITCHED, calls))
    out = tmp_path / "s.mp4"
    res = CliRunner().invoke(cli, ["flow", "scene", "stitch", "m1", "m2@2-5", "-o", str(out),
                                   "--timeout", "120", "--json"])
    assert res.exit_code == 0, res.output
    assert calls[-1] == ((["m1", "m2@2-5"],), {"project": None, "out": out, "overwrite": False,
                                               "timeout_s": 120.0, "visible": False})
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_scene_download",
                        fake({**STITCHED, "scene_id": "s1"}, calls))
    res = CliRunner().invoke(cli, ["flow", "scene", "download", "s1"])
    assert res.exit_code == 0 and calls[-1][0] == ("s1",) and "Wrote s.mp4" in res.stdout
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_scenes", fake(
        {"scenes": [{"id": "s1", "title": "Untitled Scene", "created": None,
                     "clips": [{"media_id": "m1", "title": "a", "seconds": 8}]}],
         "count": 1, "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "scene", "list"])
    assert res.exit_code == 0 and "s1  Untitled Scene  1 clip(s), 8 s" in res.stdout


def test_video_passes_characters(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(QUOTED, calls))
    res = CliRunner().invoke(cli, ["flow", "image", "x", "--character", "Paper boat",
                                   "--character", "c2", "--json"])
    assert res.exit_code == 0, res.output
    assert calls[-1][1]["characters"] == ["Paper boat", "c2"]


def test_flow_notice_comes_after_validation(logged_in_profile, monkeypatch):
    # Plan C review M5: no "Asking Flow…" for a run that fails its checks.
    from aistudio.exceptions import ValidationError

    logged_in_profile()

    async def refuse(profile, *args, **kwargs):
        raise ValidationError("bad duration")

    async def start(profile, *args, **kwargs):
        kwargs["on_start"]()
        return QUOTED

    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", refuse)
    res = CliRunner().invoke(cli, ["flow", "video", "x", "--duration", "99"])
    assert res.exit_code == 1 and "Asking Flow" not in res.stderr
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", start)
    res = CliRunner().invoke(cli, ["flow", "video", "x"])
    assert res.exit_code == 0 and "Asking Flow" in res.stderr



def test_long_list_lines_do_not_wrap(logged_in_profile, monkeypatch):
    logged_in_profile()
    title = "A very long scene title " * 5
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_scenes", fake(
        {"scenes": [{"id": "s1", "title": title.strip(), "created": None,
                     "clips": [{"media_id": "m1", "title": "a", "seconds": 8}]}],
         "count": 1, "project": "p"}, []))
    res = CliRunner().invoke(cli, ["flow", "scene", "list"])
    assert f"s1  {title.strip()}  1 clip(s), 8 s" in res.stdout


def test_an_unverified_charge_says_unknown(logged_in_profile, monkeypatch):
    logged_in_profile()
    free = {"status": "done", "files": [], "credits_spent": None, "credits_left": None,
            "model": "nano-banana-2.1", "project": "p",
            "warnings": ["Flow generated without asking (free model), so there was no quote"]}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(free, []))
    res = CliRunner().invoke(cli, ["flow", "image", "x"])
    assert "Spent None" not in res.stdout and "credits spent unknown" in res.stdout



def test_personality_options(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_set_personality",
                        fake({"character_id": "c1", "personality": "Brave", "project": "p"},
                             calls))
    res = CliRunner().invoke(cli, ["flow", "character", "personality", "c1", "Brave"])
    assert res.exit_code == 0 and calls[-1][0] == ("c1", "Brave") and "Brave" in res.stdout
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_character",
                        fake({"status": "done", "character_id": "c7", "name": "A", "files": [],
                              "credits_spent": 0, "credits_left": 1, "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "character", "create", "A", "a robot",
                                   "--personality", "Calm"])
    assert res.exit_code == 0 and calls[-1][1]["personality"] == "Calm"



def test_character_body_command(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_character_body",
                        fake({"status": "done", "character_id": "c1", "files": [],
                              "credits_spent": 0, "credits_left": 5, "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "character", "body", "c1", "full body", "--json"])
    assert res.exit_code == 0 and calls[-1][0] == ("c1", "full body")



def test_edit_command(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_edit",
                        fake({"status": "done", "files": [], "credits_spent": 20,
                              "credits_left": 980, "project": "p"}, calls))
    res = CliRunner().invoke(cli, ["flow", "edit", "m1", "make it black and white",
                                   "--max-credits", "20", "--timeout", "300", "--json"])
    assert res.exit_code == 0 and calls[-1][0] == ("m1", "make it black and white")
    assert calls[-1][1]["max_credits"] == 20 and calls[-1][1]["timeout_s"] == 300.0



def test_video_waits_longer_by_default(logged_in_profile, monkeypatch):
    # Live: a queued Veo 3.1 Fast clip took more than 10 minutes.
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_generate", fake(QUOTED, calls))
    CliRunner().invoke(cli, ["flow", "video", "x", "--json"])
    CliRunner().invoke(cli, ["flow", "image", "x", "--json"])
    assert [c[1]["timeout_s"] for c in calls] == [1200.0, 600.0]



def test_edit_and_extend_wait_longer_by_default(logged_in_profile, monkeypatch):
    # Plan I review M7: they are paid video too, and Flow can queue them.
    logged_in_profile()
    calls = []
    done = {"status": "done", "files": [], "credits_spent": 10, "credits_left": 1, "project": "p"}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_edit", fake(done, calls))
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_extend", fake(done, calls))
    CliRunner().invoke(cli, ["flow", "edit", "m1", "x", "--max-credits", "20", "--json"])
    CliRunner().invoke(cli, ["flow", "extend", "m1", "x", "--max-credits", "10", "--json"])
    assert [c[1]["timeout_s"] for c in calls] == [1200.0, 1200.0]


def test_character_output_without_an_id(logged_in_profile, monkeypatch):
    # Audit: "Created character None (Untitled character)".
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_character",
                        fake({"status": "done", "character_id": None,
                              "name": "Untitled character", "files": [], "credits_spent": 0,
                              "credits_left": 1020, "project": "p"}, []))
    res = CliRunner().invoke(cli, ["flow", "character", "create", "Robot", "a robot"])
    assert res.exit_code == 0, res.output
    assert "None" not in res.stdout and "id not reported" in res.stdout


def test_flow_frame_save_passes_every_option(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    saved = {"status": "done", "media_id": "f1", "source": "m1", "at": 3.5,
             "timecode": "00:03:12", "project": "p", "credits_spent": 0, "credits_left": 1020}
    monkeypatch.setattr("aistudio.cli.flow_cmd.flow_frame", fake(saved, calls))
    out = tmp_path / "f.png"
    res = CliRunner().invoke(cli, ["flow", "frame", "save", "m1", "--at", "3.5", "-o", str(out),
                                   "--project", "p1", "--force", "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == saved
    assert calls[-1] == (("m1",), {"at": 3.5, "project": "p1", "out": out, "overwrite": True,
                                   "visible": True})
    res = CliRunner().invoke(cli, ["flow", "frame", "save", "m1"])  # the last frame by default
    assert res.exit_code == 0, res.output
    assert calls[-1][1]["at"] is None
    assert "f1" in res.stdout and "00:03:12" in res.stdout and "--start-frame" in res.stdout
