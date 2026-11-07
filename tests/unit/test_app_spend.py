import json
import os
import stat
from datetime import datetime, timezone

import pytest

from aistudio._app import spend
from aistudio.exceptions import ValidationError
from aistudio.paths import get_profile

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
VIDEO_DONE = {"status": "done", "credits_spent": 10, "credits_left": 90, "model": "veo-3.1-lite",
              "project": "p1", "files": [{"path": "a.mp4", "mime_type": "video/mp4", "bytes": 9,
                                          "media_id": "m1"}]}


def entry(path="flow video", params=None, result=None, error_code=None, profile="default"):
    return spend.build_entry(path, profile, params or {}, result, error_code, now=NOW)


def test_a_done_video_run_is_logged_with_what_was_measured():
    got = entry(params={"max_credits": 10, "model": None}, result=VIDEO_DONE)
    assert got == {
        "v": 1, "ts": "2026-11-07T12:00:00Z", "profile": "default", "command": "flow video",
        "unit": "flow_credits", "model": "veo-3.1-lite", "status": "done", "max_credits": 10,
        "credits_spent": 10, "files": ["a.mp4"], "media_ids": ["m1"], "project": "p1"}


def test_a_timeout_keeps_the_job_and_the_assumed_spend():
    got = entry(params={"max_credits": 10}, result={
        "status": "timeout", "media_ids": ["m1"], "project_url": "https://x/p1",
        "credits_spent": 10})
    assert got["status"] == "timeout" and got["credits_spent"] == 10
    assert got["media_ids"] == ["m1"] and got["url"] == "https://x/p1"


def test_a_failed_paid_run_is_logged_with_unknown_spend_and_the_budget_at_risk():
    got = entry(params={"max_credits": 20, "model": "veo-3.1-fast"}, error_code="SERVER_ERROR")
    assert got["status"] == "failed" and got["error_code"] == "SERVER_ERROR"
    assert got["credits_spent"] is None and got["max_credits"] == 20
    assert got["model"] == "veo-3.1-fast"


def test_a_quote_only_run_is_not_logged():
    assert entry(params={}, result={"status": "quoted", "credits": 10}) is None
    assert entry(path="flow music generate", params={},
                 result={"status": "quoted", "credits": 5}) is None


@pytest.mark.parametrize("code", ["VALIDATION_ERROR", "OVER_BUDGET", "PLAN_MISMATCH",
                                  "AUTH_ERROR", "NOT_FOUND", "RATE_LIMITED"])
def test_errors_that_spent_nothing_are_not_logged(code):
    assert entry(params={"max_credits": 10}, error_code=code) is None


def test_a_quote_run_that_errors_is_not_logged():
    assert entry(path="flow video", params={"max_credits": None}, error_code="TIMEOUT") is None


def test_a_free_image_run_is_logged_even_without_a_budget():
    got = entry(path="flow image", params={"max_credits": None}, error_code="TIMEOUT")
    assert got["status"] == "timeout"
    done = entry(path="flow image", params={}, result={**VIDEO_DONE, "credits_spent": 0})
    assert done["credits_spent"] == 0


def test_a_timeout_error_is_a_timeout_status():
    assert entry(params={"max_credits": 10}, error_code="TIMEOUT")["status"] == "timeout"
    assert entry(params={"max_credits": 10}, error_code="INTERRUPTED")["status"] == "failed"


def test_commands_that_never_spend_are_not_logged():
    assert entry(path="flow credits", result={"credits": 5}) is None
    assert entry(path="models list", result={}) is None
    assert entry(path="flow character create", params={"photo": "m1", "description": None},
                 result={"status": "done", "credits_spent": 0}) is None
    assert entry(path="flow character voice", params={"style": None},
                 result={"credits_spent": 0}) is None
    assert entry(path="flow tool use", params={"allow_spend": False}, result={}) is None


