import base64
import json
import re

import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import AGENT_MESSAGE, CHARACTERS_TAB, INGREDIENTS, PROMPT_EDITOR, FlowPage
from aistudio.exceptions import BrowserError, ServerError, WaitTimeoutError
from tests.unit.test_flow_page import PID, Clock
from tests.unit.test_flow_rows import batch, permission, stream

JOB = "projects/1/locations/us/jobs/u1"


def rpc(method, payload, status=200):
    return CapturedResponse(method, status, batch(method, payload), "")


async def test_rename_sends_the_field_mask_and_checks_the_name(fake_ui):
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", [[PID, "c1", None, [1, "Boat", []]]])]})
    await FlowPage(ui).rename_character(PID, "c1", "Boat")
    assert ("batch_rpc", "rzMKMb", json.dumps(
        [[PID, "c1", None, [1, "Boat"]], [["entity_info.display_name"]]])) in ui.ops


async def test_a_rename_flow_did_not_apply_is_an_error(fake_ui):
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", [[PID, "c1", None, [1, "Old", []]]])]})
    with pytest.raises(BrowserError):
        await FlowPage(ui).rename_character(PID, "c1", "Boat")


async def test_a_photo_character_is_created_named_then_linked(fake_ui):
    ui = fake_ui({"C4BZMd": [rpc("C4BZMd", [[PID, "c7", None, [1, "Apple", []]]])],
                  "Sc7aEb": [rpc("Sc7aEb", [["i7", PID, "w7"], ["w7"]])]})
    page = FlowPage(ui)
    cid, stored = await page.create_character_record(PID, "Apple")
    assert (cid, stored) == ("c7", "Apple")
    assert ("batch_rpc", "C4BZMd", json.dumps([[PID, None, None, [1, "Apple", []]]])) in ui.ops
    assert await page.link_photo(PID, cid, "m3") == "i7"
    sent = json.loads(next(op[2] for op in ui.ops if op[:2] == ("batch_rpc", "Sc7aEb")))
    assert sent[0] == "m3" and sent[3] == PID and sent[7] == [None, None, ["c7", [0]]]
    assert all(re.fullmatch(r"[0-9A-F-]{36}", s) for s in sent[9:11])


async def test_a_character_flow_did_not_create_is_an_error(fake_ui):
    ui = fake_ui({"C4BZMd": [rpc("C4BZMd", [], status=500)]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).create_character_record(PID, "Apple")
    assert "character list" in info.value.hint  # it may exist: never re-run under a new name


async def test_a_failed_link_names_the_character(fake_ui):
    ui = fake_ui({"Sc7aEb": [rpc("Sc7aEb", [], status=500)]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).link_photo(PID, "c7", "m3")
    assert "c7" in info.value.message


async def test_stitch_polls_until_done_and_decodes(fake_ui):
    data = b"\x00\x00\x00\x18ftypisom" + b"x" * 50
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [2, "", "", 0]),
                             rpc("IxdSwd", [3, "", "", 3, base64.b64encode(data).decode()])]})
    clips = [["m1", None, [], [8]], ["m2", None, [2], [5]]]
    assert await FlowPage(ui, now=Clock()).stitch(clips, timeout_s=60) == data
    assert ("batch_rpc", "GjQLTd", json.dumps([clips])) in ui.ops
    assert ("batch_rpc", "IxdSwd", json.dumps([[[JOB]]])) in ui.ops


async def test_a_stitch_flow_did_not_start_is_an_error(fake_ui):
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [["oops"]]])]})
    with pytest.raises(BrowserError):
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=60)


async def test_a_failed_stitch_is_a_server_error(fake_ui):
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [5, "", "", 0])]})
    with pytest.raises(ServerError):
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=60)


async def test_a_slow_stitch_times_out(fake_ui):
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [2, "", "", 0]) for _ in range(100)]})
    with pytest.raises(WaitTimeoutError):
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=5)


async def test_attach_character_searches_then_picks_it(fake_ui):
    # Live: the panel acts on its highlighted item (the first), not the one clicked, so the
    # name is searched first; the search leaves it as the only, highlighted option.
    from aistudio._flow.page import SEARCH_ASSETS

    ui = fake_ui(labels={("option", "."): [["Paper boat"]]})
    await FlowPage(ui).attach_character("Paper boat")
    assert [op for op in ui.ops if op[0] in ("activate", "dispatch", "fill")] == [
        ("activate", "button", INGREDIENTS, 0), ("dispatch", "tab", CHARACTERS_TAB.pattern),
        ("fill", SEARCH_ASSETS, "Paper boat"), ("dispatch", "option", "Paper boat")]


