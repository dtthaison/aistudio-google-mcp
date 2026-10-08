# Deferred Minors (Plan C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Fix every minor deferred by the final reviews of image/music, Flow plan A and Flow plan B (user: "go ahead all the issues").

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md` (§3, §7, §10) and `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` (§6.4, §6.8). Where an item is a difference between spec and code, the spec is updated to match the shipped behaviour, or the code is fixed, as stated per item.

**Format note:** these are small, independent fixes. Each item names its test and its fix rather than spelling out full code. The executor writes the test first.

## Global Constraints

- Same safety rules as plans A and B: no spending without the budget rules; a paid result is never lost; no secrets in output.
- ruff is clean, and the suite is green after each task.

## Review Focus

1. A Flow image saved with `-o x.png` that is really JPEG → it keeps the name and warns, as `generate image` does.
2. A quote-only Flow run → Flow's request is rejected, so nothing stays waiting with changed settings.
3. A notice printed for a command that then fails validation → there should be no "Generating…" line.
4. Extra outputs with a different type → each gets its own extension.
5. A 2 KB Flow prompt → typed quickly, and arrives intact.

---

### Task 1: AI Studio media and shared inputs

| # | Test | Fix |
|---|---|---|
| 1 | `test_extra_parts_take_their_own_extension` (tests/unit/test_app_media_gen.py) | `_generate_media`: extra parts use `free_name(first.with_suffix(ext_of_part), start=2)` |
| 2 | `test_paid_tier_hint_only_for_invalid_or_quota` | `_refused`: add `_TIER_HINT` only for `ValidationError`/`QuotaExceededError` |
| 3 | `test_a_bom_is_not_part_of_the_text` (tests/cli/test_generate_cmd.py) | `read_text`: `encoding="utf-8-sig"` |
| 4 | `test_no_notice_when_validation_fails` | the use cases accept `on_start: Callable[[], None] | None`, called right before the browser opens; the CLI passes the notice printer |

### Task 2: Catalog and lookups

| # | Test | Fix |
|---|---|---|
| 5 | `test_a_cold_cache_miss_fetches_once` | `load_catalog` reports whether it fetched; `catalog_with` refetches only when the first copy came from cache |
| 6 | `test_a_cached_prefix_does_not_hide_a_new_exact_id` | `get_model`: when only a prefix match exists in a cached copy, refetch once and prefer an exact id |
| 7 | `test_pick_prefers_a_case_sensitive_prefix` | `pick`: exact, then case-insensitive exact, then a **case-sensitive** unique prefix, then a case-insensitive unique prefix |
| 8 | `test_read_errors_say_what_happened` (test_ui_roles) | `PlaywrightUi._read`: a Playwright timeout reports "the page did not show it in time"; only target-closed errors say "window closed". Messages say "the page", not "AI Studio" |

### Task 3: Flow page robustness

| # | Test | Fix |
|---|---|---|
| 9 | `test_batched_rpcids_are_split` | `_method` returns the rpcid list; `_QueueLog.put` stores one entry per rpcid |
| 10 | `test_upload_ignores_media_that_existed_before` | `upload(path, known_ids)`: accept only an as29s record whose id is not in `known_ids` (from contents before the upload) |
| 11 | `test_character_id_survives_a_missing_image` | `create_character` raises with the character id in the hint when ogiZ0b never comes; an empty image list is an error, not done |
| 12 | `test_character_page_is_loaded_once` | split into `open_character(pid) -> model` and `start_character(description)`; the use case checks the price between them |
| 13 | `test_credits_drift_hint_is_not_dev_capture` | `flow_credits`' SchemaDrift hint: "re-run; if it persists, the Flow UI changed" |

### Task 4: Flow outputs and options

| # | Test | Fix |
|---|---|---|
| 14 | `test_flow_files_warn_on_an_extension_mismatch` | `_type_of` sniffs PNG/JPEG/WEBP/MP4 magic; `_save_all` warns as `generate image` does |
| 15 | `test_quote_only_rejects_flows_request` | QUOTE_ONLY presses Reject; spec §3.1 and §3.2 are updated (result unchanged, plus `"declined": true`) |
| 16 | `test_model_is_reported_as_the_cli_id` | `label_to_id(label)` maps a label from `MODELS` to its id (label kept when unknown); used by generate and extend |
| 17 | `test_extend_reports_the_scene` | `extend()` reads `rqZuUc` (scene id); the result gains `scene_id` |
| 18 | `test_media_lists_prompts` | `flow_media` items gain `prompt` |
| 19 | `test_project_links_with_a_query_string` | `_PROJECT` ignores `?…` and `#…` |
| 20 | `test_duration_is_checked_against_the_default_model` | after `read_defaults`, re-check `--duration` against the default model's durations (when known) |
| 21 | `test_a_different_model_after_the_job_is_a_warning` | known keys (`veo_3_1_t2v_lite` → veo-3.1-lite) are compared after the job, and a mismatch warns |
| 22 | `test_flow_image_has_no_frames` (CLI) | `--start-frame`/`--end-frame` only on `flow video` |
| 23 | `test_unknown_credits_say_unknown` (CLI) | the human output prints "credits left unknown" for `None` |
| 24 | `test_long_prompts_are_inserted_not_typed` | `Ui.type_text`: over 200 chars uses `keyboard.insert_text` after focusing; live check in Step 4 |

### Task 5: Docs and live tests

| # | Test | Fix |
|---|---|---|
| 25 | (docs) | SKILL.md: `OVER_BUDGET` from extend/character is aistudio's own refusal; "no measured price" is final; a quote-only run with a local file still uploads it (use the reported media id for the paid run); the one-profile-at-a-time list includes `flow …` |
| 26 | (docs) | README and spec §3.1/§7 describe the shipped plan check (counts and cost, plus saved settings); spec §6.4/§6.8 (AI Studio) list image/music flags and exit 3 |
| 27 | `tests/live/test_live_flow.py` | the extend live test picks a finished Veo 3.1 Lite video (model key) or skips |

Step 4 of the run: with the profile idle, run `uv run pytest -m live tests/live/test_live_flow.py -q`, plus one free quote with a 2 KB prompt (item 24). Expected: all pass, credits unchanged.
