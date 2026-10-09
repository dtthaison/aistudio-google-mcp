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


def transcript_body(*texts, finish=1):
    def chunk(parts, done=None):
        candidate = [[parts, "model"]] + ([done] if done is not None else [])
        return [[candidate], None, [9, 1, 10], None, None, None, None, "v1_id"]
    return json.dumps([[chunk([[None, t]]) for t in texts] + [chunk([[None, ""]], finish)]])


def transcribe_ui(fake_ui, body):
    from aistudio._browser.media_pages import REMOVE_MEDIA

    return fake_ui({"GenerateContent": [CapturedResponse("GenerateContent", 200, body, "[]")]},
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
