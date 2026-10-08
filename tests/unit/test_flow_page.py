import re

import pytest

from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import (
    AGENT_MESSAGE,
    NEW_SESSION,
    PERMISSION_LAST,
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


ASKING = "Would you like me to kick off this 1 video generation, costing 10 credits? Approve"

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
                 texts={PERMISSION_LAST: [ASKING]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, credits=10, timeout_s=60, kind="video")
    assert ("activate", "radio", "Approve", 0) in ui.ops
    assert [m.media_id for m in outcome.media] == [MEDIA]
    assert (outcome.credits_left, outcome.timed_out) == (1040, False)


async def test_a_late_running_poll_does_not_undo_done(fake_ui):
    ui = fake_ui({"as29s": [poll("as29s", record(status=3))],
                  "jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])]},
                 texts={PERMISSION_LAST: [ASKING]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, credits=10, timeout_s=60, kind="video")
    assert not outcome.timed_out and outcome.media[0].status == 3


async def test_follow_times_out_with_what_it_saw(fake_ui):
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [record(status=2, with_links=False)]])]},
                 texts={PERMISSION_LAST: [ASKING]})
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.approve_and_follow(count=1, credits=10, timeout_s=5, kind="video")
    assert outcome.timed_out and [m.media_id for m in outcome.media] == [MEDIA]
    assert page.paid_media == [MEDIA]


async def test_older_media_in_the_project_is_never_taken_for_this_job(fake_ui):
    old = "00000000-0000-0000-0000-000000000000"
    ui = fake_ui({"as29s": [poll("as29s", record(media_id=old, created=CREATED - 86_400)),
                            poll("as29s", record(status=3))],
                  "bOKtO": [poll("bOKtO", record(media_id=old, status=7,
                                                 created=CREATED - 86_400))]},
                 texts={PERMISSION_LAST: [ASKING]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, credits=10, timeout_s=60, kind="video")
    assert [m.media_id for m in outcome.media] == [MEDIA] and not outcome.timed_out


async def test_a_video_is_ready_only_with_its_video_link(fake_ui):
    poster_only = record(status=3)
    poster_only[5][11] = None  # no video link yet, only the poster image
    ui = fake_ui({"jwpduf": [poll("jwpduf", [None, 1040, [poster_only]])]},
                 texts={PERMISSION_LAST: [ASKING]})
    outcome = await FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30).approve_and_follow(
        count=1, credits=10, timeout_s=5, kind="video")
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


async def test_a_cut_prompt_is_not_sent(fake_ui):
    # Plan C review M1: 90% of the length is not the whole prompt.
    from aistudio.exceptions import BrowserError

    text = "Generate exactly 1 video of a red apple rolling slowly across a wooden kitchen table."
    ui = fake_ui(texts={PROMPT_EDITOR: [text[:-6]]})
    with pytest.raises(BrowserError):
        await FlowPage(ui, now=Clock()).submit_for_quote(text)
    assert ("key", "Enter") not in ui.ops



async def test_approve_presses_the_one_waiting_request(fake_ui):
    # Live: an older, answered request stays in the chat with disabled radios.
    from aistudio._browser.ui import CapturedResponse

    ui = fake_ui({"jwpduf": [CapturedResponse("jwpduf", 200, "", "")]},
                 enabled={("radio", "Approve"): [[False, True]]}, texts={PERMISSION_LAST: [ASKING]})
    page = FlowPage(ui, now=Clock(), clock=lambda: 0)
    await page.approve_and_follow(count=1, credits=10, timeout_s=2, kind="video")
    assert ("activate", "radio", "Approve", 1) in ui.ops and page.started


