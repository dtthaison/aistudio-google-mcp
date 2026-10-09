import base64
import json
from datetime import datetime
from pathlib import Path

import pytest
import respx

from aistudio._app.media import generate_image, generate_music, transcribe
from aistudio._browser.media_pages import IMAGE_PROMPT, MUSIC_PROMPT
from aistudio._browser.ui import CapturedResponse
from aistudio.exceptions import BotGuardError, ValidationError
from tests.unit.test_app_media import browser_with, serve_catalog

NOW = datetime(2026, 10, 7, 10, 15, 0)
JPEG = b"\xff\xd8\xff\xe0 synthetic jpeg for tests \xff\xd9"
MP3 = b"ID3\x04\x00 synthetic mp3 for tests"
INVALID = '[3,"Request contains an invalid argument."]'


def media_ui(fake_ui, captured, n, status=200, body=None):
    raw = captured("GenerateContent", n).raw
    text = body if body is not None else raw["response"]["body"]
    return fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", status, text,
                                                         raw["request"]["body"])]})


def with_images(captured, *images):
    """Fixture 4 with its image part repeated for each payload."""
    response = json.loads(captured("GenerateContent", 4).raw["response"]["body"])
    first, finish = response[0]
    extra = []
    for data in images[1:]:
        chunk = json.loads(json.dumps(first))
        chunk[0][0][0][0][0][2][1] = base64.b64encode(data).decode()
        extra.append(chunk)
    return json.dumps([[first, *extra, finish]])


def run(fn, profile, prompt, ui, opened, make_client, **kw):
    return fn(profile, prompt, client_factory=lambda p: make_client(),
              browser=browser_with(ui, opened), now=lambda: NOW, **kw)


@respx.mock
async def test_generate_image_writes_the_jpeg(logged_in_profile, make_client, fake_ui, captured,
                                             tmp_path):
    serve_catalog(captured)
    ui, opened, out = media_ui(fake_ui, captured, 4), [], tmp_path / "apple.jpg"
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo", ui, opened, make_client,
                       out=out)
    assert opened == ["minimized"]
    assert result == {"files": [{"path": str(out), "mime_type": "image/jpeg",
                                 "bytes": len(JPEG)}],
                      "model": "gemini-3.1-flash-lite-image"}
    assert out.read_bytes() == JPEG
    assert ("fill", "textbox", IMAGE_PROMPT, "A red apple on a white table, studio photo") in ui.ops


@respx.mock
async def test_generate_music_writes_mp3_and_returns_the_text(logged_in_profile, make_client,
                                                             fake_ui, captured, tmp_path):
    serve_catalog(captured)
    ui, out = media_ui(fake_ui, captured, 5), tmp_path / "song.mp3"
    result = await run(generate_music, logged_in_profile(),
                       "A calm solo piano melody, slow tempo", ui, [], make_client, out=out)
    assert result == {"files": [{"path": str(out), "mime_type": "audio/mpeg", "bytes": len(MP3)}],
                      "model": "lyria-3-clip-preview", "text": "<instrumental>"}
    assert out.read_bytes() == MP3
    assert ("fill", "textbox", MUSIC_PROMPT, "A calm solo piano melody, slow tempo") in ui.ops


@respx.mock
async def test_default_names_use_the_returned_type(logged_in_profile, make_client, fake_ui,
                                                   captured, tmp_path, monkeypatch):
    serve_catalog(captured)
    monkeypatch.chdir(tmp_path)
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4), [], make_client)
    assert result["files"][0]["path"] == "aistudio-image-20261007-101500.jpg"
    assert (tmp_path / "aistudio-image-20261007-101500.jpg").read_bytes() == JPEG


@respx.mock
async def test_an_extension_mismatch_keeps_the_file_and_warns(logged_in_profile, make_client,
                                                              fake_ui, captured, tmp_path):
    serve_catalog(captured)
    out = tmp_path / "x.png"
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4), [], make_client, out=out)
    assert out.read_bytes() == JPEG
    assert result["warnings"] == [f"{out} holds image/jpeg data; its extension says otherwise"]


