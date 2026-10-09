# Plan J: Fix What Plan I Left Open

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Fix every open item from the plan I report that can be fixed without spending credits or using credentials. (User: "fix all".)

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` §6.4 and `docs/superpowers/specs/2026-10-07-flow-generation-design.md`. These fixes change wording, warnings and accepted inputs; the spec's §6.4 "As shipped" note gains the new audio types.

**Evidence:** `docs/wire/findings.md`, "AI Studio transcription and image editing" (live and free probes, 2026-10-09).

## Global Constraints

- No spending beyond tiny transcription checks; a paid result is never lost; no secrets in output.
- ruff is clean and the suite is green after each task.

## Not in this plan (blocked; the user decides)

- **Rewriting git history** to drop the expired fixture tokens: the force-push was refused by the session's permission check.
- **Moving aistudio's Drive uploads to the trash:** Drive needs an OAuth token or the signed session. Calling Drive that way was refused by the session's permission check.
- **The Flow restore root cause:** reproducing it means a paid video run.

## Review Focus

1. A quote whose Flow request is still waiting never claims the settings were restored, whether the restore later fails, succeeds, or never runs (wanted == previous).
2. A transcript reply with text only in slot 1 (no part has slot 22) is kept but warned about as "not a transcript". A normal reply (slot 22, with or without words) gives no new warning.
3. `.mp4` and `.webm` audio reach the page labelled `audio/mp4` and `audio/webm`. Other files keep Playwright's own type, and image uploads are unchanged.
4. A file too big to read still fails with the existing 100 MB check before Chrome opens; reading the bytes happens only after that check.

---

### Task 1: Fixes

| # | Test | Fix |
|---|---|---|
| 1 | `test_a_waiting_quote_never_claims_a_restore` (test_app_flow) | QUOTE_ONLY keeps the waiting note as is. "(the settings were restored)" is gone: the restore runs later and may fail, or may not run at all |
| 2 | `test_decode_transcript_marks_plain_text` (test_rows_media), `test_a_plain_text_reply_is_flagged` (test_app_media_gen) | `Transcript.plain` is True when text came only from slot 1. `_transcript_problems` warns "AI Studio replied with plain text, not a transcript (it may be a refusal); check it" |
| 3 | `test_audio_types_are_sent_as_audio` (test_media_pages), `test_set_files_labels_given_types` (test_browser_ui), `test_mp4_and_webm_audio_are_accepted` (test_app_media_gen) | `AUDIO_INPUTS` gains `.mp4`/`.webm`. `TranscribePage.attach` passes `AUDIO_TYPES = {".mp4": "audio/mp4", ".webm": "audio/webm"}`. `Ui.set_files(selector, paths, types=None)` sends file payloads with those types (others: `mimetypes.guess_type`) |
| 4 | (docs) | findings: long recording, inline sending, accept list, Drive Bearer upload. README, SKILL, spec §6.4: the new audio types |
