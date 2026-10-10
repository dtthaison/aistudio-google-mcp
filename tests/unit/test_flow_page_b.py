
import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import (
    CLIPS,
    EDITOR_ANY,
    EDITOR_LAST,
    LAST_CLIP,
    LAST_CLIP_SELECTED,
    FlowPage,
)
from tests.unit.test_flow_page import PID, Clock, poll
from tests.unit.test_flow_rows import CREATED, MEDIA, batch, record


async def test_projects_come_from_the_home_page_list(fake_ui):
    payload = [[["p1", ["aistudio", None, [1, 0]]]]]
    ui = fake_ui({"UpteDb": [CapturedResponse("UpteDb", 200, batch("UpteDb", payload), "")]})
    (project,) = await FlowPage(ui, now=Clock()).projects()
    assert (project.id, project.title) == ("p1", "aistudio")


async def test_contents_open_the_project(fake_ui):
    workflow = ["w1", None, None, ["Red apple", [CREATED, 0], None, None, MEDIA], PID]
    ui = fake_ui({"Zzl0ze": [CapturedResponse("Zzl0ze", 200,
                                              batch("Zzl0ze", [None, [workflow], [record()], []]),
                                              "")]},
                 url=f"https://flow.google.com/project/{PID}")
    contents = await FlowPage(ui, now=Clock()).contents(PID)
    assert contents.titles == {MEDIA: "Red apple"}


async def test_upload_accepts_the_rights_dialog_and_returns_the_new_media(fake_ui, tmp_path):
    image = tmp_path / "apple.jpg"
    image.write_bytes(b"\xff\xd8x")
    ui = fake_ui({"maseQ": [CapturedResponse("maseQ", 200, ")]}'", "")],
                  "as29s": [poll("as29s", record(media_id="new1"))]},
                 visible={("button", "I agree"): [True]})
    media_id = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).upload(image)
    assert media_id == "new1"
    assert ("choose_file", "button", "Upload media", str(image)) in ui.ops
    assert ("dispatch", "button", "I agree") in ui.ops


async def test_attach_uses_the_tiles_add_to_prompt(fake_ui):
    ui = fake_ui()
    await FlowPage(ui).attach("Red apple")
    assert ui.ops[:3] == [("hover", "Red apple"),
                          ("dispatch_near", "button", "More options", "Red apple"),
                          ("dispatch", "menuitem", "Add to prompt")]


def character_ui(fake_ui, image_payload):
    responses = {"C4BZMd": [CapturedResponse("C4BZMd", 200, batch(
        "C4BZMd", [[PID, "c1", None, [1, "Untitled character", []]]]), "")]}
    if image_payload is not None:
        responses["ogiZ0b"] = [CapturedResponse("ogiZ0b", 200, batch("ogiZ0b", image_payload), "")]
    return fake_ui(responses, inner={("button", "Select model family"): [
        "🍌 Nano Banana 2.1 arrow_drop_down"]}, texts={EDITOR_ANY: ["a robot"]},
        url=f"https://flow.google.com/project/{PID}/character")


IMAGE_ROW = ["i1", None, "w1", None, None, None,
             [[None, 1, None, None, None, None, 1, "a robot", 36, None, None, "w1", None,
               "https://flow-content.google/image/i1?S=s", 3]], None, [1376, 768]]


async def test_the_character_page_is_loaded_once(fake_ui):
    ui = character_ui(fake_ui, [[IMAGE_ROW]])
    page = FlowPage(ui, now=Clock())
    assert await page.open_character(PID) == "Nano Banana 2.1"
    outcome = await page.start_character("a robot")
    assert outcome.character_id == "c1" and outcome.media[0].image_url
    assert [op for op in ui.ops if op[0] == "goto"] == [
        ("goto", f"https://flow.google.com/project/{PID}/character")]
    assert ("type", "[contenteditable=true]", "a robot") in ui.ops