@respx.mock
async def test_extra_images_get_distinct_names(logged_in_profile, make_client, fake_ui, captured,
                                               tmp_path):
    serve_catalog(captured)
    (tmp_path / "x-2.jpg").write_bytes(b"keep me")
    body = with_images(captured, JPEG, JPEG + b"second")
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4, body=body), [], make_client,
                       out=tmp_path / "x.jpg")
    assert [f["path"] for f in result["files"]] == [str(tmp_path / "x.jpg"),
                                                    str(tmp_path / "x-3.jpg")]
    assert (tmp_path / "x-2.jpg").read_bytes() == b"keep me"
    assert (tmp_path / "x-3.jpg").read_bytes() == JPEG + b"second"


@pytest.mark.parametrize("fn,model,hint", [
    (generate_image, "lyria-3.5", 'use "aistudio generate music"'),
    (generate_music, "gemini-3.1-flash-lite-image", 'use "aistudio generate image"'),
    (generate_image, "gemini-3.5-flash", 'see "aistudio models list --category images"'),
])
@respx.mock
async def test_wrong_kind_of_model_fails_before_the_browser(logged_in_profile, make_client,
                                                            fake_ui, captured, tmp_path, fn,
                                                            model, hint):
    serve_catalog(captured)
    opened = []
    with pytest.raises(ValidationError) as info:
        await run(fn, logged_in_profile(), "anything", fake_ui(), opened, make_client,
                  model=model, out=tmp_path / "x.bin")
    assert info.value.hint == hint
    assert opened == []


@pytest.mark.parametrize("status,body,error,hint_word", [
    (403, '[,[7,"The caller does not have permission"]]', BotGuardError, "--visible"),
    (400, INVALID, ValidationError, "paid tier"),
])
@respx.mock
async def test_refused_and_invalid_are_mapped(logged_in_profile, make_client, fake_ui, captured,
                                              tmp_path, status, body, error, hint_word):
    serve_catalog(captured)
    out = tmp_path / "x.jpg"
    with pytest.raises(error) as info:
        await run(generate_image, logged_in_profile(), "A red apple on a white table, studio photo",
                  media_ui(fake_ui, captured, 4, status=status, body=body), [], make_client,
                  out=out)
    assert hint_word in info.value.hint
    assert not out.exists()


async def test_empty_prompt_and_missing_folder_fail_before_anything_opens(logged_in_profile,
                                                                         fake_ui, tmp_path):
    opened = []
    for prompt, out, word in [("  ", tmp_path / "x.jpg", "empty"),
                              ("an apple", tmp_path / "missing" / "x.jpg", "does not exist")]:
        with pytest.raises(ValidationError) as info:
            await generate_image(logged_in_profile(), prompt, out=out,
                                 browser=browser_with(fake_ui(), opened))
        assert word in info.value.message
    assert opened == []


async def test_existing_output_needs_force(logged_in_profile, fake_ui, tmp_path):
    out = tmp_path / "x.mp3"
    out.write_bytes(b"old")
    with pytest.raises(ValidationError) as info:
        await generate_music(logged_in_profile(), "piano", out=out,
                             browser=browser_with(fake_ui(), []))
    assert "--force" in info.value.hint
    assert out.read_bytes() == b"old"



@respx.mock
async def test_a_folder_without_hard_links_still_gets_the_file(logged_in_profile, make_client,
                                                               fake_ui, captured, tmp_path,
                                                               monkeypatch):
    # exFAT, SMB and cloud-synced folders refuse os.link (review #1).
    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("os.link", no_links)
    serve_catalog(captured)
    out = tmp_path / "apple.jpg"
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4), [], make_client, out=out)
    assert out.read_bytes() == JPEG and result["files"][0]["path"] == str(out)
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


