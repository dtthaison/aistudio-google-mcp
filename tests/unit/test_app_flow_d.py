import pytest

from aistudio._app.flow import (
    flow_character,
    flow_character_body,
    flow_characters,
    flow_edit,
    flow_extend,
    flow_generate,
    flow_rename_character,
    flow_scene_download,
    flow_scenes,
    flow_set_personality,
    flow_stitch,
)
from aistudio._flow.page import AgentTurn, CharacterOutcome, FlowOutcome
from aistudio._flow.rows import FlowCharacter, FlowScene, ProjectContents, Quote, SceneClip
from aistudio.exceptions import (
    BrowserError,
    NotFoundError,
    OverBudgetError,
    PlanMismatchError,
    ValidationError,
)
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
                 after=None, link_error=None, details_error=None, stored_name=None,
                 outcome_id="c1", personality_error=None, edit_model="Omni 1.1 Flash", **kw):
        super().__init__(ui, contents=contents, **kw)
        self.turn_characters, self.rename_error, self.after = turn_characters, rename_error, after
        self.link_error, self.details_error = link_error, details_error
        self.stored_name, self.outcome_id = stored_name, outcome_id
        self.personality_error, self.edit_model = personality_error, edit_model

    async def start_character(self, description):
        self.calls.append(("character", description))
        return CharacterOutcome(self.outcome_id, [media("i9", video=False)])

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
                         Quote(1, "video", 10), self.turn_characters, tuple(self.attached))

    async def edit_label(self, pid, workflow_id):
        self.calls.append(("edit_label", workflow_id))
        return self.edit_model

    async def edit(self, prompt, *, timeout_s):
        self.calls.append(("edit", prompt))
        self.started, self.paid_media = True, ["e1"]
        return FlowOutcome([media("e1")], None, False)

    async def open_body(self, pid, cid):
        self.calls.append(("open_body", cid))
        return self.character_model_label

    async def start_body(self, prompt):
        self.calls.append(("body", prompt))
        self.started, self.paid_media = True, ["b1"]
        return [media("b1", video=False)]

    async def choose_character_model(self, label):
        self.calls.append(("choose", label))
        self.character_model_label = label

    async def attach_character(self, name):
        self.calls.append(("attach_character", name))

    async def rename_character(self, pid, cid, name):
        self.calls.append(("rename", cid, name))
        if self.rename_error:
            raise self.rename_error

    async def create_character_record(self, pid, name):
        self.calls.append(("record", name))
        return "c7", self.stored_name or name

    async def link_photo(self, pid, cid, media_id):
        self.calls.append(("link", cid, media_id))
        if self.link_error:
            raise self.link_error
        return "i7"

    async def set_personality(self, pid, cid, text):
        self.calls.append(("personality", cid, text))
        if self.personality_error:
            raise self.personality_error

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



async def test_a_failed_description_character_names_it(logged_in_profile, tmp_path):
    # Plan C review M8: the spent-hint named a character id as "media".
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))

    async def broken(description):
        pages[-1].paid_media, pages[-1].started = ["c1"], True
        raise BrowserError("closed")

    original = kw["page_factory"]

    def factory(ui):
        page = original(ui)
        page.start_character = broken
        return page

    kw["page_factory"] = factory
    with pytest.raises(BrowserError) as info:
        await flow_character(profile, "Robot", "a robot", out=tmp_path / "r.png", **kw)
    assert "character c1" in info.value.hint and "'Robot'" in info.value.hint



SPACED = ProjectContents(FULL.titles, FULL.media, FULL.workflows,
                         [FlowCharacter("c1", "Paper  boat", "i1", 100)], [SCENE])


async def test_names_compare_with_spaces_collapsed(logged_in_profile, tmp_path):
    # Plan D review M5: Flow's panel matches names with spaces collapsed.
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(contents=SPACED)
    await flow_generate(profile, "video", "x", characters=["paper boat"], out=tmp_path / "a.mp4",
                        **kw)
    assert ("attach_character", "Paper  boat") in pages[0].calls
    kw, pages, _ = harness(contents=SPACED)
    with pytest.raises(ValidationError):
        await flow_character(profile, "Paper boat", "a boat", out=tmp_path / "b.png", **kw)


