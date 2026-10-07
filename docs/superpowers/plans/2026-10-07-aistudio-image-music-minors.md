# Image + Music Generation and Deferred Minors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aistudio generate image` and `aistudio generate music`, both page-driven with no API key, and fix the 22 deferred minor review findings that still apply.

**Architecture:** Both media commands follow `generate speech`. A minimized Chrome drives AI Studio's own page, the `GenerateContent` response is read from network events, and media parts are decoded and written without clobbering. Images use the chat page with a Gemini image model (`/prompts/new_chat?model=<id>`). Music uses the Lyria page reached through the same URL, which redirects to `/new_music?model=<id>`, and its "Generate" button. One shared decoder (`rows/media.py`) collects `[mime, base64]` parts, text parts and the finish reason. The minors are small, independent, test-first fixes.

**Tech Stack:** Python 3.10+, click, httpx, Playwright, pytest + pytest-asyncio, respx, ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` (§5.4 page driving, §6.2 model categories, §6.4 media). Wire evidence is in `docs/wire/findings.md`. Its "Image and music generation" section is added in Task 4.

## Global Constraints

- No feature may need a Gemini API key (user rule). Imagen (`/prompts/new_image`, 400 for this account) and Veo (`/prompts/new_video`, "Continue to upgrade") are out of scope. Say so in the skill.
- Generation runs in a **minimized** Chrome (`window="minimized"`). `--visible` shows it. Headless is refused by BotGuard.
- Never overwrite an existing file without `--force`. Validate output folders and inputs **before** a browser opens or anything is generated.
- Never print or log cookies, Authorization values, API keys or BotGuard tokens. Fixtures are synthetic or redacted.
- Errors use the existing hierarchy and codes (`ValidationError`, `BrowserError`, `BotGuardError`, `SchemaDriftError`, `ServerError`, `NotFoundError`, `AuthError`). The CLI exits 1 for handled errors.
- Defaults: image model `gemini-3.1-flash-lite-image`, music model `lyria-3-clip-preview`. Both are the cheapest and both were verified live on 2026-10-07.
- Real generation in tests is only in `tests/live` with marker `live_slow`. It is never run without the user's OK.
- ruff clean; the whole suite is green after every task.

## Review Focus

1. **The output extension does not match the returned mime** (`-o apple.png`, Google returns JPEG). The person expects the file where they asked, plus a clear warning, and never a second paid run or a lost file.
2. **A response with several images or several audio parts.** Every image is kept under a distinct non-clobbering name. Audio parts of one mime are joined in order.
3. **A model of the wrong kind** (`generate image -m lyria-3.5`, `generate music -m gemini-3.1-flash-lite-image`). The person expects a validation error naming the right command, before any browser opens.
4. **A refused or blocked generation** (403 BotGuard, 400 invalid argument on a tier-limited model, finish reason ≠ 1). Nothing is written, and the hint says what to try.
5. **An empty or whitespace prompt, a binary `--prompt-file`, or a missing output folder.** Each fails before any browser opens.

---

### Task 1: Shared media decoder

**Files:**
- Create: `src/aistudio/_rpc/rows/media.py`
- Modify: `src/aistudio/_types/media.py` (add `MediaPart`, `GeneratedMedia`)
- Modify: `src/aistudio/_rpc/rows/speech.py` (import the shared `finish_reason`)
- Test: `tests/unit/test_rows_media.py`

**Interfaces:**
- Produces: `decode_media(response, kind: str) -> GeneratedMedia`. `kind` is `"image"` or `"audio"`. `GeneratedMedia(parts: tuple[MediaPart, ...], text: str)`, `MediaPart(mime_type: str, data: bytes)`. Also `finish_reason(response) -> int | None` and `extension_for(mime) -> str` (`.jpg`, `.png`, `.webp`, `.mp3`, `.wav`, `.ogg`, otherwise `.bin`).

Observed responses (2026-10-07 live, saved under /tmp, not committed):
- image `resp[0]` = 2 chunks. Chunk 0 has `chunk[0][0][0][0][0] = [null, null, ["image/jpeg", b64], …11 nulls…, <855 KB opaque signature>]`. Chunk 1 has `[[[[[null, ""]], "model"], 1]]` (finish 1).
- music `resp[0]` = 3 chunks: text `"<instrumental>"`, then `["audio/mpeg", b64]` at part `[2]`, then finish 1.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_rows_media.py`)