async def test_the_character_id_survives_a_missing_image(fake_ui):
    from aistudio.exceptions import BrowserError

    page = FlowPage(character_ui(fake_ui, None), now=Clock())
    await page.open_character(PID)
    with pytest.raises(BrowserError) as info:
        await page.start_character("a robot")
    assert "c1" in info.value.message and page.paid_media == ["c1"]


async def test_upload_ignores_media_that_existed_before(fake_ui, tmp_path):
    image = tmp_path / "b.jpg"
    image.write_bytes(b"\xff\xd8x")
    ui = fake_ui({"maseQ": [CapturedResponse("maseQ", 200, ")]}'", "")],
                  "as29s": [poll("as29s", record(media_id="old1")),
                            poll("as29s", record(media_id="new3"))]})
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    assert await page.upload(image, known_ids={"old1"}) == "new3"


EXTENDING = "Prompt to extend What happens next?"


def extend_ui(fake_ui, *responses, url_workflow="w1", texts=EXTENDING):
    started = {"fZytfe": [CapturedResponse("fZytfe", 200, batch("fZytfe", [
        None, 1020, [["w2"]], [record(status=2, with_links=False, media_id="m2")]]), "")]}
    scripted = {**started, **dict(responses)}
    return fake_ui(scripted,
                   inner={("menuitem", "^Extend"): ["keyboard_double_arrow_right Extend (Veo 3.1 - "
                                                    "Lite)"]},
                   texts={"body": [texts], EDITOR_LAST: ["rolls off the table"]},
                   url=f"https://flow.google.com/project/{PID}/edit/{url_workflow}")


async def test_extend_opens_the_clip_by_its_workflow_and_reads_the_model(fake_ui):
    # Review I1: never pick a clip by its title text.
    ui = extend_ui(fake_ui)
    assert await FlowPage(ui).extend_label(PID, "w1") == "Veo 3.1 - Lite"
    assert ui.ops[0] == ("goto", f"https://flow.google.com/project/{PID}/edit/w1")


async def test_extend_refuses_when_the_editor_opened_another_clip(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = extend_ui(fake_ui, url_workflow="other")
    with pytest.raises(BrowserError):
        await FlowPage(ui).extend_label(PID, "w1")
    assert not any(op[0] == "dispatch" for op in ui.ops)


async def test_extend_follows_only_the_clip_flow_says_it_started(fake_ui):
    # Review I3: a recent record of the source clip is never taken for the extension.
    ui = extend_ui(fake_ui, ("as29s", [poll("as29s", record(media_id=MEDIA)),
                                       poll("as29s", record(media_id="m2"))]))
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.extend("rolls off", timeout_s=60)
    assert [m.media_id for m in outcome.media] == ["m2"] and outcome.credits_left == 1020
    assert page.paid_media == ["m2"]


async def test_a_refused_extension_fails_fast(fake_ui):
    # Review I4: no 10-minute wait for a job that never started.
    from aistudio.exceptions import BrowserError

    ui = extend_ui(fake_ui, ("fZytfe", [CapturedResponse("fZytfe", 500, "", "")]))
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).extend("rolls off", timeout_s=600)
    assert "may still be charged" in info.value.hint


async def test_extend_presses_start_only_in_the_extend_panel(fake_ui):
    # Review I5: the edit page also has an Omni edit prompt with its own Start button.
    from aistudio.exceptions import BrowserError

    ui = extend_ui(fake_ui, texts="Describe how to edit this video…")
    with pytest.raises(BrowserError):
        await FlowPage(ui, now=Clock()).extend("rolls off", timeout_s=60)
    assert not any(op[0] == "click" for op in ui.ops)


async def test_media_details_fetch_links_from_inside_the_page(fake_ui):
    # Live: the project list (Zzl0ze) carries no links; as29s, asked from the page, does.
    ui = fake_ui({"as29s": [poll("as29s", record())]})
    media = await FlowPage(ui).media_details(MEDIA)
    assert media.video_url and media.status == 3
    assert ("batch_rpc", "as29s", f'["{MEDIA}"]') in ui.ops


