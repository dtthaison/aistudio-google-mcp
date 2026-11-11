"""Flow's current Extend (live 2026-11-11, findings "Omni Extend"): a bare "Extend" in the Add
clip menu, a length chip with a slider and Flow's own cost line, Omni 1.2 Flash."""

import contextlib
import json

import pytest

from aistudio._app.flow import STATE_FILE, flow_extend
from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import (
    EDITOR_LAST,
    EXTEND_POPUP,
    ExtendQuote,
    FlowOutcome,
    FlowPage,
)
from aistudio.exceptions import BrowserError, OverBudgetError, ValidationError
from tests.unit.test_app_flow_b import NOW, PID, VID, FakePage, media
from tests.unit.test_flow_page import PID as PAGE_PID
from tests.unit.test_flow_page import Clock, poll
from tests.unit.test_flow_rows import CREATED, batch, record

CHIP = "What happens next? add 4s pen_magic Omni 1.2 Flash Extend close arrow_forward"
POPUP = "Extend Length {s} Sec Generating will use {c} credits"
EDIT_URL = f"https://flow.google.com/project/{PAGE_PID}/edit/w1"


def omni_ui(fake_ui, *responses, popup=None, body=CHIP, visible=None, counts=0):
    popup = popup or POPUP.format(s=4, c=12)
    ui = fake_ui(dict(responses),
                 inner={("menuitem", "^Extend"): ["keyboard_double_arrow_right Extend"]},
                 texts={"body": [body], EXTEND_POPUP: [popup], EDITOR_LAST: ["she waves"]},
                 visible={("button", "Exit extend mode"): [True], **(visible or {})},
                 url=EDIT_URL)
    ui.counts = {EXTEND_POPUP: counts}
    return ui


# --- the page ---------------------------------------------------------------------------

async def test_a_bare_extend_in_the_menu_means_omni_extend(fake_ui):
    ui = omni_ui(fake_ui)
    assert await FlowPage(ui)._extend_menu_label() is None
    assert ("dispatch", "button", "Add clip") in ui.ops


async def test_the_old_veo_extend_menu_still_names_its_model(fake_ui):
    ui = omni_ui(fake_ui)
    ui._inner[("menuitem", "^Extend")] = ["keyboard_double_arrow_right Extend (Veo 3.1 - Lite)"]
    assert await FlowPage(ui)._extend_menu_label() == "Veo 3.1 - Lite"


async def test_an_unreadable_extend_item_is_refused(fake_ui):
    ui = omni_ui(fake_ui)
    ui._inner[("menuitem", "^Extend")] = ["Extend later"]
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui)._extend_menu_label()
    assert "no Extend" in info.value.message


async def test_the_quote_is_read_from_flows_own_cost_line(fake_ui):
    ui = omni_ui(fake_ui)
    quote = await FlowPage(ui).extend_quote(None)
    assert quote == ExtendQuote(4, 12, "Omni 1.2 Flash")
    assert ("dispatch", "menuitem", "^Extend") in ui.ops
    assert ("dispatch", "button", "Settings trigger") in ui.ops
    assert not any(op[0] == "press" for op in ui.ops)  # Flow's own length is kept
    assert ("key", "Escape") in ui.ops  # the pop-up is closed again
    assert not any(op[0] == "click" for op in ui.ops)  # Start is never pressed for a quote


async def test_the_slider_is_set_from_its_start_with_keys(fake_ui):
    ui = omni_ui(fake_ui, popup=POPUP.format(s=7, c=21))
    quote = await FlowPage(ui).extend_quote(7)
    assert quote.seconds == 7 and quote.credits == 21
    presses = [op[3] for op in ui.ops if op[0] == "press"]
    assert presses == ["Home"] + ["ArrowRight"] * 4
    assert all(op[1:3] == ("slider", "Extend Length") for op in ui.ops if op[0] == "press")


async def test_a_length_the_slider_did_not_take_is_refused(fake_ui):
    ui = omni_ui(fake_ui, popup=POPUP.format(s=4, c=12))
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).extend_quote(6)
    assert "reads 4 s, not 6 s" in info.value.message and "nothing was spent" in info.value.message


async def test_a_popup_with_no_cost_line_is_refused(fake_ui):
    ui = omni_ui(fake_ui, popup="Extend Length 4 Sec")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).extend_quote(None)
    assert "showed no cost" in info.value.message