```python
import base64

import pytest

from aistudio._rpc.rows.media import decode_media, extension_for, finish_reason
from aistudio.exceptions import SchemaDriftError, ServerError

JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"
MP3 = b"ID3\x04\x00fakemp3"


def b64(data):
    return base64.b64encode(data).decode()


def chunk(parts, finish=None):
    candidate = [[parts, "model"]] + ([finish] if finish is not None else [])
    return [[candidate], None, [9, 1, 10], None, None, None, None, "v1_id"]


def image_response(*images, finish=1):
    part = [None, None, ["image/jpeg", b64(images[0])]] + [None] * 11 + ["SIG" * 100]
    chunks = [chunk([part])]
    chunks += [chunk([[None, None, ["image/jpeg", b64(i)]]]) for i in images[1:]]
    return [chunks + [chunk([[None, ""]], finish)]]


def music_response(finish=1, text="<instrumental>"):
    return [[chunk([[None, text]]), chunk([[None, None, ["audio/mpeg", b64(MP3)]]]),
             chunk([[None, ""]], finish)]]


def test_image_parts_and_finish():
    media = decode_media(image_response(JPEG), "image")
    assert [(p.mime_type, p.data) for p in media.parts] == [("image/jpeg", JPEG)]
    assert media.text == ""


def test_several_images_are_kept_in_order():
    media = decode_media(image_response(JPEG, JPEG + b"2"), "image")
    assert [p.data for p in media.parts] == [JPEG, JPEG + b"2"]


def test_music_audio_and_text():
    media = decode_media(music_response(), "audio")
    assert [(p.mime_type, p.data) for p in media.parts] == [("audio/mpeg", MP3)]
    assert media.text == "<instrumental>"


def test_audio_parts_of_one_mime_are_joined():
    response = music_response()
    response[0].insert(2, chunk([[None, None, ["audio/mpeg", b64(b"more")]]]))
    assert decode_media(response, "audio").parts[0].data == MP3 + b"more"


def test_no_media_of_that_kind_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_media(music_response(), "image")


def test_bad_base64_is_schema_drift():
    response = music_response()
    response[0][1][0][0][0][0][0][2][1] = "%%%"
    with pytest.raises(SchemaDriftError):
        decode_media(response, "audio")


def test_missing_finish_is_schema_drift_and_early_stop_is_server_error():
    no_finish = music_response()
    no_finish[0].pop()
    with pytest.raises(SchemaDriftError):
        decode_media(no_finish, "audio")
    with pytest.raises(ServerError) as info:
        decode_media(music_response(finish=3), "audio")
    assert "stopped early" in info.value.message


def test_finish_reason_reads_the_last_chunk():
    assert finish_reason(music_response(finish=1)) == 1


@pytest.mark.parametrize("mime,ext", [("image/jpeg", ".jpg"), ("image/png", ".png"),
                                      ("audio/mpeg", ".mp3"), ("audio/wav", ".wav"),
                                      ("application/x-what", ".bin")])
def test_extension_for(mime, ext):
    assert extension_for(mime) == ext
```

