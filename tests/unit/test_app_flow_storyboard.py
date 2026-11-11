import json

import pytest
import yaml

from aistudio._app.flow_storyboard import StoryboardOps, flow_storyboard
from aistudio.exceptions import (
    GenerationFailedError,
    NotFoundError,
    OverBudgetError,
    ServerError,
    ValidationError,
)

PID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
MAI = "c0000000-0000-4000-8000-000000000001"


def uid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-000000000000"


STORYBOARD = {
    "style": "Soft light.",
    "characters": [{"name": "Mai", "id": MAI},
                   {"name": "Robo", "create": {"description": "a small robot"}}],
    "locations": [{"name": "gym", "prompt": "An empty gym"}],
    "shots": [
        {"id": "s1", "prompt": "Mai enters", "say": "Hello", "characters": ["Mai"],
         "location": "gym"},
        {"id": "s2", "join": "extend", "prompt": "She waves"},
        {"id": "s3", "prompt": "Robo outside", "mode": "start_frame",
         "keyframe": {"characters": ["Robo"]}, "trim": [1, 6]},
        {"id": "s4", "join": "frame", "frame_at": 2, "prompt": "Close on Robo",
         "mode": "start_frame"},
    ],
}


class Ops:
    """Scripted stand-ins for the flow use cases; every call is recorded."""

    def __init__(self, *, quote=10, fail=None, charged=None, existing=("Mai",)):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.n = 0
        self.quote, self.fail, self.charged = quote, fail or {}, charged or {}
        self.existing = existing
        self.media_ids = {uid(900): "image"}
        self.written: list[str] = []
        self.images: list[str] = []  # media ids of generated images, in order (plate, keyframes)
        self.clips: list[str] = []
        self.frames: list[str] = []

    def new_id(self) -> str:
        self.n += 1
        return uid(self.n)

    def names(self, kind):
        return [c[0] for c in self.calls if kind is None or c[0] == kind]

    def of(self, name):
        return [c for c in self.calls if c[0] == name]

    def write(self, out, data=b"x"):
        if out is not None:
            out.write_bytes(data)
            self.written.append(str(out))

    async def characters(self, profile, **kw):
        self.calls.append(("characters", (), kw))
        ids = {"Mai": MAI, "Robo": uid(700)}
        return {"characters": [{"id": ids[n], "name": n, "media_id": uid(800)}
                               for n in self.existing], "project": PID}

    async def media(self, profile, **kw):
        self.calls.append(("media", (), kw))
        return {"media": [{"media_id": m, "kind": k, "status": "done"}
                          for m, k in self.media_ids.items()], "project": PID}

    async def character(self, profile, name, description=None, **kw):
        self.calls.append(("character", (name, description), kw))
        self.maybe_fail("character")
        self.write(kw.get("out"))
        return {"status": "done", "character_id": self.new_id(), "name": name,
                "media_id": self.new_id(), "files": [], "credits_spent": 0, "project": PID}

    def maybe_fail(self, key):
        if key in self.fail:
            raise self.fail[key]

    async def generate(self, profile, kind, prompt, **kw):
        self.calls.append(("generate", (kind, prompt), kw))
        if kind == "video" and kw.get("max_credits") is None:
            return {"status": "quoted", "credits": self.quote, "count": 1, "plan": "plan",
                    "declined": True, "project": PID}
        key = "image" if kind == "image" else "video"
        self.maybe_fail(key)
        mid = self.new_id()
        self.write(kw.get("out"))
        if kind == "video" and self.fail.get("timeout"):
            return {"status": "timeout", "media_ids": [mid], "project_url": "https://x/p",
                    "credits_spent": 10}
        spent = self.charged.get(prompt, 0 if kind == "image" else self.quote)
        (self.images if kind == "image" else self.clips).append(mid)
        return {"status": "done",
                "files": [{"path": str(kw.get("out")), "mime_type": "x", "bytes": 1,
                           "media_id": mid}],
                "credits_spent": spent, "credits_left": 500, "project": PID}

    async def extend(self, profile, media_id, prompt, **kw):
        self.calls.append(("extend", (media_id, prompt), kw))
        self.maybe_fail("extend")
        mid = self.new_id()
        self.clips.append(mid)
        self.write(kw.get("out"))
        spent = (kw.get("duration") or 4) * 3
        return {"status": "done", "files": [{"path": str(kw["out"]), "mime_type": "x",
                                             "bytes": 1, "media_id": mid}],
                "credits_spent": spent, "credits_left": 500 - spent, "scene_id": "sc",
                "project": PID}

    async def frame(self, profile, media_id, **kw):
        self.calls.append(("frame", (media_id,), kw))
        self.write(kw.get("out"))
        self.frames.append(self.new_id())
        return {"status": "done", "media_id": self.frames[-1], "source": media_id,
                "credits_spent": 0, "project": PID}

    async def stitch(self, profile, clips, **kw):
        self.calls.append(("stitch", (clips,), kw))
        self.write(kw.get("out"), b"joined")
        return {"status": "done", "files": [{"path": str(kw["out"]), "mime_type": "video/mp4",
                                             "bytes": 6}], "clips": [], "project": PID}

    async def download(self, profile, media_id, **kw):
        self.calls.append(("download", (media_id,), kw))
        self.write(kw.get("out"), b"one")
        return {"files": [{"path": str(kw["out"]), "mime_type": "video/mp4", "bytes": 3,
                           "media_id": media_id}], "project": PID}

    def bundle(self):
        return StoryboardOps(characters=self.characters, media=self.media,
                             character=self.character, generate=self.generate,
                             extend=self.extend, frame=self.frame, stitch=self.stitch,
                             download=self.download)


