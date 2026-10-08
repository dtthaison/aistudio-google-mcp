import contextlib
import dataclasses
import json
from datetime import datetime

import pytest

from aistudio._app.flow import STATE_FILE, flow_credits, flow_generate
from aistudio._flow.page import AgentTurn, Defaults, FlowOutcome
from aistudio._flow.rows import FlowMedia, Quote
from aistudio.exceptions import (
    AgentReplyError,
    AIStudioError,
    BrowserError,
    NetworkError,
    NotFoundError,
    OverBudgetError,
    PlanMismatchError,
    ValidationError,
)

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
NOW = datetime(2026, 10, 7, 12, 0, 0)
TEXT = ("I'm going to generate an 8-second video using Veo 3.1 - Lite. Would you like me to kick "
        "off this 1 video generation, costing 10 credits?")
DONE = FlowMedia(media_id="m1", project_id=PID, prompt="an apple", model_key="veo_3_1_t2v_lite",
                 status=3, created=1791366665, seconds=8, size=3,
                 video_url="https://flow-content.google/video/m1?Signature=s",
                 image_url="https://flow-content.google/image/m1?Signature=s")


TURN = AgentTurn(TEXT, Quote(1, "video", 10))
OMNI = Defaults("Omni 1.1 Flash", "16:9", 1, asks=True)


class FakePage:
    """Scripted FlowPage: records calls; behaviour set per test."""

    def __init__(self, ui, *, turn=TURN, defaults=OMNI, outcome=None, missing=(),
                 restore_error=None, follow_error=None, reject_error=None, approve_error=None):
        self.calls, self.turn, self.defaults = [], turn, defaults
        self.reject_error, self.approve_error, self.started = reject_error, approve_error, False
        self.follow_error, self.paid_media = follow_error, []
        self.outcome = outcome or FlowOutcome([DONE], 1040, False)
        self.missing, self.restore_error = set(missing), restore_error

    async def open_project(self, pid):
        self.calls.append(("open", pid))
        if pid in self.missing:
            raise NotFoundError("gone")

    async def create_project(self, title):
        self.calls.append(("create", title))
        return PID

    async def read_defaults(self, kind):
        return self.defaults

    async def apply_defaults(self, kind, wanted):
        if wanted == self.defaults and self.restore_error:
            raise self.restore_error
        self.calls.append(("apply", wanted))

    async def new_session(self):
        self.calls.append(("session",))

    async def submit_for_quote(self, text, *, timeout_s=90.0):
        self.calls.append(("submit", text))
        return self.turn

    async def reject(self):
        self.calls.append(("reject",))
        if self.reject_error:
            raise self.reject_error

    async def approve_and_follow(self, *, count, credits, timeout_s, kind):
        if self.approve_error:  # refused before pressing Approve
            raise self.approve_error
        self.started = True
        self.calls.append(("approve", count))
        if self.follow_error:
            self.paid_media = ["m9"]
            raise self.follow_error
        return self.outcome

    async def credits(self):
        return 1040


def harness(**page_kw):
    pages, opened = [], []

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        opened.append(window)
        yield object()

    def factory(ui):
        pages.append(FakePage(ui, **page_kw))
        return pages[-1]

    async def downloader(url):
        return b"\x00\x00\x00\x18ftypmp4" if "/video/" in url else b"\x89PNG..."

    return {"browser": browser, "page_factory": factory, "downloader": downloader,
            "now": lambda: NOW}, pages, opened


def store(profile, pid):
    profile.ensure()
    (profile.root / STATE_FILE).write_text(json.dumps({"project_id": pid}))


async def test_quote_only_rejects_flows_request(logged_in_profile, tmp_path):
    # Plan C: a quote left waiting could later be approved in Flow with restored settings.
    kw, pages, opened = harness()
    result = await flow_generate(logged_in_profile(), "video", "an apple",
                                 model="veo-3.1-lite", duration=8, out=tmp_path / "a.mp4", **kw)
    assert result["status"] == "quoted" and result["credits"] == 10 and result["declined"]
    assert ("reject",) in pages[0].calls and ("approve", 1) not in pages[0].calls
    assert opened == ["minimized"]