- [ ] **Step 2: Run it to make sure it fails.** Run `uv run pytest tests/unit/test_rows_media.py -q`. Expected: an ImportError, because `aistudio._rpc.rows.media` does not exist.
- [ ] **Step 3: Implement.** Add the `MediaPart` and `GeneratedMedia` dataclasses (frozen) in `_types/media.py`. In `rows/media.py`:
  - Move `_finish_reason` from `speech.py` here as the public `finish_reason`, and have `speech.py` import it.
  - `decode_media` walks the tree. It collects `[mime, data]` pairs whose mime starts with `f"{kind}/"`, and decodes them with `base64.b64decode(..., validate=True)`. A `ValueError` raises `SchemaDriftError`.
  - Parts are joined when `kind == "audio"` and their mimes are equal. Image parts stay separate.
  - Text is the concatenation of every candidate text part `part[1]` that is a non-empty `str`, found in `chunk[0][*][0][0][*]`.
  - No parts, or an empty part, raises `SchemaDriftError(METHOD, (), f"{kind} parts")`.
  - `finish is None` raises `SchemaDriftError`. `finish != 1` raises `ServerError(f"generation stopped early (finish reason {n})", hint="try a different prompt: safety filters can stop a generation")`.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_rows_media.py tests/unit/test_rows_speech.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: decode image and audio parts from GenerateContent responses`.

### Task 2: Image and music page drivers

**Files:**
- Create: `src/aistudio/_browser/media_pages.py`
- Test: `tests/unit/test_media_pages.py`

**Interfaces:**
- Consumes: the `Ui` protocol (`goto`, `click`, `fill`, `press`, `responses`).
- Produces:
  - `ImagePage(ui).open(model_id)`, `.set_prompt(text)`, `.run(timeout_s=180.0) -> CapturedResponse`.
  - `MusicPage(ui).open(model_id)`, `.set_prompt(text)`, `.run(timeout_s=300.0) -> CapturedResponse`.
  - Constants `CHAT_URL = "https://aistudio.google.com/prompts/new_chat?model={model}"`, `IMAGE_PROMPT = "Enter a prompt"`, `MUSIC_EDITOR = re.compile(r"^Describe the music")`, `MUSIC_PROMPT = "Prompt input"`, `GENERATE = "Generate"`.

- [ ] **Step 1: Failing tests.**
  - `test_image_page_opens_the_model_fills_and_runs`. It checks the ops `[("goto", CHAT_URL.format(model="gemini-3.1-flash-lite-image")), ("fill", "textbox", "Enter a prompt", "an apple"), ("press", "textbox", "Enter a prompt", RUN_SHORTCUT)]`, and that the scripted GenerateContent response is returned.
  - `test_music_page_opens_the_editor_and_clicks_generate`. It checks the ops `[goto …lyria-3-clip-preview, ("click", "button", MUSIC_EDITOR.pattern, 0), ("fill", "textbox", "Prompt input", "calm piano"), ("click", "button", "Generate", 0)]`.
  - `test_no_response_is_a_browser_error`, for both pages. With an empty `fake_ui()` and `timeout_s=1` the call raises `BrowserError`, and the message names "image" or "music".
- [ ] **Step 2:** Run `uv run pytest tests/unit/test_media_pages.py -q`. Expected: an ImportError.
- [ ] **Step 3: Implement.** The model id is URL-quoted with `urllib.parse.quote`. `run()` mirrors `SpeechPage.run`: inside `async with ui.responses()` it presses or clicks, then `log.next("GenerateContent", timeout_s=…)`. A `None` raises `BrowserError(f"no {kind} was generated within {t:.0f}s", hint="re-run; if it persists, add --visible")`.
- [ ] **Step 4:** Run the test file. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: drive AI Studio's image (chat) and music (Lyria) pages`.

### Task 3: generate_image / generate_music use cases

**Files:**
- Modify: `src/aistudio/_app/media.py`
- Create: `tests/fixtures/GenerateContent/4.json` (image) and `5.json` (music). They are synthetic: real request and response *shapes*, a tiny base64 payload and the token `"!TOKEN"`.
- Test: `tests/unit/test_app_media_gen.py`; update `tests/unit/test_app_media.py` (lyria hint)

**Interfaces:**
- Consumes: `decode_media`, `extension_for`, `ImagePage`, `MusicPage`, and the existing `_confirm`, `_sent`, `_write`, `load_catalog`, `normalize_model_id`, `error_for_response`.
- Produces:
  - `DEFAULT_IMAGE_MODEL`, `DEFAULT_MUSIC_MODEL`.
  - `check_model(client, profile, model, *, command, hint) -> str`. `check_speech_model` keeps its name and calls it.
  - `generate_image(profile, prompt, *, model, out, overwrite, visible, client_factory, browser, now) -> dict` and `generate_music(...)` with the same signature.
  - Result: `{"files": [{"path", "mime_type", "bytes"}], "model", "text"?, "warnings"?}`.