def write_board(tmp_path, data=None, name="scene.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data or STORYBOARD))
    return path


async def run(profile, path, ops, **kw):
    return await flow_storyboard(profile, path, ops=ops.bundle(), **kw)


@pytest.fixture
def profile(logged_in_profile):
    return logged_in_profile()


# ----- without --max-credits: nothing is generated, everything is quoted ---------------------

async def test_without_a_budget_it_quotes_and_makes_nothing(profile, tmp_path):
    ops = Ops()
    result = await run(profile, write_board(tmp_path), ops)
    assert result["status"] == "quoted"
    assert result["credits_spent"] == 0
    assert ops.of("character") == [] and ops.of("extend") == [] and ops.of("frame") == []
    assert [c for c in ops.of("generate") if c[1][0] == "image"] == []
    quotes = [c for c in ops.of("generate") if c[1][0] == "video"]
    assert len(quotes) == 1  # s1, s3 and s4 are Lite, 8 s, 16:9: one quote for the three
    assert quotes[0][2]["max_credits"] is None and quotes[0][2]["model"] == "veo-3.1-lite"
    assert quotes[0][2]["duration"] == 8 and quotes[0][2]["aspect"] == "16:9"
    assert quotes[0][2].get("characters") is None  # the quote is for the model, not the files
    assert result["quote"] == {"credits": 42, "pending_shots": 4, "basis":
                               "Flow's quote per model, length and aspect; extensions from "
                               "aistudio's per-second Extend price"}
    by_id = {s["id"]: s for s in result["shots"]}
    assert [by_id[i]["quote"] for i in ("s1", "s2", "s3", "s4")] == [10, 12, 10, 10]
    assert by_id["s2"]["quote_basis"] == "table" and by_id["s1"]["quote_basis"] == "flow"
    assert by_id["s1"]["steps"] == ["keyframe", "clip"]
    assert by_id["s2"]["steps"] == ["extend"] and by_id["s4"]["steps"] == ["frame", "clip"]
    assert all(s["status"] == "pending" for s in result["shots"])
    assert result["warnings"] == [w for w in result["warnings"] if isinstance(w, str)]


async def test_existing_characters_are_checked_before_anything_else(profile, tmp_path):
    ops = Ops(existing=())
    with pytest.raises(NotFoundError) as caught:
        await run(profile, write_board(tmp_path), ops)
    assert "Mai" in caught.value.message
    assert ops.of("generate") == []


async def test_a_created_character_may_not_already_exist(profile, tmp_path):
    ops = Ops(existing=("Mai", "Robo"))
    with pytest.raises(ValidationError) as caught:
        await run(profile, write_board(tmp_path), ops)
    assert "Robo" in caught.value.message and "already" in caught.value.message
    assert ops.of("generate") == []


async def test_a_bad_file_fails_before_a_browser_opens(profile, tmp_path):
    data = json.loads(json.dumps(STORYBOARD))
    data["shots"][0]["mode"] = "start_frame"
    ops = Ops()
    with pytest.raises(ValidationError):
        await run(profile, write_board(tmp_path, data), ops)
    assert ops.calls == []


async def test_referenced_media_ids_must_exist_and_are_all_reported(profile, tmp_path):
    data = json.loads(json.dumps(STORYBOARD))
    data["locations"].append({"name": "street", "media": uid(901)})
    data["shots"][0]["location"] = "street"
    data["shots"][2]["keyframe"] = {"media": uid(902)}
    ops = Ops()
    with pytest.raises(NotFoundError) as caught:
        await run(profile, write_board(tmp_path, data), ops)
    assert uid(901) in caught.value.message and uid(902) in caught.value.message
    assert ops.of("generate") == []


async def test_keyframes_stage_makes_the_free_assets_and_still_spends_nothing(profile, tmp_path):
    ops = Ops()
    path = write_board(tmp_path)
    result = await run(profile, path, ops, keyframes=True)
    assert result["status"] == "quoted" and result["credits_spent"] == 0
    assert ops.of("character")[0][1] == ("Robo", "a small robot")
    assert ops.of("character")[0][2]["max_credits"] is None
    images = [c for c in ops.of("generate") if c[1][0] == "image"]
    assert [c[1][1] for c in images] == [
        "An empty gym. Soft light.",                      # the plate: style only
        "Mai enters. Soft light.",                         # s1 keyframe
        "Robo outside. Soft light."]                       # s3 keyframe (s4 is a saved frame)
    plate, kf1, kf3 = (c[2] for c in images)
    assert plate["model"] == "nano-banana-2.1" and plate["aspect"] == "16:9"
    assert plate["characters"] == [] and plate["ingredients"] == []
    assert kf1["characters"] == [MAI] and kf1["ingredients"] == [uid(3)]  # the plate's media id
    robo_id = ops.of("character")[0][2]  # created first: its id is the first generated one
    assert kf3["characters"] == [uid(1)] and robo_id["out"].name.startswith("character-robo")
    assert kf1["out"].parent == tmp_path / "scene.assets"
    assert all(c[2]["max_credits"] is None for c in images)
    assert ops.of("extend") == [] and ops.of("frame") == []
    state = json.loads((tmp_path / "scene.state.json").read_text())
    assert state["project"] == PID and state["credits_spent"] == 0
    assert state["shots"]["s1"]["keyframe"]["media_id"]
    by_id = {s["id"]: s for s in result["shots"]}
    assert by_id["s1"]["keyframe"] and by_id["s4"]["keyframe"] is None
    # a second run reuses every asset: no second character, plate or keyframe
    ops2 = Ops()
    await run(profile, path, ops2, keyframes=True)
    assert ops2.of("character") == []
    assert [c for c in ops2.of("generate") if c[1][0] == "image"] == []


async def test_changing_a_keyframe_prompt_regenerates_only_that_keyframe(profile, tmp_path):
    ops = Ops()
    path = write_board(tmp_path)
    await run(profile, path, ops, keyframes=True)
    data = json.loads(json.dumps(STORYBOARD))
    data["shots"][2]["keyframe"] = {"characters": ["Robo"], "prompt": "Robo, wide shot"}
    write_board(tmp_path, data)
    ops2 = Ops()
    await run(profile, path, ops2, keyframes=True)
    images = [c for c in ops2.of("generate") if c[1][0] == "image"]
    assert [c[1][1] for c in images] == ["Robo, wide shot. Soft light."]


# ----- with --max-credits ---------------------------------------------------------------------

async def test_a_budget_below_the_total_quote_refuses_before_anything_is_made(profile, tmp_path):
    ops = Ops()
    with pytest.raises(OverBudgetError) as caught:
        await run(profile, write_board(tmp_path), ops, max_credits=41, out=tmp_path / "o.mp4")
    assert "42" in caught.value.message and "--max-credits 42" in (caught.value.hint or "")
    assert ops.of("character") == [] and ops.of("extend") == []
    assert [c for c in ops.of("generate") if c[1][0] == "image"] == []
    assert not (tmp_path / "scene.assets").exists()


async def test_a_budget_needs_an_output_file(profile, tmp_path):
    ops = Ops()
    with pytest.raises(ValidationError) as caught:
        await run(profile, write_board(tmp_path), ops, max_credits=42)
    assert "-o" in caught.value.message and ops.calls == []


async def test_a_full_run_makes_the_clips_in_order_and_joins_them(profile, tmp_path):
    ops = Ops()
    out = tmp_path / "scene.mp4"
    result = await run(profile, write_board(tmp_path), ops, max_credits=42, out=out)
    assert result["status"] == "done" and result["credits_spent"] == 42
    assert result["files"][0]["path"] == str(out) and out.read_bytes() == b"joined"
    videos = [c for c in ops.of("generate") if c[1][0] == "video" and c[2]["max_credits"]]
    s1, s3, s4 = (c[2] for c in videos)
    assert [c[1][1] for c in videos] == [
        'Mai enters. Mai says: "Hello". Soft light.',
        "Robo outside. Soft light.", "Close on Robo. Soft light."]
    # s1: the character (Flow attaches its voice) with the keyframe as the only ingredient
    keyframe_ids = {c[1][1]: c for c in ops.of("generate") if c[1][0] == "image"}
    assert s1["characters"] == [MAI] and s1["ingredients"] == [ops.images[1]]
    assert s1["start_frame"] is None and s1["max_credits"] == 10
    assert s1["model"] == "veo-3.1-lite" and s1["duration"] == 8 and s1["aspect"] == "16:9"
    assert keyframe_ids["Mai enters. Soft light."][2]["out"].name == "keyframe-s1.jpg"
    # s3: a start frame, no character
    assert s3["start_frame"] == ops.images[2] and s3["characters"] == []
    assert s3["ingredients"] == []
    # s4: the frame saved from s3's clip at 2 s, used as the start frame
    frame = ops.of("frame")[0]
    assert frame[1] == (ops.clips[2],) and frame[2]["at"] == 2.0
    assert s4["start_frame"] == ops.frames[0]
    extend = ops.of("extend")[0]
    assert extend[1] == (ops.clips[0], "She waves. Soft light.")
    assert extend[2]["max_credits"] == 12 and extend[2]["duration"] == 4
    steps = [c[0] for c in ops.calls if c[0] in ("extend", "frame", "stitch")]
    assert steps == ["extend", "frame", "stitch"]
    stitch = ops.of("stitch")[0]
    assert stitch[1][0] == [ops.clips[0], ops.clips[1], f"{ops.clips[2]}@1-6", ops.clips[3]]
    assert stitch[2]["out"] == out and ops.of("download") == []
    assert all(s["status"] == "done" for s in result["shots"])
    state = json.loads((tmp_path / "scene.state.json").read_text())
    assert state["credits_spent"] == 42 and state["final"]["path"] == str(out)


async def test_every_call_after_the_first_uses_the_project_the_first_one_reported(
        profile, tmp_path):
    ops = Ops()
    await run(profile, write_board(tmp_path), ops, max_credits=42, out=tmp_path / "o.mp4")
    projects = [c[2].get("project") for c in ops.calls]
    assert projects[0] is None and set(projects[1:]) == {PID}


async def test_a_project_in_the_file_is_used_from_the_start(profile, tmp_path):
    data = json.loads(json.dumps(STORYBOARD))
    data["project"] = PID
    ops = Ops()
    await run(profile, write_board(tmp_path, data), ops)
    assert {c[2].get("project") for c in ops.calls} == {PID}


async def test_a_single_clip_is_downloaded_not_stitched(profile, tmp_path):
    data = {"characters": [], "shots": [{"id": "only", "prompt": "An apple rolls"}]}
    ops = Ops()
    out = tmp_path / "one.mp4"
    result = await run(profile, write_board(tmp_path, data), ops, max_credits=10, out=out)
    assert result["status"] == "done"
    assert ops.of("stitch") == [] and len(ops.of("download")) == 1
    assert ops.of("generate")[-1][2]["start_frame"]  # a start frame from its keyframe


async def test_a_failed_clip_stops_the_run_and_a_rerun_continues_after_the_finished_ones(
        profile, tmp_path):
    path = write_board(tmp_path)
    out = tmp_path / "o.mp4"
    ops = Ops(fail={"extend": GenerationFailedError("Flow failed it", hint="nothing charged")})
    result = await run(profile, path, ops, max_credits=42, out=out)
    assert result["status"] == "failed" and result["error"]["code"] == "GENERATION_FAILED"
    assert result["credits_spent"] == 10  # s1 only
    by_id = {s["id"]: s for s in result["shots"]}
    assert by_id["s1"]["status"] == "done" and by_id["s2"]["status"] == "failed"
    assert by_id["s3"]["status"] == "pending"
    assert len(ops.of("extend")) == 1  # not retried
    assert ops.of("stitch") == []
    # the user runs it again: s1 is not paid for twice, the rest goes through
    ops2 = Ops()
    result2 = await run(profile, path, ops2, max_credits=32, out=out)
    assert result2["status"] == "done" and result2["credits_spent"] == 32
    assert result2["credits_spent_total"] == 42
    clips = [c for c in ops2.of("generate") if c[1][0] == "video" and c[2]["max_credits"]]
    assert [c[1][1] for c in clips] == ["Robo outside. Soft light.",
                                        "Close on Robo. Soft light."]
    assert len(ops2.of("extend")) == 1


async def test_a_rerun_budget_covers_only_what_is_left(profile, tmp_path):
    path = write_board(tmp_path)
    out = tmp_path / "o.mp4"
    ops = Ops(fail={"extend": GenerationFailedError("x")})
    await run(profile, path, ops, max_credits=42, out=out)
    with pytest.raises(OverBudgetError) as caught:
        await run(profile, path, Ops(), max_credits=31, out=out)
    assert "32" in caught.value.message


async def test_an_error_that_may_have_charged_blocks_the_shot_until_it_is_adopted(
        profile, tmp_path):
    path = write_board(tmp_path)
    out = tmp_path / "o.mp4"
    ops = Ops(fail={"extend": ServerError("boom")})
    result = await run(profile, path, ops, max_credits=42, out=out)
    assert result["status"] == "failed"
    by_id = {s["id"]: s for s in result["shots"]}
    assert by_id["s2"]["status"] == "unknown"
    ops2 = Ops()
    with pytest.raises(ValidationError) as caught:
        await run(profile, path, ops2, max_credits=42, out=out)
    assert "s2" in caught.value.message and "--adopt" in (caught.value.hint or "")
    assert ops2.of("extend") == [] and [c for c in ops2.calls if c[0] == "generate"
                                        and c[2].get("max_credits")] == []
    ops3 = Ops()
    ops3.media_ids[uid(950)] = "video"
    result3 = await run(profile, path, ops3, max_credits=32, out=out,
                        adopt={"s2": uid(950)})
    assert result3["status"] == "done"
    assert ops3.of("extend") == []
    frame_source = ops3.of("frame")[0][1][0]
    assert frame_source  # s4 saved its frame from s3's clip
    stitch = ops3.of("stitch")[0][1][0]
    assert stitch[1] == uid(950)


async def test_a_timeout_blocks_the_shot_and_exits_with_the_job_named(profile, tmp_path):
    path = write_board(tmp_path)
    ops = Ops(fail={"timeout": True})
    result = await run(profile, path, ops, max_credits=42, out=tmp_path / "o.mp4")
    assert result["status"] == "timeout" and result["error"]["code"] == "TIMEOUT"
    s1 = result["shots"][0]
    assert s1["status"] == "timeout" and s1["media_ids"]
    assert len([c for c in ops.of("generate") if c[2].get("max_credits")]) == 1


async def test_a_finished_shot_that_was_edited_is_refused_not_paid_for_again(profile, tmp_path):
    path = write_board(tmp_path)
    out = tmp_path / "o.mp4"
    ops = Ops(fail={"extend": GenerationFailedError("x")})
    await run(profile, path, ops, max_credits=42, out=out)
    data = json.loads(json.dumps(STORYBOARD))
    data["shots"][0]["prompt"] = "Mai leaves"
    write_board(tmp_path, data)
    ops2 = Ops()
    with pytest.raises(ValidationError) as caught:
        await run(profile, path, ops2, max_credits=42, out=out)
    assert "s1" in caught.value.message and "changed" in caught.value.message
    assert [c for c in ops2.calls if c[0] in ("generate", "extend", "stitch")] == []


async def test_a_state_file_for_another_project_is_refused(profile, tmp_path):
    path = write_board(tmp_path)
    await run(profile, path, Ops(), keyframes=True)
    data = json.loads(json.dumps(STORYBOARD))
    data["project"] = "11111111-2222-4333-8444-555555555555"
    write_board(tmp_path, data)
    with pytest.raises(ValidationError) as caught:
        await run(profile, path, Ops())
    assert "project" in caught.value.message


async def test_spending_more_than_planned_stops_before_the_next_clip(profile, tmp_path):
    path = write_board(tmp_path)
    ops = Ops(charged={'Mai enters. Mai says: "Hello". Soft light.': 25})
    result = await run(profile, path, ops, max_credits=42, out=tmp_path / "o.mp4")
    assert result["status"] == "failed" and result["error"]["code"] == "OVER_BUDGET"
    assert result["credits_spent"] == 25
    assert any("charged 25" in w for w in result["warnings"])
    assert ops.of("extend") == []


async def test_a_state_file_can_be_put_elsewhere_and_assets_too(profile, tmp_path):
    ops = Ops()
    state = tmp_path / "elsewhere" / "state.json"
    assets = tmp_path / "pics"
    await run(profile, write_board(tmp_path), ops, keyframes=True, state_path=state,
              assets_dir=assets)
    assert state.exists() and any(assets.iterdir())
    assert not (tmp_path / "scene.state.json").exists()


async def test_a_finished_scene_is_not_made_again(profile, tmp_path):
    path = write_board(tmp_path)
    out = tmp_path / "o.mp4"
    await run(profile, path, Ops(), max_credits=42, out=out)
    ops = Ops()
    result = await run(profile, path, ops, max_credits=42, out=out)
    assert result["status"] == "done" and result["credits_spent"] == 0
    assert [c for c in ops.calls if c[0] in ("generate", "extend", "stitch", "frame")] == []


async def test_an_existing_output_file_is_refused_before_anything_is_spent(profile, tmp_path):
    out = tmp_path / "o.mp4"
    out.write_bytes(b"old")
    ops = Ops()
    with pytest.raises(ValidationError):
        await run(profile, write_board(tmp_path), ops, max_credits=42, out=out)
    assert ops.calls == []


async def test_progress_lines_are_reported(profile, tmp_path):
    lines: list[str] = []
    await run(profile, write_board(tmp_path), Ops(), on_progress=lines.append)
    assert any("quote" in line.lower() for line in lines)