async def test_a_popup_that_will_not_close_is_refused(fake_ui):
    ui = omni_ui(fake_ui, counts=1)
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).extend_quote(None)
    assert "would not close" in info.value.message


async def test_the_extend_box_must_open_before_anything_is_read(fake_ui):
    ui = omni_ui(fake_ui, body="Describe how to edit this video…")
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui).extend_quote(None)
    assert "did not open" in info.value.message
    assert not any(op[0] == "press" for op in ui.ops)


def started_reply(media_id="m2"):
    return CapturedResponse("fZytfe", 200, batch("fZytfe", [
        None, 1008, [["w2"]], [record(status=2, with_links=False, media_id=media_id)]]), "")


async def test_omni_extend_follows_the_clip_flows_start_reply_names(fake_ui):
    ui = omni_ui(fake_ui, ("fZytfe", [started_reply()]),
                 ("as29s", [poll("as29s", record(media_id="old")),
                            poll("as29s", record(media_id="m2"))]))
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.extend_omni("she waves", ExtendQuote(4, 12, "Omni 1.2 Flash"),
                                     timeout_s=60, known={"old"})
    assert [m.media_id for m in outcome.media] == ["m2"] and outcome.credits_left == 1008
    assert page.started and page.paid_media == ["m2"]
    assert ("type", EDITOR_LAST, "she waves") in ui.ops
    assert ("click", "button", "Start generation", -1) in ui.ops


async def test_omni_extend_follows_new_media_when_flow_sends_no_start_reply(fake_ui):
    # Flow's start call after the 2026-11-11 Extend change was not seen (Start was never pressed
    # in the free exploration), so the clip is found by what is new in the project.
    ui = omni_ui(fake_ui, ("as29s", [poll("as29s", record(media_id="old")),
                                     poll("as29s", record(media_id="m2"))]))
    page = FlowPage(ui, now=Clock(), clock=lambda: CREATED - 30)
    outcome = await page.extend_omni("she waves", ExtendQuote(4, 12, "Omni 1.2 Flash"),
                                     timeout_s=60, known={"old"})
    assert [m.media_id for m in outcome.media] == ["m2"]
    assert dict(ui.waits).get("fZytfe") is not None


async def test_omni_extend_does_not_press_start_when_the_cost_changed(fake_ui):
    ui = omni_ui(fake_ui, popup=POPUP.format(s=4, c=15))
    page = FlowPage(ui, now=Clock())
    with pytest.raises(BrowserError) as info:
        await page.extend_omni("she waves", ExtendQuote(4, 12, "Omni 1.2 Flash"), timeout_s=60,
                               known=set())
    assert "nothing was spent" in info.value.message
    assert not any(op[0] == "click" for op in ui.ops) and not page.started


async def test_omni_extend_does_not_press_start_outside_the_extend_box(fake_ui):
    ui = omni_ui(fake_ui, visible={("button", "Exit extend mode"): [False]})
    page = FlowPage(ui, now=Clock())
    with pytest.raises(BrowserError):
        await page.extend_omni("she waves", ExtendQuote(4, 12, "Omni 1.2 Flash"), timeout_s=60,
                               known=set())
    assert not any(op[0] == "click" for op in ui.ops) and not page.started


async def test_omni_extend_never_answers_flows_rights_question(fake_ui):
    ui = omni_ui(fake_ui, visible={("button", "I agree"): [True]})
    page = FlowPage(ui, now=Clock())
    with pytest.raises(BrowserError) as info:
        await page.extend_omni("she waves", ExtendQuote(4, 12, "Omni 1.2 Flash"), timeout_s=60,
                               known=set())
    assert "rights question" in info.value.message
    assert ("dispatch", "button", "I agree") not in ui.ops
    assert ("click", "button", "I agree", 0) not in ui.ops


async def test_omni_extend_fails_fast_on_a_refused_start(fake_ui):
    ui = omni_ui(fake_ui, ("fZytfe", [CapturedResponse("fZytfe", 500, "", "")]))
    with pytest.raises(BrowserError) as info:
        await FlowPage(ui, now=Clock()).extend_omni(
            "she waves", ExtendQuote(4, 12, "Omni 1.2 Flash"), timeout_s=600, known=set())
    assert "may still be charged" in info.value.hint


# --- the use case -----------------------------------------------------------------------