- `_OFFERED_COMMANDS = {"generate speech", "generate image", "generate music"}`.

Rules:
- An empty prompt raises `ValidationError` before anything opens.
- With `-o`, it must not exist unless `--force`, and its folder must exist and be writable. Without `-o`, the current folder must be writable. All of this is checked before the catalog or the browser.
- Model checks: `command="generate image"` with hint `see "aistudio models list --category images"`, and `command="generate music"` with hint `see "aistudio models list --category music"`.
- A 403 raises `BotGuardError`, hint `re-run with --visible; a paid model may also need a paid tier`. Another ≥400 raises its mapped error. When that error has no hint, add `this model may need a paid tier or may be unavailable to your account`.
- `_confirm(sent, model_id=…, text=prompt, voice=None)`. The prompt is at `[1][0][0][0][1]` for both pages (live probe).
- Output path:
  - With `-o`, the first file goes exactly there. If `extension_for(mime) != out.suffix.lower()` (with `.jpeg` treated as `.jpg`), add the warning `"{path} holds {mime} data; its extension says otherwise"`.
  - Without `-o`, the file is `aistudio-{kind}-{now:%Y%m%d-%H%M%S}{ext}`.
  - Extra images go to `{stem}-2{suffix}`, `{stem}-3{suffix}`, … The first name that does not exist is used, so nothing is clobbered and nothing fails after a paid run.
  - Writes use the existing `_write`.

- [ ] **Step 1: Failing tests** (`tests/unit/test_app_media_gen.py`). Use the `fake_ui`, `make_client`, `logged_in_profile` and respx `serve_catalog` patterns from `test_app_media.py`.
  - `test_generate_image_writes_the_jpeg` (fixture 4): the files, the model and the bytes match the fixture payload, and `opened == ["minimized"]`.
  - `test_generate_music_writes_mp3_and_returns_the_text` (fixture 5): `"text": "<instrumental>"` and a `.mp3` path.
  - `test_default_names_use_the_returned_type`: with `out=None` and a fixed `now`, the file is `aistudio-image-20261007-101500.jpg` inside `monkeypatch.chdir(tmp_path)`.
  - `test_an_extension_mismatch_keeps_the_file_and_warns`: `-o x.png` with JPEG data writes `x.png` and adds a warning.
  - `test_extra_images_get_distinct_names`: with 2 images and `x-2.jpg` already present, the files are `x.jpg` and `x-3.jpg`.
  - `test_wrong_kind_of_model_fails_before_the_browser`: `generate_image -m lyria-3.5` gives a hint of `use "aistudio generate music"`, and `generate_music -m gemini-3.1-flash-lite-image` gives `use "aistudio generate image"`. Nothing opens.
  - `test_refused_and_invalid_are_mapped`: 403 gives `BotGuardError` with `--visible` in the hint, and 400 `[3,"Request contains an invalid argument."]` gives `ValidationError` whose hint mentions "paid tier". No file is written.
  - `test_empty_prompt_and_missing_folder_fail_before_anything_opens`.
  - Update the expected hint for `lyria-3.5` in `test_app_media.py::test_non_tts_models_fail_before_any_browser_opens` to `'use "aistudio generate music"'`.
- [ ] **Step 2:** Run. Expected: an ImportError (`generate_image` missing).
- [ ] **Step 3: Implement**, with the fixtures written by hand from the observed shapes.
- [ ] **Step 4:** Run `uv run pytest tests/unit/test_app_media_gen.py tests/unit/test_app_media.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: generate images and music through AI Studio's pages`.

### Task 4: CLI commands, docs, skill, live tests

**Files:**
- Modify: `src/aistudio/cli/generate_cmd.py`, `README.md`, `integrations/claude-code/aistudio/SKILL.md`, `docs/wire/findings.md`
- Create: `tests/live/test_live_media.py` (marker `live_slow`)
- Test: `tests/cli/test_generate_cmd.py`

