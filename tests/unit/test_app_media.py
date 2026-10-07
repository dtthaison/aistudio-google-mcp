import contextlib
import io
import wave
from datetime import datetime

import httpx
import pytest
import respx

from aistudio._app.media import default_output, generate_speech, list_voices
from aistudio._browser.speech_page import FAVORITE, MODEL_BUTTON, VIEW_ALL
from aistudio._browser.ui import CapturedResponse
from aistudio._transport.http import method_url
from aistudio.exceptions import BotGuardError, ValidationError

CURRENT_MODEL = "Gemini 3.8 Flash TTS gemini-3.8-flash-tts Flagship TTS model"
REFUSED = '[,[7,"The caller does not have permission"]]'


def browser_with(ui, opened):
    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        opened.append(window)
        yield ui

    return browser


def serve_catalog(captured):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"]))


def speech_ui(fake_ui, captured, status=200):
    record = captured("GenerateContent", 3).raw
    body = record["response"]["body"] if status == 200 else REFUSED
    response = CapturedResponse("GenerateContent", status, body, record["request"]["body"])
    return fake_ui({"GenerateContent": [response]},
                   labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})


def test_default_output_name():
    assert str(default_output(datetime(2026, 10, 6, 14, 5, 9))) == (
        "aistudio-speech-20261006-140509.wav")


@respx.mock
async def test_generate_speech_writes_a_wav(logged_in_profile, make_client, fake_ui, captured,
                                            tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    ui, opened = speech_ui(fake_ui, captured), []
    out = tmp_path / "hello.wav"
    result = await generate_speech(profile, "Hello <laugh>", style="warm", out=out,
                                   client_factory=lambda p: make_client(),
                                   browser=browser_with(ui, opened))
    assert opened == ["minimized"]
    assert result == {
        "files": [{"path": str(out), "mime_type": "audio/wav", "seconds": 10.56}],
        "model": "gemini-3.8-flash-tts", "voice": "Fola", "style": "warm",
    }
    with wave.open(io.BytesIO(out.read_bytes())) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 253440)
    assert ("fill", "textbox", "Describe the voice style", "warm") in ui.ops


@respx.mock
async def test_refused_generation_writes_nothing(logged_in_profile, make_client, fake_ui,
                                                 captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    out = tmp_path / "x.wav"
    with pytest.raises(BotGuardError) as info:
        await generate_speech(profile, "Hello", out=out, client_factory=lambda p: make_client(),
                              browser=browser_with(speech_ui(fake_ui, captured, 403), []))
    assert "The caller does not have permission" in info.value.message
    assert "--visible" in info.value.hint
    assert not out.exists()


@pytest.mark.parametrize("model,hint", [("gemini-3.5-flash",
                                         'see "aistudio models list --category audio"'),
                                        ("lyria-3.5", 'use "aistudio generate music"')])
@respx.mock
async def test_non_tts_models_fail_before_any_browser_opens(logged_in_profile, make_client,
                                                           fake_ui, captured, tmp_path, model,
                                                           hint):
    profile = logged_in_profile()
    serve_catalog(captured)
    opened = []
    with pytest.raises(ValidationError) as info:
        await generate_speech(profile, "Hello", model=model, out=tmp_path / "x.wav",
                              client_factory=lambda p: make_client(),
                              browser=browser_with(fake_ui(), opened))
    assert info.value.hint == hint
    assert opened == []


async def test_existing_output_is_not_overwritten(logged_in_profile, fake_ui, tmp_path):
    out = tmp_path / "taken.wav"
    out.write_bytes(b"keep me")
    with pytest.raises(ValidationError) as info:
        await generate_speech(logged_in_profile(), "Hello", out=out,
                              browser=browser_with(fake_ui(), []))
    assert "--force" in info.value.hint
    assert out.read_bytes() == b"keep me"


async def test_empty_text_is_rejected(logged_in_profile, fake_ui, tmp_path):
    with pytest.raises(ValidationError):
        await generate_speech(logged_in_profile(), "  ", out=tmp_path / "x.wav",
                              browser=browser_with(fake_ui(), []))


async def test_list_voices_reads_headless_and_filters(logged_in_profile, fake_ui):
    ui = fake_ui(labels={("button", FAVORITE.pattern): [["Favorite Tova", "Favorite Fola"]],
                         ("button", VIEW_ALL.pattern): [[]]})
    opened = []
    result = await list_voices(logged_in_profile(), search="TO", browser=browser_with(ui, opened))
    assert opened == ["headless"]
    assert result == {"voices": ["Tova"], "count": 1}
