
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


async def test_extend_reads_the_model_and_follows_the_new_clip(fake_ui):
    started = [None, 1020, [["w2"]], [record(status=2, with_links=False, media_id="m2")]]
    ui = fake_ui({"fZytfe": [CapturedResponse("fZytfe", 200, batch("fZytfe", started), "")],
                  "as29s": [poll("as29s", record(media_id="m2"))]},
                 inner={("menuitem", "^Extend"): ["keyboard_double_arrow_right Extend (Veo 3.1 - "
                                                  "Lite)"]},
                 url=f"https://flow.google.com/project/{PID}")
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    await page.open_project(PID)
    assert await page.extend_label("Red apple") == "Veo 3.1 - Lite"
    outcome = await page.extend("Red apple", "rolls off", timeout_s=60)
    assert [m.media_id for m in outcome.media] == ["m2"] and outcome.credits_left == 1020