**Interfaces:**
- `aistudio generate image [PROMPT] [--prompt-file F] [-m MODEL] [-o OUT] [--force] [--visible] [--json]`
- `aistudio generate music [PROMPT] [--prompt-file F] [-m MODEL] [-o OUT] [--force] [--visible] [--json]`
- `_read_text(text, text_file)` is shared by speech, image and music. Exactly one source is required. A file is read as UTF-8, and a `UnicodeDecodeError` raises `ValidationError("… is not UTF-8 text")`. A file over 1 MB raises `ValidationError`. This also closes minor #6 for `--text-file`.

- [ ] **Step 1: Failing CLI tests.**
  - `test_generate_image_passes_options` and `test_generate_music_passes_options`: monkeypatch the use case and assert the kwargs and the human output, which is `Wrote <path> (<bytes> bytes)` for an image, and for music the same plus `Text: <instrumental>`.
  - `test_prompt_needs_exactly_one_source`.
  - `test_binary_text_file_is_a_validation_error`, for speech and image: exit 1 with code `VALIDATION_ERROR`.
  - The stderr notice reads "Generating … in a minimized Chrome window…", or "…in a visible Chrome window…" with `--visible`.
- [ ] **Step 2:** Run. Expected: FAIL, because the "image" and "music" commands are unknown.
- [ ] **Step 3: Implement** the commands. Update the docs:
  - README: usage, costs from the catalog (about $0.034 per image, $0.04 per clip, $0.08 per `lyria-3.5` song), and the fact that video and Imagen are not available.
  - SKILL.md: add to "Commands that change things", using `--prompt-file` for user text. Under "Not available": video (needs a paid upgrade), Imagen and chat.
  - findings.md: add the "Image and music generation (2026-10-07)" section with the evidence above.
  - Live tests: one image and one clip, written into tmp_path. Assert the files exist and are non-empty.
- [ ] **Step 4:** Run `uv run pytest tests/cli/test_generate_cmd.py -q` and then the full suite. Expected: PASS.
- [ ] **Step 5: Commit** with message `feat: aistudio generate image / generate music commands`. Then reinstall the global CLI only if entry points changed; the install is editable, so they did not.

### Task 5: Login and browser-launch errors (minors 2, 3, 18)

**Files:** `src/aistudio/_auth/login.py`; Test: `tests/unit/test_login.py`

- [ ] **Step 1: Failing tests.**
  - `test_closing_the_login_window_is_a_browser_error`: a fake context whose `cookies()` raises `Exception("Target page, context or browser has been closed")` makes `run_login` raise `BrowserError` (not a raw exception), and the message mentions "closed".
  - `test_only_a_missing_chrome_gets_the_install_hint`: the launch raises `Exception("Target closed")`, and the hint has no "install".
  - `test_opening_in_existing_session_is_profile_in_use`: the launch raises `Exception("Opening in existing browser session.")`, and the hint contains "one at a time".
- [ ] **Step 2:** Run. Expected: 3 FAIL.
- [ ] **Step 3: Implement.**
  - Wrap the login wait loop's page and context calls in `except Exception`, raising `BrowserError("the login window was closed before sign-in finished", hint='run "aistudio login" again')`.
  - Give the Chrome hint only when the message matches `Executable doesn't exist|distribution 'chrome' is not found|ENOENT|not found at`. Otherwise use the generic hint `re-run; if it persists, run with --visible to see what Chrome shows`.
  - Add `|Opening in existing browser session` to `_PROFILE_IN_USE`.
- [ ] **Step 4:** Run `uv run pytest tests/unit/test_login.py -q`. Expected: PASS.
- [ ] **Step 5:** Commit with message `fix: clearer errors when the login window closes or Chrome cannot start`.

### Task 6: Private folders, replay folders (minors 1, 4)

**Files:** `src/aistudio/paths.py`, `src/aistudio/_fs.py`, `src/aistudio/_dev/fixtures.py`; Tests: `tests/unit/test_paths.py`, `tests/cli/test_replay_cmd.py`

