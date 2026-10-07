import pytest

from aistudio._browser.media_pages import (
    CHAT_URL,
    GENERATE,
    IMAGE_PROMPT,
    MUSIC_EDITOR,
    MUSIC_PROMPT,
    ImagePage,
    MusicPage,
)
from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse
from aistudio.exceptions import BrowserError

RESPONSE = CapturedResponse("GenerateContent", 200, "[1]", "[2]")


async def test_image_page_opens_the_model_fills_and_runs(fake_ui):
    ui = fake_ui({"GenerateContent": [RESPONSE]})
    page = ImagePage(ui)
    await page.open("gemini-3.1-flash-lite-image")
    await page.set_prompt("an apple")
    assert await page.run() is RESPONSE
    assert ui.ops == [
        ("goto", "https://aistudio.google.com/prompts/new_chat?model=gemini-3.1-flash-lite-image"),
        ("fill", "textbox", IMAGE_PROMPT, "an apple"),
        ("press", "textbox", IMAGE_PROMPT, RUN_SHORTCUT),
    ]


async def test_music_page_opens_the_editor_and_clicks_generate(fake_ui):
    ui = fake_ui({"GenerateContent": [RESPONSE]})
    page = MusicPage(ui)
    await page.open("lyria-3-clip-preview")
    await page.set_prompt("calm piano")
    assert await page.run() is RESPONSE
    assert ui.ops == [
        ("goto", CHAT_URL.format(model="lyria-3-clip-preview")),
        ("click", "button", MUSIC_EDITOR.pattern, 0),
        ("fill", "textbox", MUSIC_PROMPT, "calm piano"),
        ("click", "button", GENERATE, 0),
    ]


def test_model_ids_are_url_quoted():
    assert CHAT_URL.format(model="x") == "https://aistudio.google.com/prompts/new_chat?model=x"


@pytest.mark.parametrize("page_class,kind", [(ImagePage, "image"), (MusicPage, "music")])
async def test_no_response_is_a_browser_error(fake_ui, page_class, kind):
    with pytest.raises(BrowserError) as info:
        await page_class(fake_ui()).run(timeout_s=1)
    assert kind in info.value.message
    assert "--visible" in info.value.hint
