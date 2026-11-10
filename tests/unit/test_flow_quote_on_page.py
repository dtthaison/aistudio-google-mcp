"""A quote read from Flow's permission card when its StreamChat reply never ends.

Live 2026-11-11 (profile ``second``, Omni 1.2 Flash): from about 04:00 every paid run ended with
"Flow's agent had not finished replying" while the page showed "Would you like me to kick off
this 1 video generation, costing 18 credits?" with Approve, Always approve in project and
Reject. aistudio read no reply: Flow's chat had moved to a WebChannel (see the section at the
end of this module), so there was no StreamChat reply to read.
"""

import contextlib
import json
from datetime import datetime
from urllib.parse import urlencode

import pytest

from aistudio._app.flow import PAGE_QUOTE_NOTE, flow_generate
from aistudio._flow.models import instruction
from aistudio._flow.page import (
    AGENT_MESSAGE,
    PAGE_QUOTE_GRACE_S,
    PERMISSION_LAST,
    PROMPT_EDITOR,
    Defaults,
    FlowPage,
)
from aistudio._flow.rows import ProjectContents, Quote
from aistudio.exceptions import (
    OverBudgetError,
    PlanMismatchError,
    WaitTimeoutError,
)
from tests.unit.test_flow_page import PID, Clock, chat, poll
from tests.unit.test_flow_rows import CREATED, permission, record, stream

PROMPT = "a red apple rolls across a table"
SENT = instruction("video", PROMPT, count=1, model_label="Omni 1.2 Flash", seconds=6,
                   aspect="16:9")


def asked(count=1, credits=18, kind="video"):
    """Flow's agent message with its permission card, as the page's text shows it (live)."""
    noun = f"{kind} generation" + ("s" if count != 1 else "")
    return ("Generating the Video arrow_forward_ios I'll generate 1 6-second video using Omni 1.2 "
            "Flash in 16:9 aspect ratio based on your prompt and attached reference images. "
            f"Would you like me to kick off this {count} {noun}, costing {credits} credits? "
            "check Approve check Always approve in project close Reject")


def card(count=1, credits=18, kind="video"):
    """The newest permission card's own text: the question and its options."""
    return "Would you like" + asked(count, credits, kind).split("Would you like", 1)[1]


def body(media=(), characters=()):
    message = ["sess", [[[["a prompt"]]], [[m] for m in media] or None, None,
                        [[c] for c in characters] or None], ["projects/p1"]]
    return urlencode({"f.req": json.dumps([None, json.dumps(message)])})


def page_ui(fake_ui, message=None, shown=None, *, approve=None, sent=None, prompt=SENT,
            channel=None, **kw):
    """A page whose StreamChat reply never ends: no response is scripted unless a test does,
    and the card is up. ``approve``: the Approve options per read (the first read is the chat
    before the message); by default none, then one waiting."""
    message = asked() if message is None else message
    shown = card() if shown is None else shown
    answers = [[], [True]] if approve is None else approve
    ui = fake_ui(texts={PROMPT_EDITOR: [prompt], AGENT_MESSAGE: [message],
                        PERMISSION_LAST: [shown]},
                 enabled={("radio", "Approve"): answers}, **kw)
    stream = ([] if channel is not None else [body()]) if sent is None else list(sent)
    ui.sent_bodies = {"StreamChat": stream, "channel": list(channel or [])}
    return ui


async def quote_of(ui, **kw):
    return await FlowPage(ui, now=Clock()).submit_for_quote(SENT, **kw)


async def test_an_open_stream_with_flows_card_up_gives_the_quote_from_the_page(fake_ui):
    ui = page_ui(fake_ui)
    turn = await quote_of(ui)
    assert turn.quote == Quote(1, "video", 18) and turn.quote_source == "page"
    assert turn.text == asked()  # the plan is read from Flow's message, as for a stream quote
    assert ui.fetch_bodies  # the reply is still fetched first: the page is only the fallback
    assert ("key", "Enter") in ui.ops
    assert not [op for op in ui.ops if op[0] == "activate"]  # reading never presses anything