- [ ] **Step 1: Failing tests.**
  - `test_aistudio_folders_are_private`: `get_profile("x").ensure()` under `aistudio_home` leaves `S_IMODE == 0o700` on home, `home/profiles` and the profile root.
  - `test_replay_accepts_a_method_folder`: replaying `tests/fixtures/ListModels` (as a copied tmp folder) counts more than 0.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.** Add `_fs.private_dir(path)`, which does `mkdir(parents=True, exist_ok=True)` and then `os.chmod(path, 0o700)` on each aistudio-owned level, and use it in `Profile.ensure` and `write_private_json`. `iter_fixtures`: if `root` directly contains `*.json`, iterate those; otherwise keep `*/*.json`.
- [ ] **Step 4:** Run the 2 test files. Expected: PASS. **Step 5:** Commit with message `fix: private 0700 aistudio folders; replay accepts a method folder`.

### Task 7: HTTP and wire robustness (minors 5, 8, 9; minor 6's `count-tokens --prompt-file` part)

**Files:** `src/aistudio/_transport/http.py`, `src/aistudio/_rpc/wire.py`, `src/aistudio/cli/chat_cmd.py`; Tests: `tests/unit/test_http_retry.py`, `tests/unit/test_http_transport.py`, `tests/unit/test_wire.py`, `tests/cli/test_chat_cmd.py`

- [ ] **Step 1: Failing tests.**
  - `test_html_success_body_is_a_server_error`: respx returns 200 `<html>…`, and the call raises `ServerError` whose message contains "not JSON".
  - `test_decode_rejects_nan_and_infinity`: `wire.decode("[NaN]")` and `"[Infinity]"` raise `ValueError`.
  - `test_reload_after_retries_is_bounded`: `503, 503, 401` with a changed session on disk, then 200. The call succeeds, `route.call_count == 4`, and there is never a 5th call (`503, 503, 401, 503` raises after exactly 4).
  - `test_count_tokens_binary_prompt_file`: exit 1 with code `VALIDATION_ERROR`.
- [ ] **Step 2:** Run. Expected: FAIL for the first, second and fourth. The third should pass, because it pins the current bound; it is a guard test, not a RED.
- [ ] **Step 3: Implement.**
  - Decode 2xx bodies inside `try`; a `ValueError` raises `ServerError(f"AI Studio answered with a body that is not JSON (HTTP {status})", hint="re-run; if it persists, AI Studio may be down")`.
  - `wire.decode` passes `parse_constant=_reject_constant`, which raises `ValueError`.
  - Document the bound in `call`'s docstring: attempts + 1 session reload.
  - `chat_cmd` uses the Task 4 helper `_read_text`. Move it to `cli/inputs.py` so both modules import it.
- [ ] **Step 4:** Run the test files. Expected: PASS. **Step 5:** Commit with message `fix: non-JSON success bodies, NaN in responses and binary prompt files are clear errors`.

### Task 8: Lookup consistency (minors 7, 12, 16, 22; 11 regression)

**Files:** `src/aistudio/_app/apps.py` (pick), `src/aistudio/_app/models.py`; Tests: `tests/unit/test_app_apps.py`, `tests/unit/test_app_models.py`, `tests/unit/test_app_models_star.py`

- [ ] **Step 1: Failing tests.**
  - `test_pick_ignores_case_but_prefers_exact`: pick picks `"ABC"` from `["abc1", "ABC"]` by exact match, and `"abc1"` by `"ABC1"`.
  - `test_blank_ref_is_rejected` (regression for minor 11).
  - `test_unknown_model_hint_mentions_refresh`.
  - `test_get_model_uses_pick`: an ambiguous prefix gives `ValidationError` listing the candidates, as pick does.
  - `test_star_refreshes_the_catalog_once_for_a_new_model`: a stale cache without the model, plus a respx ListModels that has it, lets the star succeed.
