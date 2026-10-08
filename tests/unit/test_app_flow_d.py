import pytest

from aistudio._app.flow import (
    flow_character,
    flow_characters,
    flow_extend,
    flow_generate,
    flow_rename_character,
    flow_scene_download,
    flow_scenes,
    flow_stitch,
)
from aistudio._flow.page import AgentTurn
from aistudio._flow.rows import FlowCharacter, FlowScene, ProjectContents, Quote, SceneClip
from aistudio.exceptions import BrowserError, NotFoundError, PlanMismatchError, ValidationError
from tests.unit.test_app_flow_b import CONTENTS, IMG, VID, listed, media, store
from tests.unit.test_app_flow_b import FakePage as PlanBPage
from tests.unit.test_app_flow_b import harness as plan_b_harness

VID2 = "5c8f0a1e-1111-4222-8333-944455556666"
RUN = "7d9e1b2f-1111-4222-8333-944455556666"
IMG_RUN = "8e0f2c3a-1111-4222-8333-944455556666"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"x" * 20
CHARS = [FlowCharacter("c1", "Paper boat", "i1", 100),
         FlowCharacter("c2", "Untitled character", "i2", 90),
         FlowCharacter("c3", "untitled CHARACTER", "i3", 80),
         FlowCharacter("c4", "Sketch", None, 70)]
SCENE = FlowScene("s1", "Untitled Scene", 200,
                  [SceneClip(VID2, "the camera pulls back", 0, 8), SceneClip(VID, "apple", 2, 6)])
FULL = ProjectContents(
    {**CONTENTS.titles, VID2: "the camera pulls back", RUN: "running"},
    [*CONTENTS.media, listed(media(VID2)), listed(media(RUN, status=2)),
     listed(media(IMG_RUN, video=False, status=2))],
    CONTENTS.workflows, CHARS, [SCENE])


class FakePage(PlanBPage):
    def __init__(self, ui, *, contents=FULL, turn_characters=("c1",), rename_error=None,
                 after=None, link_error=None, details_error=None, **kw):
        super().__init__(ui, contents=contents, **kw)
        self.turn_characters, self.rename_error, self.after = turn_characters, rename_error, after
        self.link_error, self.details_error = link_error, details_error

    async def media_details(self, media_id):
        if self.details_error:
            raise self.details_error
        return await super().media_details(media_id)

    async def contents(self, pid):
        if self.after is not None and any(c[0] == "extend" for c in self.calls):
            if isinstance(self.after, Exception):
                raise self.after
            return self.after
        return await super().contents(pid)

    async def submit_for_quote(self, text, *, timeout_s=90.0):
        self.calls.append(("submit", text))
        return AgentTurn("kick off this 1 video generation, costing 10 credits?",
                         Quote(1, "video", 10), self.turn_characters)

    async def attach_character(self, name):
        self.calls.append(("attach_character", name))

    async def rename_character(self, pid, cid, name):
        self.calls.append(("rename", cid, name))
        if self.rename_error:
            raise self.rename_error

    async def create_character_record(self, pid, name):
        self.calls.append(("record", name))
        return "c7"

    async def link_photo(self, pid, cid, media_id):
        self.calls.append(("link", cid, media_id))
        if self.link_error:
            raise self.link_error
        return "i7"

    async def stitch(self, clips, *, timeout_s):
        self.calls.append(("stitch", clips))
        return MP4


def harness(**page_kw):
    kw, pages, opened = plan_b_harness()

    def factory(ui):
        pages.append(FakePage(ui, **page_kw))
        return pages[-1]

    kw["page_factory"] = factory
    return kw, pages, opened


def profile_with_project(logged_in_profile):
    profile = logged_in_profile()
    store(profile)
    return profile


def lists(kw):
    return {k: v for k, v in kw.items() if k in ("browser", "page_factory")}


def stitching(kw):
    return {k: v for k, v in kw.items() if k != "downloader"}