async def test_a_queued_status_is_not_a_failure(fake_ui):
    # Live: an extension's poll said status 6 (queued) before 2 (running) and 3 (done).
    queued = record(media_id="m2", status=6, with_links=False)
    ui = extend_ui(fake_ui, ("jwpduf", [poll("jwpduf", [None, 1010, [queued]])]),
                   ("as29s", [poll("as29s", record(media_id="m2"))]))
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).extend(
        "rolls off", timeout_s=60)
    assert [m.media_id for m in outcome.media] == ["m2"] and not outcome.timed_out


async def test_upload_without_a_rights_dialog_still_completes(fake_ui, tmp_path):
    # Live: Flow asks "Rights to use this image" only on a first upload.
    image = tmp_path / "apple-start.jpg"
    image.write_bytes(b"\xff\xd8x")
    ui = fake_ui({"maseQ": [CapturedResponse("maseQ", 200, ")]}'", "")],
                  "as29s": [poll("as29s", record(media_id="new2"))]})
    assert await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).upload(image) == "new2"
    assert ("dispatch", "button", "I agree") not in ui.ops


async def test_extend_starts_only_once_the_editor_shows_the_prompt(fake_ui):
    # Final review I2: Start spends 10 credits with no quote; a lagging insert sent nothing.
    from aistudio.exceptions import BrowserError

    ui = extend_ui(fake_ui)
    ui._texts[EDITOR_LAST] = [""]
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).extend("rolls off the table", timeout_s=60)
    assert "nothing was sent" in info.value.message
    assert not any(op[0] == "click" for op in ui.ops)