@respx.mock
async def test_a_dangling_symlink_at_the_output_never_loses_the_result(
        logged_in_profile, make_client, fake_ui, captured, tmp_path):
    serve_catalog(captured)
    out = tmp_path / "x.jpg"
    out.symlink_to(tmp_path / "missing-target")  # exists() is False, but the name is taken
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4), [], make_client, out=out)
    saved = tmp_path / "x-2.jpg"
    assert saved.read_bytes() == JPEG and result["files"][0]["path"] == str(saved)
    assert f"saved to {saved} instead" in result["warnings"][0]


@respx.mock
async def test_an_unwritable_folder_rescues_the_paid_result(logged_in_profile, make_client,
                                                            fake_ui, captured, tmp_path,
                                                            monkeypatch):
    import tempfile

    rescue = tmp_path / "rescue"
    rescue.mkdir()
    folder = tmp_path / "out"
    folder.mkdir()
    real_mkstemp = tempfile.mkstemp

    def mkstemp(*args, dir=None, **kw):
        if dir is not None and Path(dir) == folder:
            raise PermissionError(13, "Permission denied")
        return real_mkstemp(*args, dir=dir, **kw)

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(rescue))
    serve_catalog(captured)
    with pytest.raises(ValidationError) as info:
        await run(generate_image, logged_in_profile(), "A red apple on a white table, studio photo",
                  media_ui(fake_ui, captured, 4), [], make_client, out=folder / "x.jpg")
    (saved,) = rescue.iterdir()
    assert saved.read_bytes() == JPEG
    assert str(saved) in info.value.hint


@respx.mock
async def test_a_prompt_mismatch_after_a_paid_run_keeps_the_file_and_warns(
        logged_in_profile, make_client, fake_ui, captured, tmp_path):
    serve_catalog(captured)
    raw = captured("GenerateContent", 4).raw
    request = raw["request"]["body"].replace("A red apple on a white table, studio photo",
                                             "A red apple on a white table")
    ui = fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 200,
                                                       raw["response"]["body"], request)]})
    out = tmp_path / "x.jpg"
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo", ui, [], make_client,
                       out=out)
    assert out.read_bytes() == JPEG
    assert "did not match" in result["warnings"][0]


@respx.mock
async def test_a_trailing_newline_from_a_prompt_file_is_not_sent(logged_in_profile, make_client,
                                                                 fake_ui, captured, tmp_path):
    serve_catalog(captured)
    ui = media_ui(fake_ui, captured, 4)
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo\n", ui, [], make_client,
                       out=tmp_path / "x.jpg")
    assert ("fill", "textbox", IMAGE_PROMPT, "A red apple on a white table, studio photo") in ui.ops
    assert "warnings" not in result


@respx.mock
async def test_extra_parts_take_their_own_extension(logged_in_profile, make_client, fake_ui,
                                                    captured, tmp_path):
    serve_catalog(captured)
    response = json.loads(captured("GenerateContent", 4).raw["response"]["body"])
    first, finish = response[0]
    png = json.loads(json.dumps(first))
    png[0][0][0][0][0][2] = ["image/png", base64.b64encode(b"\x89PNG second").decode()]
    body = json.dumps([[first, png, finish]])
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4, body=body), [], make_client,
                       out=tmp_path / "x.jpg")
    assert [Path(f["path"]).name for f in result["files"]] == ["x.jpg", "x-2.png"]


async def test_paid_tier_hint_only_for_invalid_or_quota():
    from aistudio._app.media import _refused

    assert "paid tier" in _refused(400, '[3,"Request contains an invalid argument."]',
                                   "image").hint
    assert _refused(500, '[13,"internal"]', "image").hint is None


async def test_on_start_runs_only_after_validation(logged_in_profile, fake_ui, tmp_path):
    started = []
    with pytest.raises(ValidationError):
        await generate_image(logged_in_profile(), "  ", out=tmp_path / "x.jpg",
                             browser=browser_with(fake_ui(), []),
                             on_start=lambda: started.append(1))
    assert started == []