def test_character_create_voice_style_and_tool_use_with_spend_are_logged():
    assert entry(path="flow character create", params={"description": "a robot", "photo": None},
                 result={"status": "done", "credits_spent": 0, "files": []}) is not None
    assert entry(path="flow character voice", params={"style": "calm"},
                 result={"credits_spent": 0}) is not None
    got = entry(path="flow tool use", params={"allow_spend": True},
                result={"id": "t1", "url": "https://x/t1", "may_spend_credits": True,
                        "credits_spent": 4, "files": [{"path": "o.png"}]})
    assert got["credits_spent"] == 4 and got["tool_id"] == "t1" and got["files"] == ["o.png"]
    free = entry(path="flow tool use", params={"allow_spend": True},
                 result={"id": "t1", "may_spend_credits": False})
    assert free is None


def test_ai_studio_runs_get_a_usd_estimate_from_the_catalog():
    got = entry(path="generate image", params={"model": "gemini-3.1-flash-lite-image"},
                result={"files": [{"path": "a.jpg", "mime_type": "image/jpeg", "bytes": 5}],
                        "model": "gemini-3.1-flash-lite-image"})
    assert got["unit"] == "usd_estimate" and got["usd_estimate"] == 0.034
    assert got["status"] == "done" and "credits_spent" not in got
    song = entry(path="generate music", params={"model": "lyria-3.5"},
                 result={"files": [], "model": "lyria-3.5"})
    assert song["usd_estimate"] == 0.08
    unknown = entry(path="transcribe", params={"model": "gemini-3.5-transcribe"},
                    result={"text": "secret words", "model": "gemini-3.5-transcribe"})
    assert unknown["usd_estimate"] is None
    assert "secret words" not in json.dumps(unknown)


def test_a_failed_ai_studio_run_has_no_usd_estimate():
    got = entry(path="generate image", params={"model": "gemini-3.1-flash-lite-image"},
                error_code="BROWSER_ERROR")
    assert got["status"] == "failed" and got["usd_estimate"] is None


def test_build_runs_keep_the_app_and_url():
    got = entry(path="app create", params={}, result={
        "app_id": "a1", "url": "https://aistudio.google.com/apps/a1", "title": "My secret idea",
        "status": "timeout", "files": ["index.html"]})
    assert got["app_id"] == "a1" and got["url"].endswith("/a1") and got["status"] == "timeout"
    assert "title" not in got and "secret" not in json.dumps(got)


def test_flow_music_is_counted_in_its_own_unit():
    got = entry(path="flow music generate", params={"max_credits": 5}, result={
        "status": "done", "clip_id": "c1", "title": "Morning", "model": "Lyria 3.5",
        "credits_spent": 5, "project_url": "https://www.flowmusic.app/session/s",
        "files": [{"path": "s.m4a", "media_id": "c1"}]})
    assert got["unit"] == "flow_music_credits" and got["media_ids"] == ["c1"]
    timeout = entry(path="flow music generate", params={"max_credits": 5}, result={
        "status": "timeout", "job_id": "j9", "credits_spent": None, "files": [],
        "project_url": "https://www.flowmusic.app/session"})
    assert timeout["job_id"] == "j9" and timeout["credits_spent"] is None


def test_no_prompt_text_or_message_reaches_the_entry():
    result = {**VIDEO_DONE, "prompt": "a very private prompt", "warnings": ["me@example.com"],
              "text": "words"}
    text = json.dumps(entry(params={"max_credits": 10, "prompt": "a very private prompt"},
                            result=result))
    assert "private" not in text and "example.com" not in text and "words" not in text


def test_append_and_read_round_trip_with_private_permissions(aistudio_home):
    profile = get_profile("work")
    first = entry(params={"max_credits": 10}, result=VIDEO_DONE, profile="work")
    second = {**first, "ts": "2026-11-07T13:00:00Z"}
    spend.append_entry(profile, first)
    spend.append_entry(profile, second)
    assert spend.read_entries(profile) == [first, second]
    assert stat.S_IMODE(os.stat(profile.spend_log).st_mode) == 0o600
    assert profile.spend_log.name == "spend.jsonl"