async def test_a_quote_survives_a_failed_reject(logged_in_profile, tmp_path):
    # Final review I3: the free run still returns its quote, and says a request is waiting.
    kw, pages, _ = harness(reject_error=BrowserError("radio 'Reject' not found"))
    result = await flow_generate(logged_in_profile(), "video", "an apple",
                                 model="veo-3.1-lite", duration=8, out=tmp_path / "a.mp4", **kw)
    assert result["status"] == "quoted" and result["credits"] == 10
    assert result["declined"] is False
    assert any("still waiting" in w and "Reject" in w for w in result["warnings"])
    assert ("approve", 1) not in pages[0].calls


async def test_approved_video_is_downloaded_and_defaults_restored(logged_in_profile, tmp_path):
    kw, pages, _ = harness()
    out = tmp_path / "a.mp4"
    result = await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                                 duration=8, max_credits=10, out=out, **kw)
    assert result["status"] == "done" and result["credits_spent"] == 10
    assert result["credits_left"] == 1040 and result["model"] == "veo-3.1-lite"
    assert result["files"] == [{"path": str(out), "mime_type": "video/mp4", "bytes": 11,
                                "media_id": "m1"}]
    applied = [c[1] for c in pages[0].calls if c[0] == "apply"]
    assert applied == [Defaults("Veo 3.1 - Lite", "16:9", 1), Defaults("Omni 1.1 Flash", "16:9", 1)]
    submit = next(c[1] for c in pages[0].calls if c[0] == "submit")
    assert submit.startswith("Generate exactly 1 8-second video with Veo 3.1 - Lite")


async def test_over_budget_rejects_and_spends_nothing(logged_in_profile, tmp_path):
    kw, pages, _ = harness()
    with pytest.raises(OverBudgetError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                            duration=8, max_credits=5, out=tmp_path / "a.mp4", **kw)
    assert "--max-credits 10" in info.value.hint and ("reject",) in pages[0].calls


async def test_plan_mismatch_rejects_and_spends_nothing(logged_in_profile, tmp_path):
    turn = AgentTurn(TEXT.replace("Lite", "Fast"), Quote(1, "video", 20))
    kw, pages, _ = harness(turn=turn)
    with pytest.raises(PlanMismatchError):
        await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                            max_credits=50, out=tmp_path / "a.mp4", **kw)
    assert ("reject",) in pages[0].calls and not any(c[0] == "approve" for c in pages[0].calls)


async def test_an_agent_question_is_an_agent_reply(logged_in_profile, tmp_path):
    kw, _, _ = harness(turn=AgentTurn("Which style would you like?", None))
    with pytest.raises(AgentReplyError) as info:
        await flow_generate(logged_in_profile(), "image", "an apple", max_credits=5,
                            out=tmp_path / "a.png", **kw)
    assert "Which style" in info.value.message


async def test_a_deleted_stored_project_is_replaced(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile, "11111111-1111-1111-1111-111111111111")
    kw, pages, _ = harness(missing={"11111111-1111-1111-1111-111111111111"})
    await flow_generate(profile, "video", "an apple", out=tmp_path / "a.mp4", **kw)
    assert ("create", "aistudio") in pages[0].calls
    assert json.loads((profile.root / STATE_FILE).read_text()) == {"project_id": PID}


async def test_restore_failure_after_a_paid_job_keeps_files_and_warns(logged_in_profile,
                                                                      tmp_path):
    kw, _, _ = harness(restore_error=BrowserError("tune changed"))
    result = await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                                 duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert (tmp_path / "a.mp4").exists()
    assert "could not restore Flow's video defaults (Omni 1.1 Flash, 16:9, x1)" in (
        result["warnings"][0])