@respx.mock
async def test_extra_parts_are_numbered_in_order(logged_in_profile, make_client, fake_ui,
                                                 captured, tmp_path):
    # Plan C review M6: jpg, png, jpg -> x.jpg, x-2.png, x-3.jpg.
    serve_catalog(captured)
    response = json.loads(captured("GenerateContent", 4).raw["response"]["body"])
    first, finish = response[0]
    png = json.loads(json.dumps(first))
    png[0][0][0][0][0][2] = ["image/png", base64.b64encode(b"\x89PNG second").decode()]
    body = json.dumps([[first, png, first, finish]])
    result = await run(generate_image, logged_in_profile(),
                       "A red apple on a white table, studio photo",
                       media_ui(fake_ui, captured, 4, body=body), [], make_client,
                       out=tmp_path / "x.jpg")
    assert [Path(f["path"]).name for f in result["files"]] == ["x.jpg", "x-2.png", "x-3.jpg"]


@respx.mock
async def test_on_start_runs_on_success(logged_in_profile, make_client, fake_ui, captured,
                                        tmp_path):
    serve_catalog(captured)
    started = []
    await run(generate_image, logged_in_profile(), "A red apple on a white table, studio photo",
              media_ui(fake_ui, captured, 4), [], make_client, out=tmp_path / "a.jpg",
              on_start=lambda: started.append(1))
    assert started == [1]


@respx.mock
async def test_an_unknown_model_fails_before_the_browser(logged_in_profile, make_client, fake_ui,
                                                        captured, tmp_path):
    from aistudio.exceptions import AIStudioError

    serve_catalog(captured)
    opened, started = [], []
    with pytest.raises(AIStudioError):
        await run(generate_image, logged_in_profile(), "x", media_ui(fake_ui, captured, 4),
                  opened, make_client, model="no-such-model", out=tmp_path / "a.jpg",
                  on_start=lambda: started.append(1))
    assert opened == [] and started == []



def png_file(tmp_path, name="in.png"):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n synthetic")
    return path


@respx.mock
async def test_generate_image_with_inputs(logged_in_profile, make_client, fake_ui, captured,
                                          tmp_path):
    # Plan I: each --image is attached (and shows its chip) before the prompt runs.
    from aistudio._browser.media_pages import FILE_INPUT, REMOVE_MEDIA

    serve_catalog(captured)
    raw = captured("GenerateContent", 4).raw
    ui = fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 200,
                                                       raw["response"]["body"],
                                                       raw["request"]["body"])]},
                 labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA] * 2]})
    first, second = png_file(tmp_path, "a.png"), png_file(tmp_path, "b.png")
    result = await run(generate_image, logged_in_profile(), "Make the red squares blue.", ui, [],
                       make_client, images=[first, second], out=tmp_path / "x.jpg")
    names = [op[0] for op in ui.ops]
    assert ("set_files", FILE_INPUT, (str(first), str(second))) in ui.ops
    assert names.index("set_files") < names.index("fill")
    assert result["files"][0]["path"] == str(tmp_path / "x.jpg")


@respx.mock
async def test_image_inputs_are_checked_before_the_browser(logged_in_profile, make_client,
                                                          fake_ui, captured, tmp_path):
    serve_catalog(captured)
    text = tmp_path / "notes.txt"
    text.write_text("x")
    for bad in (tmp_path / "missing.png", text):
        opened = []
        with pytest.raises(ValidationError):
            await run(generate_image, logged_in_profile(), "x", fake_ui(), opened, make_client,
                      images=[bad], out=tmp_path / "y.jpg")
        assert opened == []