- [ ] **Step 2:** Run. Expected: FAIL, except the regression test.
- [ ] **Step 3: Implement.**
  - pick: try an exact match, then a case-insensitive exact match, then a case-insensitive prefix.
  - `get_model` calls `pick(..., noun="model", hint='see "aistudio models list", or add --refresh')`.
  - `star_model`: on NOT_FOUND from the cached catalog, call `load_catalog(..., refresh=True)` once.
- [ ] **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `fix: case-insensitive lookups, one lookup path, refresh hint and star refresh`.

### Task 9: Lists (minors 10, 14, 15)

**Files:** `src/aistudio/_rpc/rows/apps.py`, `src/aistudio/_app/apps.py`, `src/aistudio/cli/prompt_cmd.py`, `src/aistudio/cli/app_cmd.py`; Tests: `tests/unit/test_rows_apps.py`, `tests/unit/test_app_apps.py`, `tests/cli/test_prompt_cmd.py`

- [ ] **Step 1: Failing tests.**
  - `test_a_malformed_app_entry_is_skipped_with_a_warning`: the decoder returns the good apps plus a `skipped` count. `app list --json` then includes `"warnings": ["skipped 1 app entry AI Studio sent in an unknown shape"]`.
  - `test_app_list_never_returns_more_than_limit`.
  - `test_prompt_list_limit_help_says_page`: the `--help` text says the filters apply within those N.
- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement. **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `fix: list commands skip malformed entries, honour --limit, explain paging`.

### Task 10: Build timing and messages (minors 13, 17, 19a, 19b)

**Files:** `src/aistudio/_browser/build_page.py`, `src/aistudio/cli/app_cmd.py`, `README.md`, `tests/live/test_live_build.py`; Tests: `tests/unit/test_build_page.py`, `tests/cli/test_app_build_cmd.py`

- [ ] **Step 1: Failing tests.**
  - `test_timeout_caps_the_start_waits`: `create(…, timeout_s=30)` with a scripted `next` that records `timeout_s` asks for at most 30 s in total.
  - `test_visible_build_notice_does_not_say_minimized`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Implement.** Compute the deadline at the start of `create`/`prompt`, and give `_expect` `min(START_TIMEOUT_S, remaining)`. A start that runs out of time stays a `BrowserError` with the existing hint. The notice wording depends on `visible`. README: builds take about 5–7 minutes. Live test: slice the applets before awaiting their runs.
- [ ] **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `fix: --timeout covers build start waits; accurate build notices and timings`.

### Task 11: Status (minors 20, 21)

**Files:** `src/aistudio/_rpc/rows/status.py`, `src/aistudio/_types/status.py`, `src/aistudio/cli/status_cmd.py`; Tests: `tests/unit/test_rows_status.py`, `tests/cli/test_status_cmd.py`

- [ ] **Step 1: Failing tests.**
  - `test_inferred_codes_are_marked`: an incident whose state code is among the inferred ones has `"inferred": true` in `to_dict()`. An unknown state still counts as active, and is shown as `state:<n>` with `inferred: true`.
  - `test_fail_if_active_exits_3`: one active incident gives exit code 3 after the normal output, and none gives exit 0.
- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement. Exit 3 is chosen so it does not collide with 1 (handled error) or 2 (unexpected). **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `feat: mark inferred status codes; status --fail-if-active`.

### Task 12: UI wrappers, export progress (minors 23, 24)

**Files:** `src/aistudio/_browser/ui.py`, `src/aistudio/_app/apps.py`, `src/aistudio/cli/app_cmd.py`; Tests: `tests/unit/test_ui_roles.py`, `tests/cli/test_app_export_cmd.py`

- [ ] **Step 1: Failing tests.**
  - `test_reads_on_a_closed_window_are_browser_errors`: `visible`, `labels` and `key` raise `BrowserError` when the page raises.
  - `test_export_reports_progress_on_stderr`: in human mode stderr contains `Downloaded 1/2` … `2/2`. In `--json` mode there is no progress.
- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement a light `_read` wrapper whose message is `the browser window closed or the page changed`, plus `export_app(..., progress=None)`, a callback called after each verified file. **Step 4:** Run. Expected: PASS. **Step 5:** Commit with message `fix: wrap page reads; show export progress`.