async def test_the_card_must_stay_up_for_the_grace_period_before_it_is_trusted(fake_ui):
    # Flow's structured request ends the stream, so it normally lands first
    ui = page_ui(fake_ui)
    calls = []

    def clock():
        calls.append(1)
        return float(len(calls))

    turn = await FlowPage(ui, now=clock).submit_for_quote(SENT)
    assert turn.quote is not None
    assert len(calls) > PAGE_QUOTE_GRACE_S  # polled for the whole grace period, not once


async def test_the_attachments_come_from_the_request_that_was_sent(fake_ui):
    # the media and character checks of a stream quote need the request, not the reply
    ui = page_ui(fake_ui, sent=[body(media=("m1", "m2"), characters=("c1",))])
    turn = await quote_of(ui)
    assert turn.quote is not None and turn.media == ("m1", "m2") and turn.characters == ("c1",)


async def test_a_plural_and_an_image_question_are_read_too(fake_ui):
    two = await quote_of(page_ui(fake_ui, asked(2, 36), card(2, 36)))
    assert two.quote == Quote(2, "video", 36)
    image = await quote_of(page_ui(fake_ui, asked(1, "1,200", "image"),
                                   card(1, "1,200", "image")))
    assert image.quote == Quote(1, "image", 1200)
    one = await quote_of(page_ui(fake_ui, asked(1, 1).replace("credits?", "credit?"),
                                 card(1, 1).replace("credits?", "credit?")))
    assert one.quote == Quote(1, "video", 1)


async def test_the_prompt_echoed_in_the_editor_is_not_a_quote(fake_ui):
    # The typed prompt holds Flow's sentence, but no card is up: no Approve option exists, and
    # the agent's message does not hold it.
    echo = "Prompt: Would you like me to kick off this 1 video generation, costing 1 credits?"
    ui = page_ui(fake_ui, "Generating the Video", "", approve=[[]], prompt=echo)
    turn = await FlowPage(ui, now=Clock()).submit_for_quote(echo, timeout_s=30)
    assert turn.quote is None and turn.generating and not turn.answered


async def test_an_echo_in_the_agent_message_without_a_card_is_not_a_quote(fake_ui):
    # the agent repeats the prompt's sentence, but no request is waiting (no Approve option)
    ui = page_ui(fake_ui, asked(1, 1), "", approve=[[]])
    assert (await quote_of(ui, timeout_s=30)).quote is None


async def test_an_echoed_price_that_the_card_does_not_show_is_not_a_quote(fake_ui):
    # the message says 1 credit (an echo of the prompt); Flow's own card says 18
    ui = page_ui(fake_ui, asked(1, 1), card(1, 18))
    assert (await quote_of(ui, timeout_s=30)).quote is None


async def test_two_different_questions_in_the_message_are_no_quote(fake_ui):
    both = asked(1, 1) + " " + asked(1, 18)
    assert (await quote_of(page_ui(fake_ui, both, card(1, 18)), timeout_s=30)).quote is None


async def test_a_request_left_waiting_by_an_earlier_run_is_not_this_ones(fake_ui):
    # one Approve option was already waiting before the message and no new one showed
    ui = page_ui(fake_ui, approve=[[True]])
    assert (await quote_of(ui, timeout_s=30)).quote is None


async def test_two_waiting_options_are_no_quote(fake_ui):
    ui = page_ui(fake_ui, approve=[[], [True, True]])
    assert (await quote_of(ui, timeout_s=30)).quote is None


async def test_nothing_sent_means_no_page_quote(fake_ui):
    ui = page_ui(fake_ui, sent=())
    assert (await quote_of(ui, timeout_s=30)).quote is None


async def test_a_card_that_cannot_be_read_never_fails_the_wait(fake_ui):
    from aistudio.exceptions import BrowserError

    ui = page_ui(fake_ui)

    async def broken(role, name):
        raise BrowserError("the page did not show what aistudio expected in time")

    ui.enabled = broken
    turn = await quote_of(ui, timeout_s=30)
    assert turn.quote is None and not turn.answered