def transcript_body(*texts, finish=1, plain=False):
    """Live shape: the transcript in slot 22 (``plain``: text only in slot 1, as a refusal)."""
    def chunk(parts, done=None):
        candidate = [[parts, "model"]] + ([done] if done is not None else [])
        return [[candidate], None, [9, 1, 10], None, None, None, None, "v1_id"]

    def part(t):
        return [None, t] if plain else [None, t] + [None] * 20 + [[t]]
    return json.dumps([[chunk([part(t)]) for t in texts] + [chunk([[None, ""]], finish)]])


SENT_AUDIO = json.dumps(["models/gemini-3.5-transcribe",
                         [[[[None, None, None, None, None, ["drive1"]]], "user"]]])


def transcribe_ui(fake_ui, body, sent=SENT_AUDIO):
    from aistudio._browser.media_pages import REMOVE_MEDIA

    return fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 200, body, sent)]},
                   labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]},
                   checks={("switch", "Word timestamps", 0): [False]})


@respx.mock
async def test_transcribe_writes_text(logged_in_profile, make_client, fake_ui, captured,
                                      tmp_path):
    serve_catalog(captured)
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF0000WAVEfmt synthetic")
    ui, opened = transcribe_ui(fake_ui, transcript_body("Xin chào ", "Yuri Handmade.")), []
    result = await transcribe(logged_in_profile(), audio, timestamps=True,
                              client_factory=lambda p: make_client(),
                              browser=browser_with(ui, opened), out=tmp_path / "t.txt")
    assert result["text"] == "Xin chào Yuri Handmade."
    assert result["model"] == "gemini-3.5-transcribe"
    assert (tmp_path / "t.txt").read_text(encoding="utf-8") == "Xin chào Yuri Handmade."
    assert "warnings" not in result
    assert ("activate", "switch", "Word timestamps", 0) in ui.ops and opened == ["minimized"]


@respx.mock
async def test_transcribe_checks_before_the_browser(logged_in_profile, make_client, fake_ui,
                                                    captured, tmp_path):
    from aistudio.exceptions import AIStudioError

    serve_catalog(captured)
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF")
    existing = tmp_path / "t.txt"
    existing.write_text("old")
    cases = [dict(audio=tmp_path / "missing.wav"), dict(audio=tmp_path / "talk.txt"),
             dict(audio=audio, out=existing), dict(audio=audio, model="gemini-3.8-flash-tts")]
    (tmp_path / "talk.txt").write_text("x")
    for case in cases:
        opened = []
        with pytest.raises(AIStudioError):
            await transcribe(logged_in_profile(), case.pop("audio"),
                             client_factory=lambda p: make_client(),
                             browser=browser_with(fake_ui(), opened), **case)
        assert opened == []


@respx.mock
async def test_vocabulary_turns_timestamps_off(logged_in_profile, make_client, fake_ui, captured,
                                               tmp_path):
    # Live: "custom_vocabulary is incompatible with word timestamps" (timestamps are on by
    # default on the page).
    from aistudio._browser.media_pages import REMOVE_MEDIA

    serve_catalog(captured)
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF")
    ui = fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 200,
                                                       transcript_body("ok"), "[]")]},
                 labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]},
                 checks={("switch", "Word timestamps", 0): [True],
                         ("switch", "Speaker labels", 0): [True]})
    await transcribe(logged_in_profile(), audio, vocabulary="Yuri Handmade",
                     client_factory=lambda p: make_client(), browser=browser_with(ui, []))
    assert ("activate", "switch", "Word timestamps", 0) in ui.ops
    assert ("activate", "switch", "Speaker labels", 0) in ui.ops  # live: "…with diarization"
    for clash in ({"timestamps": True}, {"diarize": True}):
        opened = []
        with pytest.raises(ValidationError):
            await transcribe(logged_in_profile(), audio, vocabulary="Yuri",
                             client_factory=lambda p: make_client(),
                             browser=browser_with(fake_ui(), opened), **clash)
        assert opened == []