async def test_character_mismatch_hint_points_at_flow(logged_in_profile, tmp_path):
    # Plan D review M6: a stale chip would fail every re-run the same way.
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(turn_characters=())
    with pytest.raises(PlanMismatchError) as info:
        await flow_generate(profile, "video", "x", characters=["Paper boat"],
                            out=tmp_path / "a.mp4", **kw)
    assert "prompt box" in info.value.hint


async def test_a_different_stored_name_is_a_warning(logged_in_profile, tmp_path):
    # Plan D review M8: report the name Flow kept.
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(credits=(1010, 1010), stored_name="Apple (2)")
    result = await flow_character(profile, "Apple", photo=IMG, out=tmp_path / "a.png", **kw)
    assert result["name"] == "Apple (2)"
    assert any("Apple (2)" in w for w in result["warnings"])


async def test_no_rename_without_an_id(logged_in_profile, tmp_path):
    # Plan D review M9: never send a rename for character "None".
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010), outcome_id=None)
    result = await flow_character(profile, "Robot", "a robot", out=tmp_path / "r.png", **kw)
    assert not any(c[0] == "rename" for c in pages[0].calls)
    assert any("Robot" in w for w in result["warnings"])
    assert result["name"] == "Untitled character"  # what Flow holds, not what was asked



async def test_a_character_that_never_started_is_not_reported_as_made(logged_in_profile,
                                                                     tmp_path):
    # Plan E review: the page check and the prompt wait run before Start; nothing was made.
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))
    original = kw["page_factory"]

    def factory(ui):
        page = original(ui)

        async def refuse(description):
            raise BrowserError("Flow's character page is not open; nothing was spent",
                               hint="re-run")
        page.start_character = refuse
        return page

    kw["page_factory"] = factory
    with pytest.raises(BrowserError) as info:
        await flow_character(profile, "Robot", "a robot", out=tmp_path / "r.png", **kw)
    assert info.value.hint == "re-run"



async def test_create_with_personality_and_the_personality_command(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))
    result = await flow_character(profile, "Apple", photo=IMG, personality=" Calm ",
                                  out=tmp_path / "a.png", **kw)
    assert ("personality", "c7", "Calm") in pages[0].calls and result["personality"] == "Calm"
    kw, pages, _ = harness(credits=(1010, 1010), personality_error=BrowserError("no"))
    result = await flow_character(profile, "Apple 2", photo=IMG, personality="Calm",
                                  out=tmp_path / "b.png", **kw)
    assert result["status"] == "done" and any("personality" in w for w in result["warnings"])
    kw, pages, _ = harness()
    done = await flow_set_personality(profile, "c1", "Brave", **lists(kw))
    assert ("personality", "c1", "Brave") in pages[0].calls and done["personality"] == "Brave"
    with pytest.raises(NotFoundError):
        await flow_set_personality(profile, "c9", "Brave", **lists(kw))



async def test_an_unnamed_character_cannot_be_attached(logged_in_profile, tmp_path):
    # Plan E review M2: Flow's panel cannot pick a character with no name.
    profile = profile_with_project(logged_in_profile)
    unnamed = ProjectContents(FULL.titles, FULL.media, FULL.workflows,
                              [*CHARS, FlowCharacter("c8", "", "i8", 60)], [SCENE])
    kw, pages, _ = harness(contents=unnamed)
    with pytest.raises(ValidationError) as info:
        await flow_generate(profile, "video", "x", characters=["c8"], out=tmp_path / "a.mp4", **kw)
    assert "character rename c8" in info.value.hint
    assert not any(c[0] == "attach_character" for c in pages[0].calls)


