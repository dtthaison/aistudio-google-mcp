"""``flow frame save``: Flow's "Save frame" in a clip's editor or its scene (findings, "Saving a
frame")."""

import pytest

from aistudio._browser.ui import CapturedResponse, PlaywrightUi
from aistudio._flow.page import (
    CANVAS,
    CLIPS,
    DURATION,
    RULER,
    TIMECODE,
    TIMELINE,
    FlowPage,
    timecode_frame,
)
from aistudio._flow.rows import saved_frame
from aistudio.exceptions import BrowserError, ValidationError
from tests.unit.test_browser_ui import Page
from tests.unit.test_flow_page import PID
from tests.unit.test_flow_rows import batch

FRAME = "1e63b580-c6ca-4e13-8c7a-1847ca609f6c"
# Live (2026-10-29): the reply to Save frame's upload is the new image and its workflow.
SAVED = [[FRAME, PID, "14d0b1b3-a133-4734-8dab-ad5cb18423bd", None, None,
          [[1793260131, 789090000], None, None, None, None, None, [None, None, None, None, 1],
           None, None, 1, None, None, None, 338199],
          [None, [None, None, None, None, 0, None, "image/jpeg"], [1080, 1920]]],
         ["14d0b1b3-a133-4734-8dab-ad5cb18423bd", None, None,
          ["Saved frame from Red paper boat floating pond", [1793260131, 789090000], None, None,
           FRAME], PID]]
# Live geometry of an 8 s clip in its editor: the ruler starts with a 21 px "00" unit, then
# 102 px per second; the clip starts at x=21 and is 816 px wide (4.25 px per frame).
RULER_BOXES = [(0.0, 691.0, 21.0, 158.0), (21.0, 691.0, 102.0, 158.0)]
AREA = [(0.0, 690.0, 1592.0, 160.0)]
ONE_CLIP = [(21.0, 760.0, 816.0, 90.0)]
TWO_CLIPS = [(21.0, 760.0, 816.0, 90.0), (837.0, 760.0, 816.0, 90.0)]
PPF = 816.0 / 192


def player(fake_ui, *, clips=ONE_CLIP, total="00:08:00", shown=("00:07:23",), area=AREA,
           url=f"https://flow.google.com/project/{PID}/edit/w1"):
    ui = fake_ui(texts={DURATION: [total], TIMECODE: list(shown)}, url=url)
    ui.box_table = {CLIPS: [clips], RULER: [RULER_BOXES], TIMELINE: [area]}
    ui.counts = {CLIPS: len(clips)}
    return ui


def clicks(ui):
    return [op[1:] for op in ui.ops if op[0] == "click_at"]


def test_saved_frame_reads_the_new_image_from_flows_reply():
    assert saved_frame(SAVED) == FRAME
    assert saved_frame(None) is None and saved_frame([[None]]) is None


def test_timecodes_count_frames_at_24_per_second():
    # Live: 3.5 s read "00:03:12"; an 8 s clip's length reads "00:08:00"
    assert timecode_frame("00:03:12") == 84
    assert timecode_frame("00:08:00") == 192
    assert timecode_frame(" 01:02:05 ") == 62 * 24 + 5
    assert timecode_frame("") is None and timecode_frame("8 s") is None


async def test_the_last_frame_is_the_default(fake_ui):
    ui = player(fake_ui)
    assert await FlowPage(ui).seek_frame(0, None) == (191, "00:07:23")
    # the middle of frame 191, on the ruler row
    assert clicks(ui) == [(pytest.approx(21 + 191.5 * PPF), pytest.approx(699.0))]


async def test_at_picks_the_nearest_frame(fake_ui):
    ui = player(fake_ui, shown=("00:03:12",))
    assert await FlowPage(ui).seek_frame(0, 3.5) == (84, "00:03:12")
    assert clicks(ui) == [(pytest.approx(21 + 84.5 * PPF), pytest.approx(699.0))]


async def test_a_missed_frame_is_corrected_once(fake_ui):
    ui = player(fake_ui, shown=("00:03:11", "00:03:12"))
    assert await FlowPage(ui).seek_frame(0, 3.5) == (84, "00:03:12")
    assert [x for x, _ in clicks(ui)] == [pytest.approx(21 + 84.5 * PPF),
                                         pytest.approx(21 + 85.5 * PPF)]


async def test_a_frame_missed_twice_is_refused(fake_ui):
    ui = player(fake_ui, shown=("00:03:11",))
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).seek_frame(0, 3.5)
    assert "00:03:11" in info.value.message and "nothing was saved" in info.value.message


async def test_a_time_outside_the_clip_is_refused_before_any_click(fake_ui):
    for at in (8.0, 9.5, -0.5):
        ui = player(fake_ui)
        with pytest.raises(ValidationError) as info:
            await FlowPage(ui).seek_frame(0, at)
        assert "8.00 s" in info.value.message and "nothing was saved" in info.value.message
        assert clicks(ui) == []