@respx.mock
async def test_a_refused_option_gets_an_accurate_hint(logged_in_profile, make_client, fake_ui,
                                                      captured, tmp_path):
    from aistudio._browser.media_pages import REMOVE_MEDIA

    serve_catalog(captured)
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF")
    refused = '[3,"custom_vocabulary is incompatible with word timestamps."]'
    ui = fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 400, refused, "[]")]},
                 labels={("button", REMOVE_MEDIA): [[REMOVE_MEDIA]]})
    with pytest.raises(ValidationError) as info:
        await transcribe(logged_in_profile(), audio, client_factory=lambda p: make_client(),
                         browser=browser_with(ui, []))
    assert "paid tier" not in (info.value.hint or "")


def test_a_request_with_images_is_checked_by_its_text_part_and_file_count():
    # Live: the edit warned "sent None" because the first part is the image, not the text.
    from aistudio._app.media import _mismatches

    sent = ["models/m", [[[[None, None, None, None, None, ["drive1"]],
                           [None, None, None, None, None, ["drive2"]],
                           [None, "make it blue"]], "user"]]]
    assert _mismatches(sent, model_id="m", text="make it blue", voice=None) == []
    assert _mismatches(sent, model_id="m", text="make it blue", voice=None, files=2) == []
    assert _mismatches(sent, model_id="m", text="make it blue", voice=None, files=3) == [
        "files: expected 3, sent 2"]


def test_an_inline_file_counts_as_sent():
    # Live: a 5 s clip went inline ([mime, base64] in part slot 2), not as a Drive file id, and
    # the transcript warned "files: expected 1, sent 0".
    from aistudio._app.media import _mismatches

    sent = ["models/m", [[[[None, None, ["audio/aiff", "UklGRg=="]]], "user"]]]
    assert _mismatches(sent, model_id="m", text=None, voice=None, files=1) == []
    assert _mismatches(sent, model_id="m", text=None, voice=None, files=2) == [
        "files: expected 2, sent 1"]



def live_body(*segments, finish=1):
    def chunk(parts, done=None):
        candidate = [[parts, "model"]] + ([done] if done is not None else [])
        return [[candidate], None, [9, 1, 10], None, None, None, None, "v1_id"]
    parts = [[None, text] + [None] * 20 + [[text, speaker, words]]
             for text, speaker, words in segments]
    return json.dumps([[chunk(parts), chunk([[None, ""]], finish)]])


WORDS = [["Xin", [None, 100000000], [None, 400000000]], ["chào.", [None, 400000000], ["2"]]]


async def run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path, body, sent=SENT_AUDIO,
                         **kw):
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF")
    ui = transcribe_ui(fake_ui, body, sent)
    return await transcribe(logged_in_profile(), audio, client_factory=lambda p: make_client(),
                            browser=browser_with(ui, []), **kw)


@respx.mock
async def test_mp4_and_webm_audio_are_accepted(logged_in_profile, make_client, fake_ui,
                                               captured, tmp_path):
    serve_catalog(captured)
    for name in ("talk.mp4", "talk.webm"):
        audio = tmp_path / name
        audio.write_bytes(b"synthetic")
        ui = transcribe_ui(fake_ui, live_body(("Xin chào.", "spk:0", WORDS)))
        result = await transcribe(logged_in_profile(), audio,
                                  client_factory=lambda p: make_client(),
                                  browser=browser_with(ui, []))
        assert result["text"] == "Xin chào."


@respx.mock
async def test_large_mp4_and_webm_are_refused_before_the_browser(logged_in_profile, make_client,
                                                                 fake_ui, captured, tmp_path):
    # Final review I1: relabelled files go as one in-memory upload, which Playwright caps at
    # 50 MB; past it the attach failed after Chrome opened with a "page changed" error.
    from aistudio._browser.media_pages import RELABEL_MAX_BYTES

    serve_catalog(captured)
    audio = tmp_path / "meeting.webm"
    with audio.open("wb") as fh:
        fh.truncate(RELABEL_MAX_BYTES + 1)  # sparse: no 50 MB written
    opened = []
    with pytest.raises(ValidationError) as info:
        await transcribe(logged_in_profile(), audio, client_factory=lambda p: make_client(),
                         browser=browser_with(transcribe_ui(fake_ui, "[]"), opened))
    assert "50 MB" in info.value.message and ".m4a" in (info.value.hint or "")
    assert opened == []


