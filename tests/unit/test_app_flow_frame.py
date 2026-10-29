import dataclasses

import pytest

from aistudio._app.flow import flow_frame
from aistudio._flow.rows import FlowScene, ProjectContents, SceneClip
from aistudio.exceptions import BrowserError, NotFoundError, ValidationError
from tests.unit.test_app_flow_b import IMG, VID, listed, media
from tests.unit.test_app_flow_b import PID as PROJECT
from tests.unit.test_app_flow_d import CHARS, FULL, SCENE, profile_with_project
from tests.unit.test_app_flow_d import FakePage as PlanDPage
from tests.unit.test_app_flow_d import harness as plan_d_harness

FRAME = "1e63b580-c6ca-4e13-8c7a-1847ca609f6c"
EXT = "9a1b2c3d-1111-4222-8333-944455556666"  # an extension: no workflow of its own


class FakePage(PlanDPage):
    def __init__(self, ui, *, reached=(191, "00:07:23"), save_error=None, **kw):
        kw.setdefault("credits", (1020, 1020))
        super().__init__(ui, **kw)
        self.reached, self.save_error = reached, save_error

    async def open_frames(self, pid, where, *, clips):
        self.calls.append(("open_frames", where, clips))

    async def seek_frame(self, clip, at, *, trim_start=0):
        self.calls.append(("seek", clip, at, trim_start))
        return self.reached

    async def save_frame(self):
        self.calls.append(("save",))
        self.started = True
        if self.save_error is not None:
            raise self.save_error
        return FRAME


def harness(**page_kw):
    kw, pages, opened = plan_d_harness()

    def factory(ui):
        pages.append(FakePage(ui, **page_kw))
        return pages[-1]

    kw["page_factory"] = factory
    return kw, pages, opened


def with_extension(start=0, end=8):
    ext = dataclasses.replace(listed(media(EXT)), prompt="the bunny is finished")
    chain = FlowScene("s7", "Untitled Scene", 300,
                      [SceneClip(VID, "apple", 0, 8), SceneClip(EXT, "bunny", start, end)])
    return ProjectContents(FULL.titles, [*FULL.media, ext], FULL.workflows, CHARS,
                           [SCENE, chain])


async def test_the_last_frame_of_a_clip_is_saved_in_its_editor(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages, opened = harness()
    result = await flow_frame(profile, VID, **kw)
    calls = [c for c in pages[0].calls if c[0] in ("open_frames", "seek", "save")]
    assert calls == [("open_frames", "edit/w1", 1), ("seek", 0, None, 0), ("save",)]
    assert result == {"status": "done", "media_id": FRAME, "source": VID, "at": 7.958,
                      "timecode": "00:07:23", "project": PROJECT, "credits_spent": 0,
                      "credits_left": 1020}
    assert opened == ["minimized"]


async def test_at_is_passed_and_reported_as_the_frame_reached(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(reached=(84, "00:03:12"))
    result = await flow_frame(profile, VID, at=3.5, **kw)
    assert ("seek", 0, 3.5, 0) in pages[0].calls
    assert (result["at"], result["timecode"]) == (3.5, "00:03:12")


async def test_out_also_downloads_the_frame(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness()
    result = await flow_frame(profile, VID, out=tmp_path / "f.png", **kw)
    assert ("details", FRAME) in pages[0].calls
    assert result["files"][0]["media_id"] == FRAME and (tmp_path / "f.png").exists()


async def test_an_extension_is_saved_through_its_scene(logged_in_profile):
    # Live: an extension's own editor is empty; its scene has the player and Save frame.
    profile = profile_with_project(logged_in_profile)
    kw, pages, _ = harness(contents=with_extension(start=2))
    await flow_frame(profile, EXT, at=5, **kw)
    assert ("open_frames", "scene/s7", 2) in pages[0].calls
    assert ("seek", 1, 5, 2) in pages[0].calls


async def test_only_finished_videos_in_the_project(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    running = dataclasses.replace(FULL, media=[listed(media(VID, status=2)), *FULL.media[1:]])
    for media_id, contents, error in ((IMG, FULL, ValidationError),
                                      (VID, running, ValidationError),
                                      ("0" * 8 + "-0000-4000-8000-" + "0" * 12, FULL,
                                       NotFoundError)):
        kw, pages, _ = harness(contents=contents)
        with pytest.raises(error):
            await flow_frame(profile, media_id, **kw)
        assert not any(c[0] in ("open_frames", "save") for c in pages[0].calls)


async def test_a_time_outside_the_clip_is_refused_first(logged_in_profile, tmp_path):
    profile = profile_with_project(logged_in_profile)
    kw, _, opened = harness()
    with pytest.raises(ValidationError):  # before any browser
        await flow_frame(profile, VID, at=-1, **kw)
    assert opened == []
    for at, contents in ((8, FULL), (9.5, FULL), (1, with_extension(start=2))):
        kw, pages, _ = harness(contents=contents)
        with pytest.raises(ValidationError) as info:
            await flow_frame(profile, VID if contents is FULL else EXT, at=at, **kw)
        assert "nothing was saved" in info.value.message
        assert not any(c[0] == "open_frames" for c in pages[0].calls)
    (tmp_path / "f.png").write_bytes(b"x")
    with pytest.raises(ValidationError):  # an existing -o, before any browser
        await flow_frame(profile, VID, out=tmp_path / "f.png", **harness()[0])


async def test_a_failed_save_says_to_check_flow(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(save_error=BrowserError("Flow did not report the saved frame",
                                               hint="check the project's images in Flow"))
    with pytest.raises(BrowserError) as info:
        await flow_frame(profile, VID, **kw)
    assert "check the project's images" in info.value.hint


async def test_a_credit_change_is_a_warning(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(credits=(1020, 1010))
    result = await flow_frame(profile, VID, **kw)
    assert result["credits_spent"] == 10
    assert any("fell by 10" in w for w in result["warnings"])
    kw, _, _ = harness(credits=(BrowserError("no menu"), 1020))
    result = await flow_frame(profile, VID, **kw)  # a failed read never fails a free save
    assert result["media_id"] == FRAME and result["warnings"]


async def test_an_unread_balance_after_the_save_is_one_warning(logged_in_profile):
    profile = profile_with_project(logged_in_profile)
    kw, _, _ = harness(credits=(1020, BrowserError("no menu")))
    result = await flow_frame(profile, VID, **kw)
    assert result["credits_spent"] is None and result["credits_left"] is None
    assert result["warnings"] == ["could not read Flow credits around the save; Save frame was "
                                  "free when measured"]
