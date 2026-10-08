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
from tests.unit.test_flow_rows import CREATED, MEDIA, batch, permission, record, stream

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


async def test_open_project_waits_for_the_editor_and_checks_the_url(fake_ui):
    ui = fake_ui(url=f"https://flow.google.com/project/{PID}")
    await FlowPage(ui).open_project(PID)
    assert ui.ops == [("goto", f"https://flow.google.com/project/{PID}"),
                      ("wait_for", PROMPT_EDITOR)]
    with pytest.raises(NotFoundError):
        await FlowPage(fake_ui(missing={PROMPT_EDITOR})).open_project(PID)


async def test_a_project_that_lands_elsewhere_is_not_found(fake_ui):
    # e.g. a deleted project redirecting to another page that also has the editor
    ui = fake_ui(url="https://flow.google.com/project/11111111-1111-1111-1111-111111111111")
    with pytest.raises(NotFoundError):
        await FlowPage(ui).open_project(PID)


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
    defaults = await FlowPage(ui).read_defaults("video")
    assert defaults == Defaults("Omni 1.1 Flash", "9:16", 2)
    assert defaults.asks is False  # not scripted as "Always": treated as not asking
    assert ui.ops[0] == ("activate", "button", TUNE, 0)


async def test_read_defaults_reports_whether_flow_asks_before_spending(fake_ui):
    ui = fake_ui(inner={("button", "Video generation default model"): ["Omni 1.1 Flash"]},
                 checks={("radio", r"(^|\s)16:9$", 1): [True], ("radio", r"^x1$", 1): [True],
                         ("radio", r"^Always\b.*ask for confirmation", 0): [True]},
                 url=f"https://flow.google.com/project/{PID}")
    assert (await FlowPage(ui).read_defaults("video")).asks is True


async def test_new_session(fake_ui):
    ui = fake_ui()
    await FlowPage(ui).new_session()
    assert ui.ops == [("activate", "button", NEW_SESSION, 0)]


def chat(text):
    return CapturedResponse("StreamChat", 200, text, "")