@respx.mock
async def test_a_plain_text_reply_is_flagged(logged_in_profile, make_client, fake_ui, captured,
                                             tmp_path):
    serve_catalog(captured)
    result = await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path,
                                  transcript_body("I can't transcribe this.", plain=True))
    assert result["text"] == "I can't transcribe this."
    assert any("plain text, not a transcript" in w for w in result["warnings"])


@respx.mock
async def test_transcribe_returns_segments_and_a_timed_file(logged_in_profile, make_client,
                                                            fake_ui, captured, tmp_path):
    # Plan I review I1: with AI Studio's defaults the reply carries speakers and word times.
    serve_catalog(captured)
    result = await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path,
                                  live_body(("Xin chào.", "spk:0", WORDS)),
                                  out=tmp_path / "t.txt")
    assert result["segments"] == [{"speaker": "spk:0", "start": 0.1, "end": 2.0,
                                   "text": "Xin chào.",
                                   "words": [["Xin", 0.1, 0.4], ["chào.", 0.4, 2.0]]}]
    written = (tmp_path / "t.txt").read_text(encoding="utf-8")
    assert written == "[0:00.1 - 0:02.0] spk:0: Xin chào.\n"
    assert "warnings" not in result


@respx.mock
async def test_a_cut_off_transcript_is_kept_with_a_warning(logged_in_profile, make_client, fake_ui,
                                                           captured, tmp_path):
    # Plan I review I2: a long recording that reaches the output limit is paid for: keep it.
    serve_catalog(captured)
    result = await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path,
                                  live_body(("Xin chào.", None, []), finish=2))
    assert result["text"] == "Xin chào." and any("cut off" in w for w in result["warnings"])


@respx.mock
async def test_transcription_errors_say_what_happened(logged_in_profile, make_client, fake_ui,
                                                      captured, tmp_path):
    # Plan I review M3: not "the response shape changed" / "try a different prompt".
    from aistudio.exceptions import AIStudioError

    serve_catalog(captured)
    for body, words in ((live_body(finish=1), "no speech"),
                        (live_body(("x", None, []), finish=3), "stopped")):
        with pytest.raises(AIStudioError) as info:
            await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path, body)
        assert words in info.value.message and "re-capture" not in (info.value.hint or "")


@respx.mock
async def test_a_request_without_the_audio_is_flagged(logged_in_profile, make_client, fake_ui,
                                                      captured, tmp_path):
    # Plan I review M5.
    serve_catalog(captured)
    no_file = json.dumps(["models/gemini-3.5-transcribe", [[[[None, "x"]], "user"]]])
    result = await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path,
                                  live_body(("Xin chào.", None, [])), sent=no_file)
    assert any("files" in w for w in result["warnings"])


@respx.mock
async def test_transcribe_without_out_needs_no_writable_folder(logged_in_profile, make_client,
                                                               fake_ui, captured, tmp_path,
                                                               monkeypatch):
    # Plan I review M4.
    import aistudio._app.media as media_module

    def refuse(folder):
        raise ValidationError(f"{folder} is not writable")

    serve_catalog(captured)
    monkeypatch.setattr(media_module, "_check_folder", refuse)
    result = await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path,
                                  live_body(("Xin chào.", None, [])))
    assert result["text"] == "Xin chào."


