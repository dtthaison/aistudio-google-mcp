
import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import FlowPage
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
                  "as29s": [poll("as29s", record(media_id="new1"))]})
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


async def test_create_character_returns_its_id_and_image(fake_ui):
    image = ["i1", None, "w1", None, None, [[CREATED, 0], "a robot", None, None, None, None,
                                            None, None, [3], 1,
                                            "https://flow-content.google/image/i1?S=s"]]
    ui = fake_ui({"C4BZMd": [CapturedResponse("C4BZMd", 200, batch(
                      "C4BZMd", [[PID, "c1", None, [1, "Untitled character", []]]]), "")],
                  "ogiZ0b": [CapturedResponse("ogiZ0b", 200, batch("ogiZ0b", [[image]]), "")]})
    outcome = await FlowPage(ui, now=Clock()).create_character(PID, "a robot")
    assert outcome.character_id == "c1" and outcome.media[0].image_url
    assert ("type", "[contenteditable=true]", "a robot") in ui.ops


EXTENDING = "Prompt to extend What happens next?"


def extend_ui(fake_ui, *responses, url_workflow="w1", texts=EXTENDING):
    started = {"fZytfe": [CapturedResponse("fZytfe", 200, batch("fZytfe", [
        None, 1020, [["w2"]], [record(status=2, with_links=False, media_id="m2")]]), "")]}
    scripted = {**started, **dict(responses)}
    return fake_ui(scripted,
                   inner={("menuitem", "^Extend"): ["keyboard_double_arrow_right Extend (Veo 3.1 - "
                                                    "Lite)"]},
                   texts={"body": [texts]},
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


async def test_character_model_reads_the_character_pages_picker(fake_ui):
    ui = fake_ui(inner={("button", "Select model family"): ["🍌 Nano Banana Pro arrow_drop_down"]})
    assert await FlowPage(ui).character_model(PID) == "Nano Banana Pro"
    assert ui.ops[0] == ("goto", f"https://flow.google.com/project/{PID}/character")


async def test_media_details_fetch_links_from_inside_the_page(fake_ui):
    # Live: the project list (Zzl0ze) carries no links; as29s, asked from the page, does.
    ui = fake_ui({"as29s": [poll("as29s", record())]})
    media = await FlowPage(ui).media_details(MEDIA)
    assert media.video_url and media.status == 3
    assert ("batch_rpc", "as29s", f'["{MEDIA}"]') in ui.ops