async def test_the_quote_comes_from_flows_structured_request(fake_ui):
    ui = fake_ui({"StreamChat": [chat(stream(permission(cost=10)))]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 video."],
                        AGENT_MESSAGE: ["Would you like me to kick off this 1 video generation, "
                                        "costing 1 credit? (echoed prompt text)"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert ui.ops == [("type", PROMPT_EDITOR, "Generate exactly 1 video."), ("key", "Enter")]
    assert turn.quote == Quote(1, "video", 10)  # never the text's "1 credit"


async def test_a_reply_without_a_permission_request_has_no_quote(fake_ui):
    ui = fake_ui({"StreamChat": [chat(stream([[None, [["c", None, None, ["open_chat_panel"]]]]]))]},
                 texts={PROMPT_EDITOR: ["x"], AGENT_MESSAGE: ["Which style would you like?"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("x", timeout_s=60)
    assert turn.quote is None and turn.text == "Which style would you like?"


async def test_approve_presses_only_the_single_approve_option(fake_ui):
    done = record(status=3)
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])],
                  "as29s": [poll("as29s", done)]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, timeout_s=60, kind="video")
    assert ("activate", "radio", "Approve", 0) in ui.ops
    assert [m.media_id for m in outcome.media] == [MEDIA]
    assert (outcome.credits_left, outcome.timed_out) == (1040, False)


async def test_a_late_running_poll_does_not_undo_done(fake_ui):
    ui = fake_ui({"as29s": [poll("as29s", record(status=3))],
                  "jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, timeout_s=60, kind="video")
    assert not outcome.timed_out and outcome.media[0].status == 3


async def test_follow_times_out_with_what_it_saw(fake_ui):
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.approve_and_follow(count=1, timeout_s=5, kind="video")
    assert outcome.timed_out and [m.media_id for m in outcome.media] == [MEDIA]
    assert page.paid_media == [MEDIA]


async def test_older_media_in_the_project_is_never_taken_for_this_job(fake_ui):
    old = "00000000-0000-0000-0000-000000000000"
    ui = fake_ui({"as29s": [poll("as29s", record(media_id=old, created=CREATED - 86_400)),
                            poll("as29s", record(status=3))],
                  "bOKtO": [poll("bOKtO", record(media_id=old, status=7,
                                                 created=CREATED - 86_400))]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, timeout_s=60, kind="video")
    assert [m.media_id for m in outcome.media] == [MEDIA] and not outcome.timed_out


async def test_a_video_is_ready_only_with_its_video_link(fake_ui):
    poster_only = record(status=3)
    poster_only[5][11] = None  # no video link yet, only the poster image
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [poster_only]])]},
                 labels={("radio", r"^Approve$"): [["Approve"]]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, timeout_s=5, kind="video")
    assert outcome.timed_out


async def test_credits_from_the_account_menu(fake_ui):
    ui = fake_ui(texts={"body": ["Son 1,040 Google Flow credits Upgrade"]})
    assert await FlowPage(ui).credits() == 1040


SAVED = CapturedResponse("Kcr7Ub", 200, ")]}'\n", "")


async def test_apply_defaults_sets_model_aspect_count_and_checks_they_took(fake_ui):
    model_button = ("button", "Video generation default model")
    ui = fake_ui({"Kcr7Ub": [SAVED]},
                 inner={model_button: ["Omni 1.1 Flash arrow_drop_down",
                                       "Veo 3.1 - Lite arrow_drop_down"]},
                 checks={("radio", r"(^|\s)9:16$", 1): [True], ("radio", r"^x2$", 1): [True]},
                 url=f"https://flow.google.com/project/{PID}")
    await FlowPage(ui).apply_defaults("video", Defaults("Veo 3.1 - Lite", "9:16", 2))
    assert ("activate", "menuitem", re.escape("Veo 3.1 - Lite"), 0) in ui.ops
    assert ("activate", "radio", r"(^|\s)9:16$", 1) in ui.ops
    assert ("activate", "radio", r"^x2$", 1) in ui.ops
    # Flow keeps tune changes only after Save (live: the save call is Kcr7Ub).
    assert ui.ops.index(("activate", "button", "Save", 0)) > ui.ops.index(
        ("activate", "radio", r"^x2$", 1))


async def test_settings_flow_never_saves_are_an_error(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(inner={("button", "Video generation default model"): ["Veo 3.1 - Lite"]},
                 url=f"https://flow.google.com/project/{PID}")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).apply_defaults("video", Defaults("Veo 3.1 - Lite",
                                                                         "16:9", 1))
    assert "save" in info.value.message


async def test_settings_that_do_not_take_effect_are_an_error(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui({"Kcr7Ub": [SAVED]},
                 inner={("button", "Video generation default model"): ["Omni 1.1 Flash"]},
                 checks={("radio", r"(^|\s)16:9$", 1): [True], ("radio", r"^x1$", 1): [True]},
                 url=f"https://flow.google.com/project/{PID}")
    with pytest.raises(BrowserError):
        await FlowPage(ui).apply_defaults("video", Defaults("Veo 3.1 - Lite", "9:16", 2))


def test_picker_labels_keep_their_dashes_and_lose_icons():
    from aistudio._flow.page import _label

    assert _label("Veo 3.1 - Lite arrow_drop_down") == "Veo 3.1 - Lite"
    assert _label("🍌 Nano Banana 2.1 arrow_drop_down") == "Nano Banana 2.1"


async def test_new_session_expands_a_compact_prompt_box_first(fake_ui):
    # Live: the box can be collapsed ("Expand"); "Start new session" appears only once expanded.
    ui = fake_ui(visible={("button", NEW_SESSION): [False], ("button", "Expand"): [True]})
    await FlowPage(ui).new_session()
    assert ui.ops == [("activate", "button", "Expand", 0),
                      ("activate", "button", NEW_SESSION, 0)]


async def test_enter_waits_until_the_editor_shows_the_prompt(fake_ui):
    # Live: a 2 KB prompt is inserted at once; Enter pressed immediately sent an empty box.
    ui = fake_ui({"StreamChat": [chat(stream(permission(cost=10)))]},
                 texts={PROMPT_EDITOR: ["", "", "Generate exactly 1 video."],
                        AGENT_MESSAGE: ["quote"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert turn.quote is not None and ("key", "Enter") in ui.ops


async def test_a_prompt_that_never_reaches_the_editor_is_not_sent(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(texts={PROMPT_EDITOR: [""]})
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert "nothing was sent" in info.value.message
    assert ("key", "Enter") not in ui.ops