def test_a_long_segment_is_split_into_timed_sentences():
    # Live: a 174 s single-speaker clip came back as one segment, so the -o file was one line.
    from aistudio._app.media import transcript_lines

    words = [["Đoạn", 0.0, 0.3], ["một.", 0.3, 0.9], ["Đoạn", 1.0, 1.3], ["hai", 1.3, 1.6],
             ["nhé!", 1.6, 2.4], ["Hết", 2.5, 2.9]]
    segment = {"speaker": "spk:0", "start": 0.0, "end": 2.9,
               "text": "Đoạn một. Đoạn hai nhé! Hết", "words": words}
    assert transcript_lines([segment]) == ["[0:00.0 - 0:00.9] spk:0: Đoạn một.",
                                           "[0:01.0 - 0:02.4] spk:0: Đoạn hai nhé!",
                                           "[0:02.5 - 0:02.9] spk:0: Hết"]


def test_segments_that_cannot_be_split_stay_whole():
    from aistudio._app.media import transcript_lines

    # Text without spaces (words do not line up with the text): one line, as before
    cjk = {"speaker": None, "start": 0.0, "end": 2.0, "text": "你好。谢谢。",
           "words": [["你好。", 0.0, 1.0], ["谢", 1.0, 1.5], ["谢。", 1.5, 2.0]]}
    assert transcript_lines([cjk]) == ["[0:00.0 - 0:02.0] 你好。谢谢。"]
    plain = {"speaker": None, "start": None, "end": None, "text": "Just text. More."}
    assert transcript_lines([plain]) == ["Just text. More."]
    # No sentence end for a long stretch: break every 40 words
    many = [[f"w{i}", float(i), i + 0.5] for i in range(90)]
    run_on = {"speaker": None, "start": 0.0, "end": 89.5,
              "text": " ".join(w for w, _, _ in many), "words": many}
    lines = transcript_lines([run_on])
    assert [len(line.split("] ", 1)[1].split()) for line in lines] == [40, 40, 10]
    assert lines[1].startswith("[0:40.0 - 1:19.5] ")


@respx.mock
async def test_the_timed_file_and_screen_show_one_line_per_sentence(logged_in_profile, make_client,
                                                                     fake_ui, captured, tmp_path):
    serve_catalog(captured)
    words = [["Xin", [None, 100000000], [None, 400000000]], ["chào.", [None, 400000000], ["1"]],
             ["Tạm", ["2"], ["2", 300000000]], ["biệt.", ["2", 300000000], ["3"]]]
    result = await run_transcribe(logged_in_profile, make_client, fake_ui, tmp_path,
                                  live_body(("Xin chào. Tạm biệt.", "spk:0", words)),
                                  out=tmp_path / "t.txt")
    assert (tmp_path / "t.txt").read_text(encoding="utf-8") == (
        "[0:00.1 - 0:01.0] spk:0: Xin chào.\n[0:02.0 - 0:03.0] spk:0: Tạm biệt.\n")
    assert len(result["segments"]) == 1  # the JSON keeps AI Studio's segments


def test_a_sentence_without_a_start_time_follows_the_previous_line():
    # Plan K review M2: it took the segment start and began before the line above it.
    from aistudio._app.media import transcript_lines

    words = [["One", 0.0, 1.0], ["two.", 1.0, 2.0], ["Three", None, None], ["four.", 11.0, 12.0]]
    segment = {"speaker": None, "start": 0.0, "end": 12.0, "text": "One two. Three four.",
               "words": words}
    assert transcript_lines([segment]) == ["[0:00.0 - 0:02.0] One two.",
                                           "[0:02.0 - 0:12.0] Three four."]


def test_other_scripts_sentence_ends_split_lines():
    # Plan K review M3: Hindi, Arabic and Urdu use spaces, so their words line up.
    from aistudio._app.media import transcript_lines

    words = [["नमस्ते।", 0.0, 1.0], ["धन्यवाद।", 1.0, 2.0], ["كيف", 2.0, 2.5], ["حالك؟", 2.5, 3.0]]
    segment = {"speaker": None, "start": 0.0, "end": 3.0, "text": "नमस्ते। धन्यवाद। كيف حالك؟",
               "words": words}
    assert len(transcript_lines([segment])) == 3
