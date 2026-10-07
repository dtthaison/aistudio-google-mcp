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
from aistudio.exceptions import BotGuardError, BrowserError, SchemaDriftError, ValidationError

CURRENT_MODEL = "Gemini 3.8 Flash TTS gemini-3.8-flash-tts Flagship TTS model"
REFUSED = '[,[7,"The caller does not have permission"]]'
FIXTURE_TEXT = (
    "Good morning! It's 68 degrees and sunny outside. Traffic on the bridge"
    " is already backing up a bit, <short pause> so I'd suggest heading out"
    " ten minutes early for your nine o'clock meeting."
)
FIXTURE_STYLE = (
    'Bright, sunny, welcoming morning greeting with a subtle vocal smile '
    'and crisp pacing'
)


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
    result = await generate_speech(profile, FIXTURE_TEXT, style=FIXTURE_STYLE, out=out,
                                   client_factory=lambda p: make_client(),
                                   browser=browser_with(ui, opened))
    assert opened == ["minimized"]
    assert result == {
        "files": [{"path": str(out), "mime_type": "audio/wav", "seconds": 10.56}],
        "model": "gemini-3.8-flash-tts", "voice": "Fola", "style": FIXTURE_STYLE,
    }
    with wave.open(io.BytesIO(out.read_bytes())) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 253440)
    assert ("fill", "textbox", "Describe the voice style", FIXTURE_STYLE) in ui.ops


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


@pytest.mark.parametrize("model,hint", [
    ("gemini-3.5-flash", 'see "aistudio models list --category audio"'),
    ("lyria-3.5", 'see "aistudio models list --category audio"'),
])
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


@respx.mock
async def test_audio_for_other_text_is_never_written(logged_in_profile, make_client, fake_ui,
                                                     captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    out = tmp_path / "x.wav"
    with pytest.raises(BrowserError) as info:
        await generate_speech(profile, "Something else entirely", out=out,
                              client_factory=lambda p: make_client(),
                              browser=browser_with(speech_ui(fake_ui, captured), []))
    assert "did not match" in info.value.message
    assert not out.exists()


async def test_a_missing_output_folder_fails_before_generating(logged_in_profile, fake_ui,
                                                               tmp_path):
    opened = []
    with pytest.raises(ValidationError) as info:
        await generate_speech(logged_in_profile(), "Hello", out=tmp_path / "nope" / "x.wav",
                              browser=browser_with(fake_ui(), opened))
    assert "does not exist" in info.value.message
    assert opened == []


@respx.mock
async def test_a_corrupt_response_is_schema_drift(logged_in_profile, make_client, fake_ui,
                                                  captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    record = captured("GenerateContent", 3).raw
    ui = fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 200, "<html>",
                                                       record["request"]["body"])]},
                 labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})
    out = tmp_path / "x.wav"
    with pytest.raises(SchemaDriftError):
        await generate_speech(profile, FIXTURE_TEXT, out=out,
                              client_factory=lambda p: make_client(),
                              browser=browser_with(ui, []))
    assert not out.exists()


@respx.mock
async def test_a_file_created_meanwhile_is_not_overwritten(logged_in_profile, make_client,
                                                           fake_ui, captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    out = tmp_path / "late.wav"
    ui = speech_ui(fake_ui, captured)

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        yield ui
        out.write_bytes(b"someone else's file")  # appears during the generation

    with pytest.raises(ValidationError):
        await generate_speech(profile, FIXTURE_TEXT, out=out,
                              client_factory=lambda p: make_client(), browser=browser)
    assert out.read_bytes() == b"someone else's file"


@respx.mock
async def test_a_style_mismatch_keeps_the_audio_and_warns(logged_in_profile, make_client, fake_ui,
                                                          captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    out = tmp_path / "x.wav"
    result = await generate_speech(profile, FIXTURE_TEXT, style="calm", out=out,
                                   client_factory=lambda p: make_client(),
                                   browser=browser_with(speech_ui(fake_ui, captured), []))
    assert out.exists() and result["style"] == FIXTURE_STYLE
    assert result["warnings"] == ["AI Studio did not include the style 'calm' in the request"]


@respx.mock
async def test_a_text_mismatch_names_what_was_sent(logged_in_profile, make_client, fake_ui,
                                                   captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    with pytest.raises(BrowserError) as info:
        await generate_speech(profile, "Other words", out=tmp_path / "x.wav",
                              client_factory=lambda p: make_client(),
                              browser=browser_with(speech_ui(fake_ui, captured), []))
    assert "'Other words'" in info.value.message and "Good morning!" in info.value.message


@respx.mock
async def test_a_style_sent_elsewhere_in_the_request_counts(logged_in_profile, make_client,
                                                            fake_ui, captured, tmp_path):
    import json as _json

    from aistudio._rpc import wire as _wire

    profile = logged_in_profile()
    serve_catalog(captured)
    record = captured("GenerateContent", 3).raw
    request = _wire.decode(record["request"]["body"])
    request[1][0][0][0][40] = None  # not at the usual place …
    request[3].append(["speaker style", "calm and slow"])  # … but elsewhere
    response = CapturedResponse("GenerateContent", 200, record["response"]["body"],
                                _json.dumps(request))
    ui = fake_ui({"GenerateContent": [response]},
                 labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})
    result = await generate_speech(profile, FIXTURE_TEXT, style="calm and slow",
                                   out=tmp_path / "x.wav", client_factory=lambda p: make_client(),
                                   browser=browser_with(ui, []))
    assert result["style"] == "calm and slow" and "warnings" not in result
