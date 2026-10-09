import pytest

from aistudio._browser.media_pages import (
    CHAT_URL,
    FILE_INPUT,
    GENERATE,
    IMAGE_PROMPT,
    MUSIC_EDITOR,
    MUSIC_PROMPT,
    REMOVE_MEDIA,
    TRANSCRIBE,
    ImagePage,
    MusicPage,
    TranscribePage,
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
    assert "charged" in info.value.hint  # never invite a blind second paid run (review #6)



class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


async def test_image_page_attaches_every_file(fake_ui):
    # Plan I: every input image must show its "Remove media" chip before Run.
    ui = fake_ui(labels={("button", REMOVE_MEDIA): [[], [REMOVE_MEDIA], [REMOVE_MEDIA] * 2]})
    await ImagePage(ui, now=Clock()).attach(["/a.png", "/b.png"])
    assert ("set_files", FILE_INPUT, ("/a.png", "/b.png")) in ui.ops


async def test_a_file_ai_studio_never_takes_is_an_error_before_run(fake_ui):
    ui = fake_ui(labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]})
    with pytest.raises(BrowserError) as info:
        await ImagePage(ui, now=Clock()).attach(["/a.png", "/b.png"])
    assert "nothing was generated" in info.value.message


async def test_transcribe_page_sets_options_then_runs(fake_ui):
    ui = fake_ui({"GenerateContent": [RESPONSE]},
                 labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]},
                 checks={("switch", "Word timestamps", 0): [True],
                         ("switch", "Speaker labels", 0): [False],
                         ("switch", "Smart transcription", 0): [True]})
    page = TranscribePage(ui, now=Clock())
    await page.open("gemini-3.5-transcribe")
    await page.attach(["/talk.wav"])
    await page.options(timestamps=True, speakers=True, smart=False)
    await page.vocabulary("Yuri Handmade, amigurumi")
    assert await page.run() is RESPONSE
    pressed = [op for op in ui.ops if op[0] == "activate"]
    assert pressed == [("activate", "switch", "Speaker labels", 0),
                       ("activate", "switch", "Smart transcription", 0)]
    assert ("fill", "textbox", "Custom vocabulary", "Yuri Handmade, amigurumi") in ui.ops
    assert ("click", "button", TRANSCRIBE, 0) in ui.ops



async def test_attach_waits_until_the_upload_lets_the_page_run(fake_ui):
    # Live: the chip shows at once, but Run stays disabled until the Drive upload finishes.
    ui = fake_ui(labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]})
    await ImagePage(ui, now=Clock()).attach(["/a.png"])
    waits = [op for op in ui.ops if op[0] == "wait_enabled"]
    assert waits and waits[0][1] == "button"
    assert getattr(waits[0][2], "pattern", "").startswith("^Run")
    ui = fake_ui(labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]})
    await TranscribePage(ui, now=Clock()).attach(["/a.wav"])
    assert ("wait_enabled", "button", TRANSCRIBE) in ui.ops