async def test_the_structured_request_is_preferred_when_it_arrives(fake_ui):
    # a reply read in full wins, and its price is never replaced by the page's
    ui = page_ui(fake_ui, asked(1, 99), card(1, 99),
                 responses={"StreamChat": [chat(stream(permission(cost=18)))]})
    turn = await quote_of(ui)
    assert turn.quote == Quote(1, "video", 18) and turn.quote_source == "stream"


async def test_the_structured_request_wins_even_after_the_page_showed_the_card(fake_ui):
    ui = page_ui(fake_ui, asked(1, 99), card(1, 99),
                 responses={"StreamChat": [None, None, chat(stream(permission(cost=18)))]})
    turn = await quote_of(ui)
    assert turn.quote == Quote(1, "video", 18) and turn.quote_source == "stream"


# The same runs through flow_generate: the spend guard must still decide, on the real page code.

class FlowPageUnderTest(FlowPage):
    """The real quote, Reject and Approve code on a FakeUi; only the project plumbing is stubbed."""

    async def credits(self):
        return 1050

    async def open_project(self, project_id):
        pass

    async def contents(self, project_id):
        return ProjectContents({}, [], {})

    async def read_defaults(self, kind):
        return Defaults("Omni 1.2 Flash", "16:9", 1, asks=True)

    async def new_session(self):
        pass


async def run_generate(logged_in_profile, tmp_path, ui, *, max_credits, prompt=PROMPT):
    async def downloader(url):
        return b"\x00\x00\x00\x18ftypmp4"

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        yield ui

    return await flow_generate(
        logged_in_profile(), "video", prompt, model="omni-1.2-flash", duration=6,
        aspect="16:9", max_credits=max_credits, project=PID, out=tmp_path / "a.mp4",
        browser=browser, downloader=downloader, now=lambda: datetime(2026, 11, 11, 4, 0, 0),
        page_factory=lambda u: FlowPageUnderTest(u, now=Clock(), clock=lambda: CREATED - 30))


async def test_a_page_quote_within_max_credits_is_approved_and_says_where_it_came_from(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, asked(), card(),
                     responses={"jwpduf": [poll("jwpduf", [None, 1032, [record(status=3)]])]})
    result = await run_generate(logged_in_profile, tmp_path, ui, max_credits=18)
    assert result["status"] == "done" and result["credits_spent"] == 18
    assert result["quote_source"] == "page" and PAGE_QUOTE_NOTE in result["warnings"]
    assert ("activate", "radio", "Approve", 0) in ui.ops
    # Only the normal Approve is ever pressed, never "Always approve in project"
    assert not [op for op in ui.ops if "Always" in str(op)]
    assert (tmp_path / "a.mp4").exists()


async def test_a_page_quote_over_max_credits_is_refused_and_rejected(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, asked(), card())
    with pytest.raises(OverBudgetError) as info:
        await run_generate(logged_in_profile, tmp_path, ui, max_credits=17)
    assert "18" in info.value.message and "--max-credits 18" in info.value.hint
    assert not [op for op in ui.ops if op[:3] == ("activate", "radio", "Approve")]
    assert ("activate", "radio", "Reject", 0) in ui.ops


async def test_a_page_quote_for_another_count_is_refused_and_rejected(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, asked(2, 36), card(2, 36))
    with pytest.raises(PlanMismatchError) as info:
        await run_generate(logged_in_profile, tmp_path, ui, max_credits=100)
    assert "count: expected 1, quoted 2" in info.value.message
    assert not [op for op in ui.ops if op[:3] == ("activate", "radio", "Approve")]
    assert ("activate", "radio", "Reject", 0) in ui.ops


async def test_a_page_quote_without_max_credits_is_only_reported(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, asked(), card())
    result = await run_generate(logged_in_profile, tmp_path, ui, max_credits=None)
    assert result["status"] == "quoted" and result["credits"] == 18
    assert result["quote_source"] == "page" and result["declined"]
    assert not [op for op in ui.ops if op[:3] == ("activate", "radio", "Approve")]