class OmniPage(FakePage):
    def __init__(self, ui, *, quote=None, start_error=None, **kw):
        super().__init__(ui, **{"label": None, **kw})
        self.quote = quote or ExtendQuote(4, 12, "Omni 1.2 Flash")
        self.start_error = start_error

    async def extend_quote(self, seconds):
        self.calls.append(("extend_quote", seconds))
        if seconds is None:
            return self.quote
        return ExtendQuote(seconds, seconds * 3, self.quote.model)

    async def extend_omni(self, prompt, quote, *, timeout_s, known):
        self.calls.append(("extend_omni", prompt, quote.credits, frozenset(known)))
        self.started, self.paid_media = True, ["m2"]
        if self.start_error:
            raise self.start_error
        return FlowOutcome([media("m2")], 1008, False)


def harness(**page_kw):
    pages = []

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        yield object()

    def factory(ui):
        pages.append(OmniPage(ui, **page_kw))
        return pages[-1]

    async def downloader(url):
        return b"\x00\x00\x00\x18ftypmp4"

    return {"browser": browser, "page_factory": factory, "downloader": downloader,
            "now": lambda: NOW}, pages


def store(profile):
    profile.ensure()
    (profile.root / STATE_FILE).write_text(json.dumps({"project_id": PID}))


async def test_without_a_budget_extend_only_quotes(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages = harness()
    result = await flow_extend(profile, VID, "she waves", out=tmp_path / "x.mp4", **kw)
    assert result == {"status": "quoted", "credits": 12, "seconds": 4, "model": "omni-1.2-flash",
                      "quote_source": "page", "source": VID, "project": PID}
    assert not any(c[0] == "extend_omni" for c in pages[0].calls)
    assert not (tmp_path / "x.mp4").exists()


async def test_a_quote_over_the_budget_is_refused_before_start(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages = harness()
    with pytest.raises(OverBudgetError) as info:
        await flow_extend(profile, VID, "she waves", max_credits=11, out=tmp_path / "x.mp4", **kw)
    assert "12 credits" in info.value.message and "--max-credits 11" in info.value.message
    assert not any(c[0] == "extend_omni" for c in pages[0].calls)


async def test_an_extend_within_budget_runs_and_reports_the_page_quote(logged_in_profile,
                                                                       tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages = harness(credits=(1030, 1018))
    result = await flow_extend(profile, VID, "she  waves", max_credits=12, duration=4,
                               out=tmp_path / "x.mp4", **kw)
    assert result["status"] == "done" and result["credits_spent"] == 12
    assert result["quote_source"] == "page" and result["quote_credits"] == 12
    assert result["seconds"] == 4 and result["model"] == "omni-1.2-flash"
    assert not any("credits" in w for w in result.get("warnings", []))
    assert (tmp_path / "x.mp4").exists() and result["files"][0]["media_id"] == "m2"
    assert ("extend_quote", 4) in pages[0].calls
    run = next(c for c in pages[0].calls if c[0] == "extend_omni")
    assert run[1] == "she waves" and VID in run[3]  # the source is not taken for the new clip


async def test_a_charge_different_from_flows_quote_is_a_warning(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, _ = harness(credits=(1030, 1020))
    result = await flow_extend(profile, VID, "she waves", max_credits=12,
                               out=tmp_path / "x.mp4", **kw)
    assert result["warnings"][0] == "credits fell by 10 during this run; Flow's quote says 12"


async def test_an_omni_extend_that_may_have_started_is_reported_as_spent(logged_in_profile,
                                                                         tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, _ = harness(start_error=BrowserError("Flow did not start the extension"))
    with pytest.raises(BrowserError) as info:
        await flow_extend(profile, VID, "she waves", max_credits=12, out=tmp_path / "x.mp4", **kw)
    assert "m2" in (info.value.hint or "") or "m2" in info.value.message


async def test_duration_is_checked_before_chrome_opens(logged_in_profile, tmp_path):
    kw, pages = harness()
    with pytest.raises(ValidationError):
        await flow_extend(logged_in_profile(), VID, "x", duration=11, out=tmp_path / "x.mp4", **kw)
    assert pages == []


async def test_the_old_extend_has_no_length_to_choose(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages = harness(label="Veo 3.1 - Lite")
    with pytest.raises(ValidationError) as info:
        await flow_extend(profile, VID, "x", max_credits=10, duration=5, out=tmp_path / "x.mp4",
                          **kw)
    assert "no length to choose" in info.value.message
    assert not any(c[0] in ("extend", "extend_omni") for c in pages[0].calls)