async def test_character_list_and_rename(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    listed_chars = await flow_characters(profile, **lists(kw))
    assert [(c["id"], c["name"], c["media_id"]) for c in listed_chars["characters"]][-1] == (
        "c4", "Sketch", None)
    with pytest.raises(ValidationError):
        await flow_rename_character(profile, "c2", "paper BOAT", **lists(kw))
    assert not any(c[0] == "rename" for c in pages[-1].calls)
    result = await flow_rename_character(profile, "c1", "  Boat ", **lists(kw))
    assert ("rename", "c1", "Boat") in pages[-1].calls and result["name"] == "Boat"
    with pytest.raises(NotFoundError):
        await flow_rename_character(profile, "c9", "Boat", **lists(kw))


async def test_character_from_a_project_photo(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))
    result = await flow_character(profile, "Apple", photo=IMG, out=tmp_path / "a.png", **kw)
    calls = pages[0].calls
    assert ("record", "Apple") in calls and ("link", "c7", IMG) in calls
    assert ("details", "i7") in calls and not any(c[0] == "character" for c in calls)
    assert result["character_id"] == "c7" and result["name"] == "Apple"
    assert result["credits_spent"] == 0 and (tmp_path / "a.png").exists()


async def test_character_from_a_local_photo_needs_rights(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"\xff\xd8x")
    kw, pages, opened = harness(credits=(1010, 1010))
    with pytest.raises(ValidationError) as info:
        await flow_character(profile, "Me", photo=str(photo), out=tmp_path / "a.png", **kw)
    assert "--i-have-rights" in info.value.hint and opened == []
    await flow_character(profile, "Me", photo=str(photo), i_have_rights=True,
                         out=tmp_path / "a.png", **kw)
    calls = pages[0].calls
    assert [c[1] for c in calls if c[0] == "upload"] == ["me.jpg"]
    assert ("link", "c7", "up1") in calls


async def test_a_photo_must_be_a_project_image(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    with pytest.raises(ValidationError):
        await flow_character(profile, "Apple", photo=VID, out=tmp_path / "a.png", **kw)
    with pytest.raises(NotFoundError):
        await flow_character(profile, "Apple", photo="00000000-0000-0000-0000-000000000000",
                             out=tmp_path / "a.png", **kw)
    assert not any(c[0] == "record" for page in pages for c in page.calls)


async def test_description_character_is_named(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))
    result = await flow_character(profile, "Robot", "a robot", out=tmp_path / "r.png", **kw)
    calls = pages[0].calls
    assert ("character", "a robot") in calls and ("rename", "c1", "Robot") in calls
    assert result["name"] == "Robot" and result["status"] == "done"
    kw, pages, _ = harness(credits=(1010, 1010), rename_error=BrowserError("no"))
    result = await flow_character(profile, "Robot", "a robot", out=tmp_path / "r2.png", **kw)
    assert result["status"] == "done" and any("rename" in w for w in result["warnings"])


