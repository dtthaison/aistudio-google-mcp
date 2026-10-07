import pytest

from aistudio._browser.speech_page import (
    BACK,
    FAVORITE,
    MODEL_BUTTON,
    SPEAKER,
    SPEECH_URL,
    TEXT_BOX,
    VIEW_ALL,
    VOICE_SEARCH,
    SpeechPage,
    voice_display_name,
)
from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse
from aistudio.exceptions import BrowserError, ValidationError

CURRENT_MODEL = "Gemini 3.8 Flash TTS gemini-3.8-flash-tts Flagship TTS model"
LITE = "gemini-3.8-flash-lite-tts"
LITE_OPTION = r"gemini\-3\.8\-flash\-lite\-tts(?![\w.-])"


def test_voice_display_name():
    assert voice_display_name(" tova ") == "Tova"
    assert voice_display_name("SADALTAGER") == "Sadaltager"


async def test_open_creates_a_dialog(fake_ui):
    ui = fake_ui()
    await SpeechPage(ui).open()
    assert ui.ops == [("goto", SPEECH_URL), ("click", "button", "Create new dialog", 0)]


async def test_select_model_is_a_no_op_when_already_current(fake_ui):
    ui = fake_ui(labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})
    await SpeechPage(ui).select_model("gemini-3.8-flash-tts")
    assert ui.ops == []


async def test_select_model_searches_and_clicks_the_option(fake_ui):
    ui = fake_ui(labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]},
                 visible={("button", LITE_OPTION): [True]})
    await SpeechPage(ui).select_model(LITE)
    assert ui.ops == [("click", "button", MODEL_BUTTON.pattern, 0),
                      ("fill", "textbox", "Search", LITE),
                      ("click", "button", LITE_OPTION, 0)]


async def test_unknown_model_is_a_validation_error_and_closes_the_picker(fake_ui):
    ui = fake_ui(labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})
    with pytest.raises(ValidationError) as info:
        await SpeechPage(ui).select_model("gemini-9-tts")
    assert ui.ops[-1] == ("key", "Escape")
    assert "models list" in info.value.hint


async def test_select_voice_normalizes_the_name_and_closes_the_panel(fake_ui):
    ui = fake_ui(labels={("button", SPEAKER.pattern): [["Speaker 1 - Fola arrow_drop_down"],
                                                       ["Speaker 1 - Tova arrow_drop_down"]]},
                 visible={("button", "Tova"): [True], ("textbox", VOICE_SEARCH): [True]})
    assert await SpeechPage(ui).select_voice("tova") == "Tova"
    assert ui.ops == [("click", "button", SPEAKER.pattern, 0),
                      ("fill", "textbox", VOICE_SEARCH, "Tova"),
                      ("click", "button", "Tova", 0),
                      ("key", "Escape")]


async def test_select_voice_is_a_no_op_when_already_current(fake_ui):
    ui = fake_ui(labels={("button", SPEAKER.pattern): [["Speaker 1 - Tova arrow_drop_down"]]})
    assert await SpeechPage(ui).select_voice("Tova") == "Tova"
    assert ui.ops == []


async def test_unknown_voice_is_a_validation_error(fake_ui):
    ui = fake_ui(labels={("button", SPEAKER.pattern): [["Speaker 1 - Fola arrow_drop_down"]]})
    with pytest.raises(ValidationError) as info:
        await SpeechPage(ui).select_voice("Nobody")
    assert info.value.hint == 'see "aistudio voices list"'
    assert ui.ops[-1] == ("key", "Escape")


async def test_a_voice_choice_that_does_not_apply_is_a_browser_error(fake_ui):
    ui = fake_ui(labels={("button", SPEAKER.pattern): [["Speaker 1 - Fola arrow_drop_down"]]},
                 visible={("button", "Tova"): [True]})
    with pytest.raises(BrowserError):
        await SpeechPage(ui).select_voice("Tova")


async def test_style_text_and_run(fake_ui):
    response = CapturedResponse("GenerateContent", 200, "[1]", "[2]")
    ui = fake_ui({"GenerateContent": [response]})
    page = SpeechPage(ui)
    await page.set_style("warm")
    await page.set_text("Hello <laugh>")
    assert await page.run() is response
    assert ui.ops == [("click", "button", "Style", 0),
                      ("fill", "textbox", "Describe the voice style", "warm"),
                      ("fill", "textbox", TEXT_BOX, "Hello <laugh>"),
                      ("press", "textbox", TEXT_BOX, RUN_SHORTCUT)]


async def test_run_without_a_response_is_a_browser_error(fake_ui):
    with pytest.raises(BrowserError) as info:
        await SpeechPage(fake_ui()).run(timeout_s=1)
    assert "no speech" in info.value.message


async def test_list_voices_walks_every_group(fake_ui):
    ui = fake_ui(labels={
        ("button", FAVORITE.pattern): [
            ["Favorite Bodi", "Remove from favorites Tova", "Favorite Fola"],
            ["Favorite Bodi", "Favorite Zephyr"],
            ["Favorite Sulafat"],
        ],
        ("button", VIEW_ALL.pattern): [["View all 7", "View all 5"]],
    })
    assert await SpeechPage(ui).list_voices() == ["Bodi", "Fola", "Sulafat", "Tova", "Zephyr"]
    clicks = [op for op in ui.ops if op[0] == "click"]
    assert clicks == [("click", "button", SPEAKER.pattern, 0),
                      ("click", "button", VIEW_ALL.pattern, 0),
                      ("click", "button", BACK.pattern, 0),
                      ("click", "button", VIEW_ALL.pattern, 1),
                      ("click", "button", BACK.pattern, 0)]
    assert ui.ops[-1] == ("key", "Escape")
