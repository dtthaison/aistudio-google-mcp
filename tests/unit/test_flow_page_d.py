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
    cid = await page.create_character_record(PID, "Apple")
    assert cid == "c7"
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


async def test_attach_character_uses_the_characters_tab(fake_ui):
    ui = fake_ui()
    await FlowPage(ui).attach_character("Paper boat")
    assert [op for op in ui.ops if op[0] in ("activate", "dispatch")] == [
        ("activate", "button", INGREDIENTS, 0), ("dispatch", "tab", CHARACTERS_TAB.pattern),
        ("dispatch", "option", "Paper boat")]


async def test_the_quote_reports_the_characters_it_was_sent_with(fake_ui):
    message = ["s", [[[["x"]]], None, None, [["c1"]]], ["projects/p"]]
    body = "f.req=" + json.dumps([None, json.dumps(message)])
    response = CapturedResponse("StreamChat", 200, stream(permission(cost=10)), body)
    ui = fake_ui({"StreamChat": [response]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 video."], AGENT_MESSAGE: ["q"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert turn.characters == ("c1",)