async def test_several_outputs_need_a_folder(logged_in_profile, tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    kw, pages, opened = harness()
    with pytest.raises(ValidationError):
        await flow_generate(logged_in_profile(), "video", "an apple", count=2,
                            out=tmp_path / "a.mp4", **kw)
    assert opened == []


async def test_a_failed_download_names_the_media(logged_in_profile, tmp_path):
    kw, _, _ = harness()

    async def broken(url):
        raise NetworkError("reset")

    kw["downloader"] = broken
    with pytest.raises(NetworkError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                            duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert "m1" in info.value.hint and PID in info.value.hint


async def test_a_timeout_reports_the_media_and_spent_credits(logged_in_profile, tmp_path):
    late = FlowMedia(media_id="m1", project_id=PID, prompt=None, model_key=None, status=2,
                     created=None, seconds=None, size=None, video_url=None, image_url=None)
    kw, _, _ = harness(outcome=FlowOutcome([late], None, True))
    result = await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                                 duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert result == {"status": "timeout", "media_ids": ["m1"], "credits_spent": 10,
                      "project_url": f"https://flow.google.com/project/{PID}"}


async def test_image_defaults_name_files_by_their_bytes(logged_in_profile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kw, _, _ = harness(turn=AgentTurn("kick off this 1 image generation, costing 2 credits?",
                                      Quote(1, "image", 2)))
    result = await flow_generate(logged_in_profile(), "image", "an apple", max_credits=2, **kw)
    assert result["files"][0]["path"] == "flow-image-20261007-120000.png"


async def test_bad_inputs_fail_before_chrome(logged_in_profile, tmp_path):
    kw, _, opened = harness()
    for args in ({"prompt": "  "}, {"max_credits": -1}, {"project": "not-a-project"},
                 {"model": "nano-banana-pro"}, {"duration": 7}):
        prompt = args.pop("prompt", "an apple")
        with pytest.raises(ValidationError):
            await flow_generate(logged_in_profile(), "video", prompt, out=tmp_path / "a.mp4",
                                **args, **kw)
    assert opened == []


async def test_credits(logged_in_profile):
    kw, _, _ = harness()
    assert await flow_credits(logged_in_profile(), browser=kw["browser"],
                              page_factory=kw["page_factory"]) == {"credits": 1040}


async def test_a_different_length_after_the_job_is_a_warning(logged_in_profile, tmp_path):
    # Flow sends the length only after Approve (live: ask_for_permission has counts and cost).
    six = dataclasses.replace(DONE, seconds=6)
    kw, _, _ = harness(turn=AgentTurn("kick off this 1 video generation, costing 10 credits?",
                                      Quote(1, "video", 10)),
                       outcome=FlowOutcome([six], 1040, False))
    result = await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                                 duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert result["status"] == "done"
    assert result["warnings"] == ["Flow made a 6-second video, not the 8 seconds asked for"]


async def test_flow_set_to_spend_without_asking_is_refused_before_anything_is_sent(
        logged_in_profile, tmp_path):
    # Review C1: with "Confirm before generating: Never", even a quote-only run would spend.
    kw, pages, _ = harness(defaults=Defaults("Omni 1.1 Flash", "16:9", 1, asks=False))
    with pytest.raises(ValidationError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", out=tmp_path / "a.mp4",
                            **kw)
    assert "Confirm before generating" in info.value.hint
    assert not any(c[0] in ("submit", "apply", "approve") for c in pages[0].calls)


async def test_several_outputs_from_the_defaults_also_need_a_folder(logged_in_profile, tmp_path):
    # Review I4: the image default is x2 (live), so --count can come from Flow.
    kw, pages, _ = harness(defaults=Defaults("Nano Banana 2.1", "16:9", 2, asks=True))
    with pytest.raises(ValidationError):
        await flow_generate(logged_in_profile(), "image", "an apple", max_credits=4,
                            out=tmp_path / "a.png", **kw)
    assert not any(c[0] in ("submit", "approve") for c in pages[0].calls)


async def test_any_failure_after_approve_says_credits_were_spent(logged_in_profile, tmp_path):
    # Review I2: never invite a blind paid re-run.
    kw, _, _ = harness(follow_error=RuntimeError("Target page, context or browser has been closed"))
    with pytest.raises(AIStudioError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                            duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert "credits were spent" in info.value.hint and "m9" in info.value.hint
    assert "do not re-run" in info.value.hint


async def test_a_non_flow_error_while_restoring_is_only_a_warning(logged_in_profile, tmp_path):
    kw, _, _ = harness(restore_error=RuntimeError("raw playwright error"))
    result = await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                                 duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert (tmp_path / "a.mp4").exists() and "could not restore" in result["warnings"][0]


async def test_an_unexpected_download_error_still_names_the_media(logged_in_profile, tmp_path):
    kw, _, _ = harness()

    async def odd(url):
        raise RuntimeError("decoding failed")

    kw["downloader"] = odd
    with pytest.raises(AIStudioError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                            duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert "m1" in info.value.hint


async def test_credits_drift_hint_is_not_dev_capture(logged_in_profile):
    from aistudio.exceptions import SchemaDriftError

    class NoCredits(FakePage):
        async def credits(self):
            return None

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        yield object()

    with pytest.raises(SchemaDriftError) as info:
        await flow_credits(logged_in_profile(), browser=browser, page_factory=NoCredits)
    assert "dev capture" not in (info.value.hint or "")


async def test_flow_files_warn_on_an_extension_mismatch(logged_in_profile, tmp_path):
    kw, _, _ = harness(turn=AgentTurn("kick off this 1 image generation, costing 2 credits?",
                                      Quote(1, "image", 2)))

    async def jpeg(url):
        return b"\xff\xd8\xff\xe0 jpeg"

    kw["downloader"] = jpeg
    out = tmp_path / "x.png"
    result = await flow_generate(logged_in_profile(), "image", "an apple", max_credits=2,
                                 out=out, **kw)
    assert result["files"][0]["mime_type"] == "image/jpeg" and out.exists()
    assert f"{out} holds image/jpeg data; its extension says otherwise" in result["warnings"]


async def test_model_is_reported_as_the_cli_id(logged_in_profile, tmp_path):
    kw, _, _ = harness(turn=AgentTurn("kick off this 1 video generation, costing 10 credits?",
                                      Quote(1, "video", 10)))
    result = await flow_generate(logged_in_profile(), "video", "an apple", max_credits=10,
                                 out=tmp_path / "a.mp4", **kw)
    assert result["model"] == "omni-1.1-flash"


async def test_project_links_with_a_query_string(logged_in_profile, tmp_path):
    kw, pages, _ = harness()
    await flow_generate(logged_in_profile(), "video", "an apple",
                        project=f"https://flow.google.com/project/{PID}?pli=1#x",
                        out=tmp_path / "a.mp4", **kw)
    assert ("open", PID) in pages[0].calls


async def test_duration_is_checked_against_the_default_model(logged_in_profile, tmp_path):
    kw, pages, _ = harness(defaults=Defaults("Veo 3.1 - Quality", "16:9", 1, asks=True))
    with pytest.raises(ValidationError):
        await flow_generate(logged_in_profile(), "video", "an apple", duration=6,
                            out=tmp_path / "a.mp4", **kw)
    assert not any(c[0] == "submit" for c in pages[0].calls)


async def test_a_different_model_after_the_job_is_a_warning(logged_in_profile, tmp_path):
    kw, _, _ = harness(turn=AgentTurn("kick off this 1 video generation, costing 20 credits?",
                                      Quote(1, "video", 20)))
    result = await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-fast",
                                 max_credits=20, out=tmp_path / "a.mp4", **kw)
    assert "Flow made the video with veo-3.1-lite, not veo-3.1-fast" in result["warnings"]



async def test_a_refusal_before_approve_rejects_and_is_not_spent(logged_in_profile, tmp_path):
    # Live: "could not find exactly one Approve option; nothing was spent" came with a
    # "credits were spent" hint, and Flow's request was left waiting.
    kw, pages, _ = harness(approve_error=BrowserError(
        "could not find exactly one waiting Approve option; nothing was spent", hint="re-run"))
    with pytest.raises(BrowserError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", model="veo-3.1-lite",
                            duration=8, max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert info.value.hint == "re-run" and ("reject",) in pages[0].calls