async def test_attach_character_refuses_when_another_name_comes_first(fake_ui):
    # Plan F review M3: "Robo" also finds "Robo 2"; the panel acts on the first option.
    from aistudio.exceptions import ValidationError

    ui = fake_ui(labels={("option", "."): [["Robo 2", "Robo"]]})
    with pytest.raises(ValidationError) as info:
        await FlowPage(ui).attach_character("Robo")
    assert "rename" in info.value.hint
    assert not any(op[:2] == ("dispatch", "option") for op in ui.ops)


async def test_the_quote_reports_the_characters_it_was_sent_with(fake_ui):
    message = ["s", [[[["x"]]], None, None, [["c1"]]], ["projects/p"]]
    body = "f.req=" + json.dumps([None, json.dumps(message)])
    response = CapturedResponse("StreamChat", 200, stream(permission(cost=10)), body)
    ui = fake_ui({"StreamChat": [response]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 video."], AGENT_MESSAGE: ["q"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert turn.characters == ("c1",)


async def test_stitch_gives_up_after_repeated_failures(fake_ui):
    # Plan D review M1: an expired session is not "still stitching" for 5 minutes.
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [rpc("IxdSwd", [], status=401) for _ in range(100)]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=10_000)
    assert "401" in info.value.message
    assert len([op for op in ui.ops if op[:2] == ("batch_rpc", "IxdSwd")]) == 5


async def test_link_photo_hint_is_accurate(fake_ui):
    # Plan D review M7: re-running with the same name is refused, so the hint must not say
    # it makes another character.
    ui = fake_ui({"Sc7aEb": [rpc("Sc7aEb", [], status=500)]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).link_photo(PID, "c7", "m3")
    assert "do not re-run" in info.value.hint and "another character" not in info.value.hint



async def test_personality_sends_its_field_mask(fake_ui):
    text = 'Calm, "curious"\nand kind ' + "x" * 2000
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", [[PID, "c1", None, [1, "Boat", []]]])]})
    await FlowPage(ui).set_personality(PID, "c1", text)
    assert ("batch_rpc", "rzMKMb", json.dumps(
        [[PID, "c1", None, [1, None, [None, None, text]]],
         [["entity_info.character_info.personality_notes"]]])) in ui.ops
    ui = fake_ui({"rzMKMb": [rpc("rzMKMb", [], status=500)]})
    with pytest.raises(BrowserError):
        await FlowPage(ui).set_personality(PID, "c1", "Calm")



async def test_stitch_give_up_names_an_unreadable_reply(fake_ui):
    ui = fake_ui({"GjQLTd": [rpc("GjQLTd", [None, [[JOB]]])],
                  "IxdSwd": [CapturedResponse("IxdSwd", 200, "garbage", "") for _ in range(9)]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).stitch([["m1", None, [], [8]]], timeout_s=10_000)
    assert "unreadable" in info.value.message



async def test_body_types_into_the_body_prompt_and_reads_the_image(fake_ui):
    from aistudio._flow.page import CHARACTER_MODEL, EDITOR_LAST
    from tests.unit.test_flow_page_b import IMAGE_ROW

    picker = "\U0001f34c Nano Banana 2.1 arrow_drop_down"
    ui = fake_ui({"ogiZ0b": [rpc("ogiZ0b", [[IMAGE_ROW]])]},
                 inner={("button", CHARACTER_MODEL): [picker]},
                 texts={EDITOR_LAST: ["full body on a dock"]},
                 visible={("button", "Ingredient"): [True]},
                 url=f"https://flow.google.com/project/{PID}/character/c1")
    page = FlowPage(ui, now=Clock())
    assert await page.open_body(PID, "c1") == "Nano Banana 2.1"
    assert ("dispatch", "button", "Create body") in ui.ops
    media = await page.start_body("full body on a dock")
    assert [m.media_id for m in media] == ["i1"] and page.started and page.paid_media == ["i1"]


async def test_body_needs_the_characters_own_page(fake_ui):
    ui = fake_ui(url=f"https://flow.google.com/project/{PID}")
    with pytest.raises(BrowserError):
        await FlowPage(ui, now=Clock()).open_body(PID, "c1")
    assert ("dispatch", "button", "Create body") not in ui.ops



async def test_body_mode_must_take_effect_before_anything_is_priced(fake_ui):
    # Plan G review Important 1: otherwise Start runs the page's other, unmeasured action.
    from aistudio._flow.page import CHARACTER_MODEL

    ui = fake_ui(inner={("button", CHARACTER_MODEL): ["Nano Banana 2.1 arrow_drop_down"]},
                 url=f"https://flow.google.com/project/{PID}/character/c1")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).open_body(PID, "c1")
    assert "nothing was spent" in info.value.message
