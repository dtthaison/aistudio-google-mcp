import re

import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import (
    AGENT_MESSAGE,
    NEW_SESSION,
    PROMPT_EDITOR,
    TUNE,
    Defaults,
    FlowPage,
)
from aistudio._flow.rows import Quote
from aistudio.exceptions import NotFoundError
from tests.unit.test_flow_rows import MEDIA, batch, record

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
QUOTED = ("I'm going to generate an 8-second video using Veo 3.1 - Lite. Would you like me to "
          "kick off this 1 video generation, costing 10 credits? Approve Always approve Reject")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def poll(rpcid, payload):
    return CapturedResponse(rpcid, 200, batch(rpcid, payload), "")


async def test_open_project_waits_for_the_editor(fake_ui):
    ui = fake_ui()
    await FlowPage(ui).open_project(PID)
    assert ui.ops == [("goto", f"https://flow.google.com/project/{PID}"),
                      ("wait_for", PROMPT_EDITOR)]
    with pytest.raises(NotFoundError):
        await FlowPage(fake_ui(missing={PROMPT_EDITOR})).open_project(PID)


async def test_create_project_names_it_and_returns_its_id(fake_ui):
    ui = fake_ui(url=f"https://flow.google.com/project/{PID}")
    assert await FlowPage(ui).create_project("aistudio") == PID
    assert ("fill", "textbox", "Editable text", "aistudio") in ui.ops
    assert ("press", "textbox", "Editable text", "Enter") in ui.ops


async def test_read_defaults_for_video(fake_ui):
    ui = fake_ui(inner={("button", "Video generation default model"): ["Omni 1.1 Flash "
                                                                       "arrow_drop_down"]},
                 checks={("radio", r"(^|\s)9:16$", 1): [True], ("radio", r"^x2$", 1): [True]},
                 url=f"https://flow.google.com/project/{PID}")
    assert await FlowPage(ui).read_defaults("video") == Defaults("Omni 1.1 Flash", "9:16", 2)
    assert ui.ops[0] == ("activate", "button", TUNE, 0)


async def test_submit_types_into_the_editor_and_presses_enter(fake_ui):
    ui = fake_ui()
    page = FlowPage(ui)
    await page.new_session()
    await page.submit("Generate exactly 1 video. Prompt: x")
    assert ui.ops == [("activate", "button", NEW_SESSION, 0),
                      ("type", PROMPT_EDITOR, "Generate exactly 1 video. Prompt: x"),
                      ("key", "Enter")]


async def test_wait_for_quote_returns_the_quote_or_the_reply(fake_ui):
    ui = fake_ui(texts={AGENT_MESSAGE: ["", QUOTED]})
    turn = await FlowPage(ui, now=Clock()).wait_for_quote(timeout_s=10)
    assert turn.quote == Quote(1, "video", 10) and "Veo 3.1 - Lite" in turn.text
    ui = fake_ui(texts={AGENT_MESSAGE: ["What style would you like?"]})
    turn = await FlowPage(ui, now=Clock()).wait_for_quote(timeout_s=3)
    assert turn.quote is None and turn.text == "What style would you like?"


async def test_approve_presses_only_the_single_approve_option(fake_ui):
    done = record(status=3)
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])],
                  "as29s": [poll("as29s", done)]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock()).approve_and_follow(count=1, timeout_s=60)
    assert ("activate", "radio", "Approve", 0) in ui.ops
    assert [m.media_id for m in outcome.media] == [MEDIA]
    assert (outcome.credits_left, outcome.timed_out) == (1040, False)


async def test_a_late_running_poll_does_not_undo_done(fake_ui):
    ui = fake_ui({"as29s": [poll("as29s", record(status=3))],
                  "jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock()).approve_and_follow(count=1, timeout_s=60)
    assert not outcome.timed_out and outcome.media[0].status == 3


async def test_follow_times_out_with_what_it_saw(fake_ui):
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock()).approve_and_follow(count=1, timeout_s=5)
    assert outcome.timed_out and [m.media_id for m in outcome.media] == [MEDIA]


async def test_credits_from_the_account_menu(fake_ui):
    ui = fake_ui(texts={"body": ["Son 1,040 Google Flow credits Upgrade"]})
    assert await FlowPage(ui).credits() == 1040


async def test_apply_defaults_sets_model_aspect_count_and_checks_they_took(fake_ui):
    model_button = ("button", "Video generation default model")
    ui = fake_ui(inner={model_button: ["Omni 1.1 Flash arrow_drop_down",
                                       "Veo 3.1 - Lite arrow_drop_down"]},
                 checks={("radio", r"(^|\s)9:16$", 1): [True], ("radio", r"^x2$", 1): [True]},
                 url=f"https://flow.google.com/project/{PID}")
    await FlowPage(ui).apply_defaults("video", Defaults("Veo 3.1 - Lite", "9:16", 2))
    assert ("activate", "menuitem", re.escape("Veo 3.1 - Lite"), 0) in ui.ops
    assert ("activate", "radio", r"(^|\s)9:16$", 1) in ui.ops
    assert ("activate", "radio", r"^x2$", 1) in ui.ops


async def test_settings_that_do_not_take_effect_are_an_error(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(inner={("button", "Video generation default model"): ["Omni 1.1 Flash"]},
                 checks={("radio", r"(^|\s)16:9$", 1): [True], ("radio", r"^x1$", 1): [True]},
                 url=f"https://flow.google.com/project/{PID}")
    with pytest.raises(BrowserError):
        await FlowPage(ui).apply_defaults("video", Defaults("Veo 3.1 - Lite", "9:16", 2))


def test_picker_labels_keep_their_dashes_and_lose_icons():
    from aistudio._flow.page import _label

    assert _label("Veo 3.1 - Lite arrow_drop_down") == "Veo 3.1 - Lite"
    assert _label("🍌 Nano Banana 2.1 arrow_drop_down") == "Nano Banana 2.1"
