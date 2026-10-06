import pytest

from aistudio._browser.build_page import APPS_URL, PROMPT_BOX, BuildPage, app_url
from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse
from aistudio._rpc import wire
from aistudio.exceptions import BotGuardError, BrowserError

APP = "912435b3-26c1-41b4-ba22-058d464afceb"
RUN = "463f35e4-afb1-41fc-925f-8d550c1d0ffc"
UPDATE_RUN = "66ca59ad-237a-4446-b206-eca5cabac1c7"


def reply(method, captured_record, status=200):
    return CapturedResponse(method, status, captured_record.raw["response"]["body"], "")


def started(captured, n):
    return [reply("CodeAssistantOffline", captured("CodeAssistantOffline", n))]


def stream(captured, run_id=RUN, finished=True):
    response = captured("StreamCodeAssistantOfflineGeneration").response
    if not finished:
        response = [response[0][:-1]]
    text = wire.encode(response).replace(RUN, run_id)
    return CapturedResponse("StreamCodeAssistantOfflineGeneration", 200, text, "")


async def test_create_follows_the_run_to_completion(fake_ui, captured):
    ui = fake_ui({
        "CreateApplet": [reply("CreateApplet", captured("CreateApplet", 2))],
        "CodeAssistantOffline": started(captured, 2),
        "StreamCodeAssistantOfflineGeneration": [stream(captured, finished=False),
                                                 stream(captured)],
    })
    outcome = await BuildPage(ui).create("a counter", timeout_s=600)
    assert (outcome.app_id, outcome.run_id) == (APP, RUN)
    assert outcome.progress.done and outcome.progress.steps == 24
    assert ui.ops == [("goto", APPS_URL), ("fill", PROMPT_BOX, "a counter"),
                      ("press", PROMPT_BOX, RUN_SHORTCUT)]


async def test_prompt_ignores_streams_of_older_runs(fake_ui, captured):
    ui = fake_ui({
        "CodeAssistantOffline": started(captured, 3),
        "StreamCodeAssistantOfflineGeneration": [stream(captured, run_id=RUN),
                                                 stream(captured, run_id=UPDATE_RUN)],
    })
    outcome = await BuildPage(ui).prompt(APP, "make it blue", timeout_s=600)
    assert outcome.run_id == UPDATE_RUN and outcome.progress.run_id == UPDATE_RUN
    assert ui.ops[0] == ("goto", app_url(APP))


async def test_no_completed_stream_is_a_timeout_outcome(fake_ui, captured):
    ui = fake_ui({
        "CodeAssistantOffline": started(captured, 3),
        "StreamCodeAssistantOfflineGeneration": [stream(captured, run_id=RUN)],
    })
    outcome = await BuildPage(ui).prompt(APP, "make it blue", timeout_s=600)
    assert (outcome.run_id, outcome.progress) == (UPDATE_RUN, None)


async def test_a_refused_start_is_botguard_rejected(fake_ui):
    refused = CapturedResponse("CodeAssistantOffline", 403,
                               '[,[7,"The caller does not have permission"]]', "")
    ui = fake_ui({"CodeAssistantOffline": [refused]})
    with pytest.raises(BotGuardError) as info:
        await BuildPage(ui).prompt(APP, "x", timeout_s=60)
    assert "--visible" in info.value.hint


async def test_a_missing_create_response_is_a_browser_error(fake_ui):
    with pytest.raises(BrowserError) as info:
        await BuildPage(fake_ui()).create("a counter", timeout_s=60)
    assert "CreateApplet" in info.value.message


async def test_an_undecodable_stream_is_skipped(fake_ui, captured):
    ui = fake_ui({
        "CodeAssistantOffline": started(captured, 2),
        "StreamCodeAssistantOfflineGeneration": [
            CapturedResponse("StreamCodeAssistantOfflineGeneration", 200, "<html>", ""),
            stream(captured),
        ],
    })
    outcome = await BuildPage(ui).prompt(APP, "x", timeout_s=60)
    assert outcome.progress.done


async def test_prompt_waits_for_send_to_be_enabled_before_submitting(fake_ui, captured):
    ui = fake_ui({"CodeAssistantOffline": started(captured, 3),
                  "StreamCodeAssistantOfflineGeneration": [stream(captured, run_id=UPDATE_RUN)]})
    await BuildPage(ui).prompt(APP, "make it blue", timeout_s=60)
    assert ui.ops == [("goto", app_url(APP)), ("fill", PROMPT_BOX, "make it blue"),
                      ("wait_enabled", "button", "Send"), ("press", PROMPT_BOX, RUN_SHORTCUT)]