async def test_character_starts_only_once_the_editor_shows_the_prompt(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = character_ui(fake_ui, [[IMAGE_ROW]])
    ui._texts[EDITOR_ANY] = [""]
    page = FlowPage(ui, now=Clock())
    await page.open_character(PID)
    with pytest.raises(BrowserError):
        await page.start_character("a robot")
    assert not any(op[0] == "click" for op in ui.ops)


async def test_extend_does_not_wait_for_an_unverified_scene_reply(fake_ui):
    # Deferred minor #51: the scene comes from the project itself (the rqZuUc reply's shape was
    # never seen live), so nothing waits for it.
    ui = extend_ui(fake_ui, ("as29s", [poll("as29s", record(media_id="m2"))]))
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    await page.extend("rolls off", timeout_s=60)
    assert "rqZuUc" not in [m for m, _ in ui.waits]
    assert not hasattr(page, "last_scene_id")


async def test_start_character_needs_the_character_page(fake_ui):
    # Plan C review M8: never type and press Start on some other page.
    from aistudio.exceptions import BrowserError

    ui = fake_ui(texts={EDITOR_ANY: ["a robot"]}, url=f"https://flow.google.com/project/{PID}")
    with pytest.raises(BrowserError):
        await FlowPage(ui, now=Clock()).start_character("a robot")
    assert not any(op[0] in ("type", "click") for op in ui.ops)



async def test_the_page_says_when_start_was_pressed(fake_ui):
    ui = extend_ui(fake_ui, ("as29s", [poll("as29s", record(media_id="m2"))]))
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    assert page.started is False
    await page.extend("rolls off", timeout_s=60)
    assert page.started is True



async def test_choose_character_model_reads_the_picker_back(fake_ui):
    import re

    from aistudio._flow.page import CHARACTER_MODEL
    from aistudio.exceptions import BrowserError

    pro = "\U0001f34c Nano Banana Pro arrow_drop_down"
    ui = fake_ui(inner={("button", CHARACTER_MODEL): [pro]})
    await FlowPage(ui).choose_character_model("Nano Banana Pro")
    dispatched = [op for op in ui.ops if op[0] == "dispatch"]
    assert dispatched[0] == ("dispatch", "button", CHARACTER_MODEL)
    assert dispatched[1][:2] == ("dispatch", "menuitem")
    assert re.search(dispatched[1][2], "\U0001f34c Nano Banana Pro")
    other = "\U0001f34c Nano Banana 2.1 arrow_drop_down"
    ui = fake_ui(inner={("button", CHARACTER_MODEL): [other]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).choose_character_model("Nano Banana Pro")
    assert "nothing was spent" in info.value.message



EDIT_BOX = ("a red apple Describe how to edit this video\u2026 add pen_magic Omni 1.1 Flash "
            "arrow_forward Google Flow can make mistakes")


async def test_edit_label_reads_the_edit_box_model(fake_ui):
    ui = fake_ui(texts={"body": [EDIT_BOX]}, url=f"https://flow.google.com/project/{PID}/edit/w1")
    assert await FlowPage(ui).edit_label(PID, "w1") == "Omni 1.1 Flash"
    assert ui.ops[0] == ("goto", f"https://flow.google.com/project/{PID}/edit/w1")


async def test_edit_label_refuses_another_clip_or_no_box(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(texts={"body": [EDIT_BOX]}, url=f"https://flow.google.com/project/{PID}/edit/w2")
    with pytest.raises(BrowserError):
        await FlowPage(ui).edit_label(PID, "w1")
    ui = fake_ui(texts={"body": ["no edit box"]},
                 url=f"https://flow.google.com/project/{PID}/edit/w1")
    with pytest.raises(BrowserError):
        await FlowPage(ui).edit_label(PID, "w1")


def edited(media_id):
    """A clip made by an Omni edit (live model key ``abra_edit``)."""
    row = record(media_id=media_id)
    row[5][6][1][0][0] = "abra_edit"
    return row


async def test_edit_follows_only_the_new_clip(fake_ui):
    old = record(media_id=MEDIA, created=CREATED - 86_400)
    ui = fake_ui({"as29s": [poll("as29s", old), poll("as29s", edited("m3"))]},
                 texts={EDITOR_LAST: ["make it black and white"]})
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.edit("make it black and white", timeout_s=60, known={MEDIA})
    assert [m.media_id for m in outcome.media] == ["m3"] and page.started
    assert ("click", "button", "Start generation", -1) in ui.ops



async def test_edit_never_takes_the_source_or_another_jobs_clip(fake_ui):
    # Plan G review Critical 1: a source made a minute before Start is inside the time slack.
    source = record(media_id=MEDIA, created=CREATED - 60)  # done, with its link
    other = record(media_id="m8", created=CREATED - 10)   # another job's Veo clip
    ui = fake_ui({"as29s": [poll("as29s", source), poll("as29s", other),
                            poll("as29s", edited("m3"))]},
                 texts={EDITOR_LAST: ["make it black and white"]})
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.edit("make it black and white", timeout_s=60, known={MEDIA})
    assert [m.media_id for m in outcome.media] == ["m3"] and page.paid_media == ["m3"]



async def test_new_upload_tiles_are_waited_for_with_reloads(fake_ui):
    # Live: right after an upload, a reload can list the project without it.
    ui = fake_ui(texts_shown={"b.png": [False, False, False, True]},
                 url=f"https://flow.google.com/project/{PID}")
    await FlowPage(ui, now=Clock()).show_tiles(PID, ["a.png", "b.png"])
    assert len([op for op in ui.ops if op[0] == "goto"]) >= 1


async def test_tiles_that_never_show_are_an_error_before_anything_is_sent(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(texts_shown={"b.png": [False]}, url=f"https://flow.google.com/project/{PID}")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).show_tiles(PID, ["b.png"])
    assert "nothing was sent" in info.value.message



async def test_media_details_returns_the_record_asked_for(fake_ui):
    ui = fake_ui({"as29s": [poll("as29s", [[record(media_id="other"), record(media_id=MEDIA)]])]})
    media = await FlowPage(ui).media_details(MEDIA)
    assert media.media_id == MEDIA


LAST_PROMPT = "Satisfying fast time-lapse: the same hands crochet the same pastel pink yarn"


def scene_ui(fake_ui, *, scene="s7", missing=(), shown=True, clips=3):
    ui = fake_ui(inner={("menuitem", "^Extend"): ["keyboard_double_arrow_right Extend (Veo 3.1 - "
                                                    "Lite)"]},
                   url=f"https://flow.google.com/project/{PID}/scene/{scene}", missing=missing,
                   # the side panel shows the selected clip's prompt, cut short (live)
                   texts={"body": ["Untitled Scene\n" + (LAST_PROMPT[:50] + "…" if shown
                                                           else "Vertical 9:16 first shot…")]})
    ui.counts = {CLIPS: clips}
    return ui


async def test_a_scene_extension_selects_the_last_clip_first(fake_ui):
    # Live (findings, "Extending an extension"): an extension has no editor of its own; its scene
    # opens with the FIRST clip selected, and Extend continues the selected clip.
    ui = scene_ui(fake_ui)
    assert await FlowPage(ui).extend_label_in_scene(PID, "s7", LAST_PROMPT,
                                                    clips=3) == "Veo 3.1 - Lite"
    steps = [op[:2] for op in ui.ops if op[0] in ("goto", "click_selector", "wait_for", "dispatch")]
    assert steps == [("goto", f"https://flow.google.com/project/{PID}/scene/s7"),
                     ("wait_for", EDITOR_ANY), ("click_selector", LAST_CLIP),
                     ("wait_for", LAST_CLIP_SELECTED), ("dispatch", "button")]


async def test_a_scene_extension_is_refused_unless_the_last_clip_is_selected(fake_ui):
    from aistudio.exceptions import BrowserError

    # Plan L review M1 (re-graded): a clip Flow shows but the project read did not list yet
    # would be the "last" one on the page; the clip count must match the scene that was read
    # Review M4: a scene page that never loads says so, never the raw selector error
    for ui in (scene_ui(fake_ui, missing={LAST_CLIP_SELECTED}), scene_ui(fake_ui, shown=False),
               scene_ui(fake_ui, scene="other"), scene_ui(fake_ui, clips=4),
               scene_ui(fake_ui, missing={EDITOR_ANY})):
        with pytest.raises(BrowserError) as info:
            await FlowPage(ui).extend_label_in_scene(PID, "s7", LAST_PROMPT, clips=3)
        assert "nothing was spent" in info.value.message
        assert not any(op[0] == "dispatch" for op in ui.ops)  # Add clip never pressed


async def test_a_clip_without_an_editor_says_what_to_do(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(url=f"https://flow.google.com/project/{PID}/edit/w9", missing={EDITOR_ANY})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).extend_label(PID, "w9")
    assert "last clip of its scene" in info.value.message
    assert "nothing was spent" in info.value.message
    assert "flow scene list" in info.value.hint and "re-run" in info.value.hint  # review M5


async def test_a_prompt_cut_at_a_space_still_matches(fake_ui):
    # Plan L review M3: character 40 is a space; the panel cuts at the word before it.
    prompt = "The pink bunny waves hello at a camera, then hops away"  # [39] is a space
    assert prompt[39] == " "
    ui = scene_ui(fake_ui)
    ui._texts["body"] = ["Untitled Scene " + prompt[:39] + "…"]
    assert await FlowPage(ui).extend_label_in_scene(PID, "s7", prompt, clips=3)


async def test_a_closed_window_passes_through_the_label_reads(fake_ui):
    from aistudio.exceptions import WindowClosedError

    class Closed(type(fake_ui())):
        async def wait_for(self, selector, *, timeout_s=30.0):
            raise WindowClosedError("the browser window was closed before aistudio finished")

    for read in (lambda p: p.extend_label(PID, "w1"),
                 lambda p: p.extend_label_in_scene(PID, "s7", LAST_PROMPT, clips=3)):
        with pytest.raises(WindowClosedError):
            await read(FlowPage(Closed()))