async def test_an_echoed_prompt_never_reaches_approve_through_flow_generate(
        fake_ui, logged_in_profile, tmp_path):
    echo = "Would you like me to kick off this 1 video generation, costing 1 credits?"
    sent = instruction("video", echo, count=1, model_label="Omni 1.2 Flash", seconds=6,
                       aspect="16:9")
    ui = page_ui(fake_ui, "Generating the Video", "", approve=[[]], prompt=sent)
    with pytest.raises(WaitTimeoutError):
        await run_generate(logged_in_profile, tmp_path, ui, max_credits=18, prompt=echo)
    assert not [op for op in ui.ops if op[:3] == ("activate", "radio", "Approve")]


async def test_a_structured_quote_is_not_marked_as_a_page_quote(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, asked(), card(),
                     responses={"StreamChat": [chat(stream(permission(cost=18)))],
                                "jwpduf": [poll("jwpduf", [None, 1032, [record(status=3)]])]})
    result = await run_generate(logged_in_profile, tmp_path, ui, max_credits=18)
    assert result["status"] == "done" and "quote_source" not in result
    assert PAGE_QUOTE_NOTE not in result.get("warnings", [])


# The card's option names, and the -vv log a failed run needs.

def test_the_option_names_take_an_icon_word_but_never_always_approve():
    # Live 2026-11-11: the card's text reads "check Approve check Always approve in project close
    # Reject"; an icon's name can come first in a control's accessible name.
    from aistudio._flow.page import APPROVE, REJECT

    for name in ("Approve", "check Approve"):
        assert APPROVE.search(name)
    for name in ("Always approve in project", "check Always approve in project", "Always approve",
                 "Approve all", "check_circle Always approve", "Reject", "check approve"):
        assert not APPROVE.search(name)
    for name in ("Reject", "close Reject"):
        assert REJECT.search(name)
    for name in ("Approve", "close Reject all", "Rejected"):
        assert not REJECT.search(name)


async def test_a_page_quote_run_logs_each_guard_with_what_it_saw(fake_ui, caplog):
    import logging

    ui = page_ui(fake_ui, labels={("radio", "approve|reject"): [["check Approve", "close Reject"]]})
    with caplog.at_level(logging.DEBUG, logger="aistudio._flow.page"):
        await quote_of(ui)
    text = caplog.text
    assert "the chat held 0 Approve option(s) before the message" in text
    assert ("PASS chat request sent in this run: 1 StreamChat request(s), 0 WebChannel POST(s) "
            "of which 0 carry a message") in text
    assert "PASS a new Approve option showed: before=0, now=1" in text
    assert "PASS exactly one Approve option enabled, the newest: enabled at [0] of 1" in text
    assert "PASS one question of Flow's shape in the newest agent message" in text
    assert "found [('1', 'video', '18')]" in text
    assert "PASS the permission card shows the same credits: " in text and "read 18" in text
    assert "QUOTE TAKEN" in text and "page quote: using Quote(count=1, kind='video'" in text
    assert "option labels the page shows: ['check Approve', 'close Reject']" in text


async def test_a_failed_guard_is_named_with_the_value_that_failed_it(fake_ui, caplog):
    import logging

    # no StreamChat request seen (an endpoint that moved), and a message with no question
    ui = page_ui(fake_ui, "Generating the Video", "", sent=())
    with caplog.at_level(logging.DEBUG, logger="aistudio._flow.page"):
        await quote_of(ui, timeout_s=30)
    text = caplog.text
    assert ("FAIL chat request sent in this run: 0 StreamChat request(s), 0 WebChannel POST(s) "
            "of which 0 carry a message") in text
    assert "FAIL one question of Flow's shape in the newest agent message" in text
    assert "found []" in text and "no quote" in text and "no quote: reply read=False" in text
    assert text.count("page quote probe") == 1  # logged once, not once per poll
    assert "PASS a new Approve option showed" in text  # every guard is read, not just the first


async def test_a_page_that_cannot_be_read_is_logged_too(fake_ui, caplog):
    import logging

    from aistudio.exceptions import BrowserError

    ui = page_ui(fake_ui)

    async def broken(role, name):
        raise BrowserError("the page did not show what aistudio expected in time")

    ui.enabled = broken
    with caplog.at_level(logging.DEBUG, logger="aistudio._flow.page"):
        await quote_of(ui, timeout_s=30)
    assert "could not count the Approve options before sending" in caplog.text
    assert "FAIL page read" in caplog.text