def test_read_skips_damaged_lines(aistudio_home):
    profile = get_profile("default").ensure()
    profile.spend_log.write_text('{"ts": "2026-11-07T10:00:00Z", "command": "a"}\nnot json\n[1]\n'
                                 '\n', encoding="utf-8")
    assert spend.read_entries(profile) == [{"ts": "2026-11-07T10:00:00Z", "command": "a"}]
    assert spend.read_entries(get_profile("nobody")) == []


@pytest.mark.parametrize("text, expected", [
    ("1h", "2026-11-07T11:00:00+00:00"), ("30m", "2026-11-07T11:30:00+00:00"),
    ("7d", "2026-10-31T12:00:00+00:00"), ("2w", "2026-10-24T12:00:00+00:00"),
])
def test_since_accepts_a_relative_span(text, expected):
    assert spend.parse_since(text, NOW).isoformat() == expected


def test_since_accepts_a_date_as_local_midnight():
    got = spend.parse_since("2026-11-01", NOW)
    assert got.astimezone().date().isoformat() == "2026-11-01"
    assert got.astimezone().hour == 0


@pytest.mark.parametrize("text", ["", "abc", "0d", "7x", "2026-13-40", "-1h"])
def test_since_rejects_nonsense(text):
    with pytest.raises(ValidationError):
        spend.parse_since(text, NOW)


def _write(profile, *entries):
    for e in entries:
        spend.append_entry(profile, e)


def test_report_filters_and_totals_per_unit(aistudio_home):
    profile = get_profile("default")
    _write(
        profile,
        {"ts": "2026-10-01T10:00:00Z", "command": "flow video", "unit": "flow_credits",
         "status": "done", "credits_spent": 100},
        {"ts": "2026-11-07T10:00:00Z", "command": "flow video", "unit": "flow_credits",
         "status": "done", "credits_spent": 10},
        {"ts": "2026-11-07T10:30:00Z", "command": "flow image", "unit": "flow_credits",
         "status": "failed", "credits_spent": None, "max_credits": 4},
        {"ts": "2026-11-07T11:00:00Z", "command": "flow music", "unit": "flow_music_credits",
         "status": "done", "credits_spent": 5},
        {"ts": "2026-11-07T11:10:00Z", "command": "generate image", "unit": "usd_estimate",
         "status": "done", "usd_estimate": 0.034},
        {"ts": "2026-11-07T11:20:00Z", "command": "transcribe", "unit": "usd_estimate",
         "status": "done", "usd_estimate": None},
        {"ts": "2026-11-07T11:30:00Z", "command": "generate music", "unit": "usd_estimate",
         "status": "failed", "usd_estimate": None},
    )
    report = spend.spend_report(profile, now=NOW)
    assert report["count"] == 7 and report["profile"] == "default"
    assert report["totals"] == {
        "flow_credits": {"runs": 3, "credits_spent": 110, "unverified_runs": 1},
        "flow_music_credits": {"runs": 1, "credits_spent": 5, "unverified_runs": 0},
        "usd_estimate": {"runs": 3, "usd": 0.034, "unverified_runs": 1, "unpriced_runs": 1},
    }
    recent = spend.spend_report(profile, since="1d", now=NOW)
    assert recent["count"] == 6 and recent["totals"]["flow_credits"]["credits_spent"] == 10
    assert recent["since"] == "2026-11-06T12:00:00Z"
    only = spend.spend_report(profile, command="flow", now=NOW)
    assert [e["command"] for e in only["entries"]] == [
        "flow video", "flow video", "flow image", "flow music"]
    assert spend.spend_report(profile, command="flow image", now=NOW)["count"] == 1


def test_report_of_nothing_is_empty_not_an_error(aistudio_home):
    report = spend.spend_report(get_profile("default"), now=NOW)
    assert report["entries"] == [] and report["count"] == 0
    assert report["totals"]["flow_credits"] == {"runs": 0, "credits_spent": 0,
                                                "unverified_runs": 0}