async def test_a_scene_clip_is_found_by_its_place_on_the_timeline(fake_ui):
    ui = player(fake_ui, clips=TWO_CLIPS, total="00:16:00", shown=("00:11:00",))
    assert await FlowPage(ui).seek_frame(1, 3.0) == (72, "00:11:00")
    assert clicks(ui)[0][0] == pytest.approx(21 + (192 + 72.5) * PPF)
    ui = player(fake_ui, clips=TWO_CLIPS, total="00:16:00", shown=("00:15:23",),
                area=[(0.0, 690.0, 1700.0, 160.0)])  # wide enough for its end
    assert await FlowPage(ui).seek_frame(1, None) == (191, "00:15:23")
    assert ("dispatch", "button", "Zoom out") not in ui.ops


async def test_a_trimmed_scene_clip_counts_from_its_trim(fake_ui):
    # A clip trimmed to 2-8 s in its scene: its own second 5 is 3 s into its part of the scene
    clips = [(21.0, 760.0, 816.0, 90.0), (837.0, 760.0, 612.0, 90.0)]
    ui = player(fake_ui, clips=clips, total="00:14:00", shown=("00:11:00",))
    assert await FlowPage(ui).seek_frame(1, 5.0, trim_start=2) == (120, "00:11:00")
    with pytest.raises(ValidationError):  # before the trim: not in the scene
        await FlowPage(player(fake_ui, clips=clips, total="00:14:00")).seek_frame(
            1, 1.0, trim_start=2)


async def test_an_off_screen_frame_zooms_the_timeline_out(fake_ui):
    # Live: the ruler shows about 15 s; a click further right missed
    ui = player(fake_ui, clips=TWO_CLIPS, total="00:16:00", shown=("00:15:23",))
    ui.box_table[CLIPS] = [TWO_CLIPS, [(21.0, 760.0, 408.0, 90.0), (429.0, 760.0, 408.0, 90.0)]]
    assert await FlowPage(ui).seek_frame(1, None) == (191, "00:15:23")
    assert ("dispatch", "button", "Zoom out") in ui.ops
    assert clicks(ui)[0][0] == pytest.approx(21 + 383.5 * 816.0 / 384)


async def test_a_timeline_that_never_fits_is_refused(fake_ui):
    ui = player(fake_ui, clips=TWO_CLIPS, total="00:16:00")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).seek_frame(1, None)
    assert "nothing was saved" in info.value.message and clicks(ui) == []


async def test_an_unreadable_player_is_refused(fake_ui):
    ui = player(fake_ui, total="")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).seek_frame(0, None)
    assert "nothing was saved" in info.value.message and clicks(ui) == []


async def test_the_clip_editor_opens_by_its_workflow(fake_ui):
    ui = player(fake_ui)
    await FlowPage(ui).open_frames(PID, "edit/w1", clips=1)
    assert [op for op in ui.ops if op[0] in ("goto", "wait_for")] == [
        ("goto", f"https://flow.google.com/project/{PID}/edit/w1"), ("wait_for", CANVAS)]


async def test_the_player_must_be_the_one_asked_for(fake_ui):
    for ui, clips in ((player(fake_ui, url=f"https://flow.google.com/project/{PID}/edit/w2"), 1),
                      (player(fake_ui), 2)):
        with pytest.raises(BrowserError) as info:
            await FlowPage(ui).open_frames(PID, "edit/w1", clips=clips)
        assert "nothing was saved" in info.value.message
    ui = player(fake_ui)
    ui._missing = {CANVAS}
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).open_frames(PID, "edit/w1", clips=1)
    assert "nothing was saved" in info.value.message


async def test_save_frame_returns_the_new_image(fake_ui):
    ui = fake_ui({"maseQ": [CapturedResponse("maseQ", 200, batch("maseQ", SAVED), "")]})
    page = FlowPage(ui)
    assert await page.save_frame() == FRAME
    assert ("dispatch", "button", "Save frame") in ui.ops and page.started


async def test_a_save_without_a_reply_says_to_check_flow(fake_ui):
    for responses in ({}, {"maseQ": [CapturedResponse("maseQ", 500, "", "")]}):
        with pytest.raises(BrowserError) as info:
            await FlowPage(fake_ui(responses)).save_frame()
        assert "check the project's images" in info.value.hint


async def test_boxes_read_every_box_in_one_step():
    scripts = []

    class Boxes:
        async def evaluate_all(self, script):
            scripts.append(script)
            return [[21, 760, 816, 90]]

    class BoxPage(Page):
        def locator(self, selector):
            return Boxes()

    assert await PlaywrightUi(BoxPage()).boxes(CLIPS) == [(21.0, 760.0, 816.0, 90.0)]
    assert "getBoundingClientRect" in scripts[0]


async def test_click_at_uses_the_real_mouse():
    clicked = []

    class Mouse:
        async def click(self, x, y):
            clicked.append((x, y))

    class MousePage(Page):
        mouse = Mouse()

    await PlaywrightUi(MousePage(url="https://flow.google.com/")).click_at(30.5, 699.0)
    assert clicked == [(30.5, 699.0)]


async def test_a_clip_without_length_is_refused(fake_ui):
    ui = player(fake_ui, clips=[(21.0, 760.0, 816.0, 90.0), (837.0, 760.0, 0.5, 90.0)],
                total="00:08:00")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).seek_frame(1, None)
    assert "nothing was saved" in info.value.message and clicks(ui) == []