async def test_the_card_options_are_looked_up_by_the_tolerant_names(fake_ui):
    # approve and reject use the same names as the quote probe, so an icon word cannot hide them
    from aistudio._flow.page import APPROVE, REJECT

    seen = []
    ui = page_ui(fake_ui)
    real = ui.enabled

    async def spy(role, name):
        seen.append((role, name))
        return await real(role, name)

    ui.enabled = spy
    await quote_of(ui)
    assert ("radio", APPROVE) in seen
    seen.clear()
    await FlowPage(ui, now=Clock()).reject()
    assert ("radio", REJECT) in seen


# Flow's chat moved to a Google WebChannel on 2026-11-11: there is no StreamChat request, and the
# message is POSTed to .../flowCreationAgent/streamCreationAgentChat/channel, after an opening
# "count=0" (7 bytes). The body's inner shape is not confirmed.

M1 = "11111111-1111-4111-8111-111111111111"
M2 = "22222222-2222-4222-8222-222222222222"
C1 = "33333333-3333-4333-8333-333333333333"
OPENING = "count=0"


def channel_body(inner, *, ofs=0):
    return urlencode({"count": 1, "ofs": ofs, "req0___data__": json.dumps(inner)})


def unknown_shape(media=(M1, M2), characters=(C1,)):
    """A message whose layout aistudio has not seen: ids appear, but not in the old slots."""
    return [["agent", {"text": SENT, "refs": [{"media": {"id": m}} for m in media],
                       "who": [{"character": c} for c in characters]}, "projects/p1"]]


def old_shape(media=(M1, M2), characters=(C1,)):
    """The old StreamChat message layout: slot 1 holds the media, slot 3 the characters."""
    return ["sess", [[[["a prompt"]]], [[m] for m in media] or None, None,
                     [[c] for c in characters] or None], ["projects/p1"]]


async def channel_quote(fake_ui, posts, *, attached=(M1, M2), characters=(C1,), **kw):
    ui = page_ui(fake_ui, channel=posts, **kw)
    turn = await FlowPage(ui, now=Clock()).submit_for_quote(
        SENT, attached=attached, characters=characters, timeout_s=30)
    return ui, turn


async def test_a_webchannel_post_is_the_chat_request_and_its_ids_are_found_verbatim(fake_ui):
    ui, turn = await channel_quote(fake_ui, [OPENING, channel_body(unknown_shape())])
    assert turn.quote == Quote(1, "video", 18) and turn.quote_source == "page"
    assert turn.media == (M1, M2) and turn.characters == (C1,)
    assert ui.watched == ("flowCreationAgent/streamCreationAgentChat/channel",)


async def test_the_opening_count_0_post_is_not_a_chat_message(fake_ui):
    _, turn = await channel_quote(fake_ui, [OPENING])
    assert turn.quote is None


async def test_a_missing_ingredient_is_not_verified_so_the_caller_refuses(fake_ui):
    _, turn = await channel_quote(fake_ui, [OPENING, channel_body(unknown_shape(media=(M1,)))])
    assert turn.quote is not None and turn.media != (M1, M2) and turn.media == ()


async def test_ingredients_out_of_order_are_not_verified(fake_ui):
    _, turn = await channel_quote(fake_ui, [OPENING, channel_body(unknown_shape(media=(M2, M1)))])
    assert turn.media == ()


async def test_a_message_without_attachments_reports_none(fake_ui):
    _, turn = await channel_quote(fake_ui, [OPENING, channel_body(unknown_shape((), ()))],
                                  attached=(), characters=())
    assert turn.quote is not None and turn.media == () and turn.characters == ()


async def test_a_body_in_the_old_message_shape_is_read_by_its_slots(fake_ui):
    nested = [None, json.dumps(old_shape())]  # proto-JSON is often a string inside a list
    for inner in (old_shape(), nested):
        _, turn = await channel_quote(fake_ui, [OPENING, channel_body(inner)])
        assert turn.media == (M1, M2) and turn.characters == (C1,)
    from aistudio._flow.rows import channel_ids

    assert channel_ids(channel_body(old_shape()), 1) == [M1, M2]
    assert channel_ids(channel_body(nested), 3) == [C1]
    assert channel_ids(OPENING, 1) == [] and channel_ids("not form data", 1) == []