async def test_approve_refuses_two_waiting_requests(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = fake_ui(enabled={("radio", "Approve"): [[True, True]]})
    page = FlowPage(ui, now=Clock())
    with pytest.raises(BrowserError) as info:
        await page.approve_and_follow(count=1, credits=10, timeout_s=2, kind="video")
    assert "nothing was spent" in info.value.message and not page.started
    assert not any(op[:3] == ("activate", "radio", "Approve") for op in ui.ops)


async def test_reject_presses_every_waiting_request_and_only_those(fake_ui):
    ui = fake_ui(enabled={("radio", "Reject"): [[False, True, True], [False, True, True],
                                                [False, False, True], [False, False, False]]})
    await FlowPage(ui).reject()
    assert [op for op in ui.ops if op[0] == "activate"] == [
        ("activate", "radio", "Reject", 1), ("activate", "radio", "Reject", 2)]



async def test_approve_checks_the_cost_flow_shows(fake_ui):
    # Live: the agent asked twice in one run; the request still waiting can differ from the
    # quote that was checked, so the cost it shows must match before Approve is pressed.
    from aistudio.exceptions import PlanMismatchError

    ui = fake_ui(texts={PERMISSION_LAST: [ASKING.replace("10 credits", "20 credits")]})
    page = FlowPage(ui, now=Clock())
    with pytest.raises(PlanMismatchError) as info:
        await page.approve_and_follow(count=1, credits=10, timeout_s=5, kind="video")
    assert "20" in info.value.message and not page.started
    assert not any(op[:3] == ("activate", "radio", "Approve") for op in ui.ops)



async def test_a_turn_reports_what_flow_generated(fake_ui):
    # Plan F: free models generate at once, with no permission request (live).
    from tests.unit.test_flow_rows import generated_call, generated_result

    ui = fake_ui({"StreamChat": [chat(stream(generated_call(), generated_result()))]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 image."], AGENT_MESSAGE: ["done"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 image.")
    assert turn.quote is None and [g.media_id for g in turn.generated] == ["g1"]


async def test_a_permission_request_wins_over_generated_results(fake_ui):
    from tests.unit.test_flow_rows import generated_call, generated_result

    ui = fake_ui({"StreamChat": [chat(stream(generated_call(), generated_result(),
                                             permission(cost=10)))]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 video."], AGENT_MESSAGE: ["q"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert turn.quote is not None and turn.quote.credits == 10



async def test_reject_waits_for_the_request_and_says_how_many(fake_ui):
    # Plan F review I2: the permission message can render after the stream ends.
    ui = fake_ui(enabled={("radio", "Reject"): [[], [], [True], [True], [False]]})
    assert await FlowPage(ui).reject() == 1  # two polls, then it showed and was pressed
    ui = fake_ui(enabled={("radio", "Reject"): [[]]})
    assert await FlowPage(ui).reject() == 0
    ui = fake_ui(enabled={("radio", "Reject"): [[]]}, record_pauses=True)
    assert await FlowPage(ui).reject(expect=False) == 0
    assert len([op for op in ui.ops if op[0] == "pause"]) == 0


async def test_approve_presses_only_the_newest_request(fake_ui):
    # Plan F review M1: the cost read is the newest message's; the radio pressed must be too.
    from aistudio.exceptions import BrowserError

    ui = fake_ui(enabled={("radio", "Approve"): [[True, False]]},
                 texts={PERMISSION_LAST: [ASKING]})
    page = FlowPage(ui, now=Clock())
    with pytest.raises(BrowserError):
        await page.approve_and_follow(count=1, credits=10, timeout_s=5, kind="video")
    assert not page.started


async def test_the_last_permission_in_a_stream_is_the_quote(fake_ui):
    ui = fake_ui({"StreamChat": [chat(stream(permission(cost=10), permission(cost=20)))]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 video."], AGENT_MESSAGE: ["q"]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 video.")
    assert turn.quote.credits == 20


async def test_credits_wait_for_the_account_menu(fake_ui):
    # Live: one read found the menu still empty; the next run read 1,030.
    ui = fake_ui(texts={"body": ["", "Loading", "movie 1,030 Google Flow credits Upgrade"]})
    assert await FlowPage(ui).credits() == 1030
    ui = fake_ui(texts={"body": [""]})
    assert await FlowPage(ui).credits() is None



async def test_approve_never_takes_a_clip_made_before_the_run(fake_ui):
    # Two runs back to back: the first clip is inside the 120 s slack of the second.
    earlier = record(media_id="00000000-0000-0000-0000-00000000aaaa", created=CREATED - 60)
    ui = fake_ui({"as29s": [poll("as29s", earlier), poll("as29s", record(status=3))]},
                 texts={PERMISSION_LAST: [ASKING]})
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.approve_and_follow(count=1, credits=10, timeout_s=60, kind="video",
                                            known={"00000000-0000-0000-0000-00000000aaaa"})
    assert [m.media_id for m in outcome.media] == [MEDIA]



async def test_a_turn_notes_a_generation_still_running(fake_ui):
    # Plan F review M7: a call without a result means Flow is still making it (free).
    from tests.unit.test_flow_rows import generated_call

    ui = fake_ui({"StreamChat": [chat(stream(generated_call()))]},
                 texts={PROMPT_EDITOR: ["Generate exactly 1 image."], AGENT_MESSAGE: ["..."]})
    turn = await FlowPage(ui, now=Clock()).submit_for_quote("Generate exactly 1 image.")
    assert turn.quote is None and turn.generating
