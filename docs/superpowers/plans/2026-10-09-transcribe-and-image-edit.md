# Plan I: `transcribe` and `generate image --image`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle.

**Goal:** Build the two Phase 1 commands still missing from the AI Studio CLI spec (user: "Transcribe + image editing"). Both are page-driven, with no API key.

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` §6.4:
- `generate image "…" [--image IN ...]`;
- `transcribe AUDIO [-m] [--diarize] [--timestamps] [-o FILE.txt]` → `{text, model}`.

Evidence: `docs/wire/findings.md`, "AI Studio transcription and image editing".

## Global Constraints

- Uses the user's quota (image ≈ $0.034; transcription is a fraction of a cent for a short clip). One request per run, and never retried.
- Inputs are checked before Chrome opens: the file exists, has an allowed type, and is at most 100 MB.
- Uploads land in the user's Google Drive (AI Studio's own folder). The docs and skill say so.
- No secrets in output, ruff is clean, and the suite is green after each task.

## Review Focus

1. An input file that AI Studio never accepts (no "Remove media" chip) → error before Run, nothing generated.
2. A transcription reply that is not text (a refusal, or a finish other than STOP) → a clear error, never empty text reported as success.
3. `--timestamps` / `--diarize` when the switches are already in the wanted state → not toggled the wrong way.
4. `-o` that exists without `--force` → refused before Chrome opens.
5. Several `--image` files → every one is attached before Run.

| # | Test | Fix |
|---|---|---|
| 1 | `test_decode_text_reads_the_reply` (test_rpc_media) | `decode_text(response) -> str`: the joined text parts. No text gives a SchemaDriftError; a finish other than STOP gives the existing stop error |
| 2 | `test_set_files_reaches_the_input` (test_ui_roles) | `Ui.set_files(selector, paths)` uses `locator.set_input_files` |
| 3 | `test_image_page_attaches_every_file` (test_media_pages) | `ImagePage.attach(paths)`: set the files on `input[type=file]`, then wait for one "Remove media" chip per file (60 s, else "AI Studio did not take …; nothing was generated") |
| 4 | `test_transcribe_page_sets_options_then_runs` (test_media_pages) | `TranscribePage`: `open`, `attach`, `options(timestamps, speakers, smart)` (read each switch with `checked`, press only when it differs), `vocabulary(text)`, and `run()` presses "Transcribe" and waits for `GenerateContent` |
| 5 | `test_generate_image_with_inputs` (test_app_media_gen) | `generate_image(..., images=[...])`: checks the files, then `page.attach` before `set_prompt` |
| 6 | `test_transcribe_writes_text` (test_app_media_gen) | `transcribe(profile, audio, *, model, timestamps, diarize, smart, vocabulary, out, overwrite, visible, …) -> {text, model, file?}`. The model must have `command == "transcribe"` |
| 7 | CLI tests | `generate image --image` (repeatable); top-level `transcribe AUDIO [-m] [--timestamps] [--diarize] [--no-smart] [--vocabulary TEXT] [-o] [--force] [--visible] [--json]` |
| 8 | live | One `transcribe` of a short `say` clip, and one `generate image --image` edit (user-accepted small quota). README and skill updated |