async def test_the_latest_message_post_is_the_one_checked(fake_ui):
    posts = [OPENING, channel_body(unknown_shape(media=(M1,))),
             channel_body(unknown_shape(), ofs=1)]
    _, turn = await channel_quote(fake_ui, posts)
    assert turn.media == (M1, M2)


async def test_a_streamchat_request_is_still_the_chat_request_on_the_old_path(fake_ui):
    ui = page_ui(fake_ui, sent=[body(media=(M1,), characters=(C1,))], channel=[OPENING])
    turn = await FlowPage(ui, now=Clock()).submit_for_quote(SENT, attached=(M1,),
                                                            characters=(C1,), timeout_s=30)
    assert turn.media == (M1,) and turn.characters == (C1,)  # read by the old slots


async def test_the_log_says_what_was_found_in_the_channel_body(fake_ui, caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="aistudio._flow.page"):
        await channel_quote(fake_ui, [OPENING, channel_body(unknown_shape(media=(M1,)))])
    text = caplog.text
    assert ("PASS chat request sent in this run: 0 StreamChat request(s), 2 WebChannel POST(s) "
            "of which 1 carry a message") in text
    assert "FAIL the attachments are in the chat request" in text
    assert f"expected ids not found in order (missing ['{M2}'])" in text
    ok = caplog.records.copy()
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="aistudio._flow.page"):
        await channel_quote(fake_ui, [OPENING, channel_body(unknown_shape())])
    assert "PASS the attachments are in the chat request" in caplog.text
    assert "each expected id appears verbatim, in order" in caplog.text and ok


class FlowPageWithIngredients(FlowPageUnderTest):
    async def contents(self, project_id):
        return ProjectContents({M1: "first", M2: "second"}, [], {})


async def run_with_ingredients(logged_in_profile, tmp_path, ui):
    async def downloader(url):
        return b"\x00\x00\x00\x18ftypmp4"

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        yield ui

    return await flow_generate(
        logged_in_profile(), "video", PROMPT, model="omni-1.2-flash", duration=6,
        aspect="16:9", max_credits=18, project=PID, out=tmp_path / "a.mp4", ingredients=[M1, M2],
        browser=browser, downloader=downloader, now=lambda: datetime(2026, 11, 11, 4, 0, 0),
        page_factory=lambda u: FlowPageWithIngredients(u, now=Clock(),
                                                       clock=lambda: CREATED - 30))


SENT_WITH_INGREDIENTS = instruction(
    "video", f"Use the attached images, in order, as: ingredient, ingredient. {PROMPT}", count=1,
    model_label="Omni 1.2 Flash", seconds=6, aspect="16:9")


async def test_a_run_with_two_ingredients_is_approved_through_the_webchannel(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, prompt=SENT_WITH_INGREDIENTS,
                 channel=[OPENING, channel_body(unknown_shape(characters=()))],
                 responses={"jwpduf": [poll("jwpduf", [None, 1032, [record(status=3)]])]})
    result = await run_with_ingredients(logged_in_profile, tmp_path, ui)
    assert result["status"] == "done" and result["quote_source"] == "page"
    assert ("activate", "radio", "Approve", 0) in ui.ops
    assert not [op for op in ui.ops if "Always" in str(op)]


async def test_a_channel_message_missing_an_ingredient_is_refused_and_rejected(
        fake_ui, logged_in_profile, tmp_path):
    ui = page_ui(fake_ui, prompt=SENT_WITH_INGREDIENTS,
                 channel=[OPENING, channel_body(unknown_shape(media=(M1,), characters=()))])
    with pytest.raises(PlanMismatchError) as info:
        await run_with_ingredients(logged_in_profile, tmp_path, ui)
    assert "carries media none" in info.value.message
    assert not [op for op in ui.ops if op[:3] == ("activate", "radio", "Approve")]
    assert ("activate", "radio", "Reject", 0) in ui.ops