async def test_attachments_with_a_character(logged_in_profile, tmp_path):
    # Plan E review M7: both checks together.
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    result = await flow_generate(profile, "video", "x", start_frame=IMG,
                                 characters=["Paper boat"], out=tmp_path / "a.mp4", **kw)
    calls = pages[0].calls
    assert result["status"] == "quoted"
    assert calls.index(("attach", "apple.jpg")) < calls.index(("attach_character", "Paper boat"))



async def test_character_with_a_chosen_model(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))
    result = await flow_character(profile, "Robot", "a robot", model="nano-banana-pro",
                                 out=tmp_path / "r.png", **kw)
    assert ("choose", "Nano Banana Pro") in pages[0].calls
    assert result["model"] == "nano-banana-pro" and result["credits_spent"] == 0
    kw, pages, opened = harness()
    with pytest.raises(ValidationError):
        await flow_character(profile, "Robot", "a robot", model="veo-3.1-lite",
                             out=tmp_path / "s.png", **kw)
    assert opened == []



async def test_character_body_is_priced_and_downloaded(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1010, 1010))
    result = await flow_character_body(profile, "c1", "  full body,  on a dock ",
                                       out=tmp_path / "b.png", **kw)
    assert ("body", "full body, on a dock") in pages[0].calls
    assert result["credits_spent"] == 0 and (tmp_path / "b.png").exists()
    assert result["character_id"] == "c1" and result["model"] == "nano-banana-2.1"
    kw, pages, _ = harness(character_model="Nano Banana Pro")  # body on Pro: not measured
    with pytest.raises(ValidationError):
        await flow_character_body(profile, "c1", "x", out=tmp_path / "c.png", **kw)
    assert not any(c[0] == "body" for c in pages[0].calls)
    kw, _, _ = harness()
    with pytest.raises(NotFoundError):
        await flow_character_body(profile, "c9", "x", out=tmp_path / "d.png", **kw)



SIX = "9a0b1c2d-1111-4222-8333-944455556666"


async def test_edit_is_priced_by_model_and_length(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(credits=(1000, 980))
    result = await flow_edit(profile, VID, " make it  black and white ", max_credits=20,
                             out=tmp_path / "e.mp4", **kw)
    assert ("edit", "make it black and white") in pages[0].calls
    assert result["credits_spent"] == 20 and result["model"] == "omni-1.1-flash"
    assert result["source"] == VID and (tmp_path / "e.mp4").exists()
    for budget in (None, 19):
        kw, pages, _ = harness()
        with pytest.raises(OverBudgetError):
            await flow_edit(profile, VID, "x", max_credits=budget, out=tmp_path / "f.mp4", **kw)
        assert not any(c[0] == "edit" for c in pages[0].calls)
    kw, pages, _ = harness(edit_model="Veo 3.1 - Lite")
    with pytest.raises(ValidationError):
        await flow_edit(profile, VID, "x", max_credits=100, out=tmp_path / "g.mp4", **kw)
    six = ProjectContents({**FULL.titles, SIX: "six"}, [*FULL.media, listed(media(SIX, seconds=6))],
                          {**FULL.workflows, SIX: "w6"}, CHARS, [SCENE])
    kw, pages, _ = harness(contents=six)
    with pytest.raises(ValidationError) as info:
        await flow_edit(profile, SIX, "x", max_credits=100, out=tmp_path / "h.mp4", **kw)
    assert "6-second" in info.value.message
    assert not any(c[0] in ("edit_label", "edit") for c in pages[0].calls)



async def test_an_older_clip_finds_its_workflow_from_its_record(logged_in_profile, tmp_path):
    # Live: the clip was in the project, but not among the newest workflows Zzl0ze lists.
    import dataclasses

    profile = profile_with_project(logged_in_profile)
    no_workflow = ProjectContents(FULL.titles, FULL.media, {}, CHARS, [SCENE])
    kw, pages, _ = harness(contents=no_workflow, credits=(1000, 980),
                           details=dataclasses.replace(media(VID), workflow_id="w-old"))
    await flow_edit(profile, VID, "make it black and white", max_credits=20,
                    out=tmp_path / "e.mp4", **kw)
    assert ("edit_label", "w-old") in pages[0].calls
