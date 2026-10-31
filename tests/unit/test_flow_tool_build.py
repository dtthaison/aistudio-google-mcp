import json

import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import FlowPage
from aistudio.exceptions import BrowserError, ServerError
from tests.conftest import FakeUi

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
TID = "5c0e7a12-3b4d-4e6f-8a9b-0c1d2e3f4a5b"
CREATE = f"https://flow.google.com/project/{PID}/create-tool"
BUILT = f"https://flow.google.com/project/{PID}/tool/{TID}?mode=EDIT"
EDITOR = "flow-rich-text-editor .ProseMirror"
TEXT = "Build a tool that turns an image into a colour chart"


def clock(step=2.0):
    now = [0.0]

    def tick():
        now[0] += step
        return now[0]

    return tick


def ui_for(stop, *, try_again=(False,), shown=TEXT, **extra):
    return FakeUi(url=BUILT, visible={("button", "Stop"): list(stop),
                                      ("button", "Try again"): list(try_again)},
                  texts={EDITOR: [shown]}, **extra)


async def test_a_build_ends_only_after_stop_has_gone():
    """Live: the page text stayed unchanged for 50 s of a 95 s build, so quiet text is not done."""
    ui = ui_for([False, True, True, True, True, False, False, False])
    got = await FlowPage(ui, now=clock()).build_tool(CREATE, TEXT, timeout_s=300)
    assert got.tool_id == TID and got.timed_out is False


async def test_a_build_never_reloads_the_page_after_sending():
    """Live: a reload at 50 s lost the whole build."""
    ui = ui_for([True, False, False, False])
    await FlowPage(ui, now=clock()).build_tool(CREATE, TEXT, timeout_s=300)
    sent = ui.ops.index(("activate", "button", "Start generation", 0))
    assert [op for op in ui.ops[sent:] if op[0] == "goto"] == []


async def test_flows_own_error_is_reported():
    ui = ui_for([False], try_again=[False, True])
    with pytest.raises(ServerError, match="Something went wrong") as caught:
        await FlowPage(ui, now=clock()).build_tool(CREATE, TEXT, timeout_s=300)
    assert "daily limit" in caught.value.hint


async def test_a_build_that_never_starts_is_an_error():
    ui = ui_for([False])
    with pytest.raises(ServerError, match="did not start"):
        await FlowPage(ui, now=clock()).build_tool(CREATE, TEXT, timeout_s=300)


async def test_a_build_still_running_at_the_limit_stays_untouched():
    ui = ui_for([True])
    got = await FlowPage(ui, now=clock(30.0)).build_tool(CREATE, TEXT, timeout_s=300)
    assert got.timed_out is True and got.tool_id == TID


async def test_nothing_is_sent_when_the_box_shows_a_cut_description():
    ui = ui_for([True], shown="Build a tool")
    with pytest.raises(BrowserError, match="nothing was sent"):
        await FlowPage(ui, now=clock()).build_tool(CREATE, TEXT, timeout_s=300)
    assert ("activate", "button", "Start generation", 0) not in ui.ops


async def test_line_breaks_are_flattened_because_enter_would_send():
    ui = ui_for([True, False, False, False], shown="one two three")
    await FlowPage(ui, now=clock()).build_tool(CREATE, "one\n\ntwo\n  three", timeout_s=300)
    assert ("type", EDITOR, "one two three") in ui.ops


def batch(rpc, payload):
    return "\n".join([")]}'", "", "9", json.dumps([["wrb.fr", rpc, json.dumps(payload)]])])


def listed(*rows):
    cells = []
    for tool_id, version in rows:
        row = [None] * 27
        row[0], row[2], row[5], row[12] = tool_id, "Chart", version, 1
        cells.append(row)
    return CapturedResponse("tRARke", 200, batch("tRARke", [cells]), "")


async def test_saved_tool_waits_for_the_list_to_catch_up():
    ui = FakeUi(responses={"tRARke": [listed(), listed((TID, "v1"))]})
    got = await FlowPage(ui).saved_tool(TID)
    assert got.version_id == "v1"


async def test_saved_tool_waits_for_a_new_version_after_an_edit():
    ui = FakeUi(responses={"tRARke": [listed((TID, "v1")), listed((TID, "v2"))]})
    got = await FlowPage(ui).saved_tool(TID, not_version="v1")
    assert got.version_id == "v2"


async def test_saved_tool_gives_up_with_none():
    ui = FakeUi(responses={"tRARke": [listed() for _ in range(20)]})
    assert await FlowPage(ui).saved_tool(TID) is None