async def test_name_and_source_rules(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, opened = harness()
    for args, extra in [(("  ", "a robot"), {}), (("Robot", "a robot"), {"photo": IMG}),
                        (("Robot",), {})]:
        with pytest.raises(ValidationError):
            await flow_character(profile, *args, out=tmp_path / "x.png", **extra, **kw)
    assert opened == []
    with pytest.raises(ValidationError):
        await flow_character(profile, "PAPER boat", "a robot", out=tmp_path / "x.png", **kw)
    assert not any(c[0] == "character" for c in pages[0].calls)


async def test_generate_attaches_characters_and_checks_the_quote(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    result = await flow_generate(profile, "video", "a boat sails", characters=["Paper boat"],
                                 out=tmp_path / "a.mp4", **kw)
    calls = pages[0].calls
    assert result["status"] == "quoted" and ("attach_character", "Paper boat") in calls
    submit = next(c[1] for c in calls if c[0] == "submit")
    assert "Feature the attached characters: Paper boat." in submit
    kw, pages, _ = harness(turn_characters=())
    with pytest.raises(PlanMismatchError):
        await flow_generate(profile, "video", "a boat sails", characters=["Paper boat"],
                            max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert ("reject",) in pages[0].calls


async def test_character_lookup_rules(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    await flow_generate(profile, "video", "x", characters=["c1"], out=tmp_path / "a.mp4", **kw)
    assert ("attach_character", "Paper boat") in pages[0].calls
    for wanted, error, words in [(["Untitled character"], ValidationError, "rename"),
                                 (["Sketch"], ValidationError, "image"),
                                 (["Nobody"], NotFoundError, "character list")]:
        kw, pages, _ = harness()
        with pytest.raises(error) as info:
            await flow_generate(profile, "video", "x", characters=wanted,
                                out=tmp_path / "a.mp4", **kw)
        assert words in f"{info.value.message} {info.value.hint}"
        assert not any(c[0] in ("submit", "attach_character") for c in pages[0].calls)


async def test_stitch_checks_clips_then_writes_the_mp4(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    out = tmp_path / "s.mp4"
    result = await flow_stitch(profile, [VID, f"{VID2}@2-5"], out=out, **stitching(kw))
    assert ("stitch", [[VID, None, [], [8]], [VID2, None, [2], [5]]]) in pages[0].calls
    assert out.read_bytes() == MP4 and result["files"][0]["mime_type"] == "video/mp4"
    for clips in ([VID, f"{VID2}@5-9"], [VID, f"{VID2}@5-5"], [IMG, VID], [RUN, VID], [VID],
                  [VID, "not-an-id"]):
        kw, pages, _ = harness()
        with pytest.raises((ValidationError, NotFoundError)):
            await flow_stitch(profile, clips, out=tmp_path / "t.mp4", **stitching(kw))
        assert not any(c[0] == "stitch" for page in pages for c in page.calls), clips
    kw, _, opened = harness()
    with pytest.raises(ValidationError):
        await flow_stitch(profile, [VID, VID2], out=out, **stitching(kw))
    assert opened == []


async def test_scene_list_and_download(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    scenes = await flow_scenes(profile, **lists(kw))
    assert scenes["scenes"][0]["clips"][1] == {"media_id": VID, "title": "apple", "seconds": 4}
    result = await flow_scene_download(profile, "s1", out=tmp_path / "s.mp4", **stitching(kw))
    assert ("stitch", [[VID2, None, [], [8]], [VID, None, [2], [6]]]) in pages[-1].calls
    assert result["scene_id"] == "s1"
    with pytest.raises(NotFoundError):
        await flow_scene_download(profile, "s0", out=tmp_path / "s2.mp4", **stitching(kw))


async def test_extend_reports_the_scene_holding_the_new_clip(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    after = ProjectContents(FULL.titles, FULL.media, FULL.workflows, CHARS,
                            [SCENE, FlowScene("s9", "Untitled Scene", 300,
                                              [SceneClip(VID, "apple", 0, 8),
                                               SceneClip("m2", "next", 0, 8)])])
    kw, _, _ = harness(after=after)
    result = await flow_extend(profile, VID, "rolls off", max_credits=10,
                               out=tmp_path / "e.mp4", **kw)
    assert result["scene_id"] == "s9"
    kw, _, _ = harness(after=BrowserError("gone"))
    result = await flow_extend(profile, VID, "rolls off", max_credits=10,
                               out=tmp_path / "e2.mp4", **kw)
    assert result["status"] == "done" and result["scene_id"] == "s1"


async def test_a_photo_must_be_a_finished_image(logged_in_profile, tmp_path):
    # Final review I1: a running or failed image would leave a named character without a photo.
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    with pytest.raises(ValidationError) as info:
        await flow_character(profile, "Apple", photo=IMG_RUN, out=tmp_path / "a.png", **kw)
    assert "nothing was made" in info.value.hint
    assert not any(c[0] == "record" for c in pages[0].calls)


async def test_failures_after_the_record_name_the_character(logged_in_profile, tmp_path):
    # Final review I2: once C4BZMd made the character, no error may lose its id or invite a
    # re-run under another name (a duplicate).
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(credits=(1010, 1010), link_error=BrowserError("closed"))
    with pytest.raises(BrowserError) as info:
        await flow_character(profile, "Apple", photo=IMG, out=tmp_path / "a.png", **kw)
    text = f"{info.value.message} {info.value.hint}"
    assert "c7" in text and "'Apple'" in text and "do not re-run" in text
    kw, _, _ = harness(credits=(1010, 1010), details_error=BrowserError("HTTP 500"))
    result = await flow_character(profile, "Apple", photo=IMG, out=tmp_path / "b.png", **kw)
    assert result["status"] == "done" and result["character_id"] == "c7"
    assert result["media_id"] == "i7" and result["files"] == []
    assert any("i7" in w for w in result["warnings"])


async def test_the_result_names_the_characters_image(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(credits=(1010, 1010))
    result = await flow_character(profile, "Robot", "a robot", out=tmp_path / "r.png", **kw)
    assert result["media_id"] == "i9"
