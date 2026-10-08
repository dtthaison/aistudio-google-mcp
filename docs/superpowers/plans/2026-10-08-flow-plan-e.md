# Plan E: Attachments by Id, and Every Deferred Minor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Two things (user: "go head all"):
- A photo used for a character can still be attached as a frame or ingredient. Attached media are verified by id before anything can be approved.
- Fix every minor deferred by the plan C and plan D final reviews.

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md`, §12 (plan E) together with §10 and §11. Evidence: `docs/wire/findings.md`, "Flow plan E discovery".

**Format note:** these are small, independent fixes. Each item names its test and its fix. The executor writes the test first.

## Global Constraints

- Same safety rules as plans A–D: no spending outside the budget rules, a paid result is never lost, and no secrets in output.
- ruff is clean and the suite is green after each task.

## Review Focus

1. A photo that already became a character, passed as `--start-frame <its id>`, is attached (no "several media share the title"), and the quote carries that exact id.
2. The quote's request carries a media id that was not attached (a stale chip in the prompt box) → rejected, `PLAN_MISMATCH`, nothing approved.
3. The model catalog is read twice in one minute with an abbreviated id → the second read uses the cache.
4. Stitching while the session expires (HTTP 401 on every poll) → it stops after 5 failures, not after `--timeout`.
5. A character named "Paper  boat" (two spaces) in Flow → `--character "paper boat"` finds it, and `create "Paper boat"` refuses the name.

---

### Task 1: Attachments by id (item 2)

| # | Test | Fix |
|---|---|---|
| 1 | `test_a_characters_photo_copy_does_not_block_its_photo` (test_app_flow_b) | `_attachment_titles` builds `by_title` without the media ids of characters' images (`contents.characters[].media_id`) |
| 2 | `test_sent_media_reads_slot_1` (test_flow_rows_d) | `rows.sent_media(body) -> list[str]`, which reads the message's slot 1 `[[media_id, …], …]`. `AgentTurn.media: tuple[str, ...]` is filled by `submit_for_quote` |
| 3 | `test_attachments_are_checked_by_id_in_the_quote` (test_app_flow_b) | `flow_generate`: right after the quote, `set(turn.media)` must equal the attached ids (the given media ids, plus each upload's new id). Otherwise call `_reject_noting` and raise `PlanMismatchError` (hint: check the prompt box's attachments in Flow) |

### Task 2: Plan C's deferred minors

| # | Test | Fix |
|---|---|---|
| 4 | `test_a_cut_prompt_is_not_sent` (test_flow_page) | `_wait_for_prompt`: the collapsed editor text must contain the last 40 characters of the collapsed text (M1) |
| 5 | `test_extend_reads_the_start_before_the_scene` (test_flow_page_b) | `extend`: read `fZytfe` first, then `rqZuUc` with `timeout_s=2.0` (M2) |
| 6 | `test_a_bad_cache_fetches_once` (test_models) | `_load(...) -> (models, fetched_at, fetched_now)` is shared by `load_catalog` and `catalog_with`. The cache is parsed once; a cache that fails to decode counts as fetched now (M3) |
| 7 | `test_a_prefix_lookup_uses_a_recent_cache` (test_models) | `catalog_with` refetches only when the copy came from cache **and** is older than `RECHECK_S = 300` (M4) |
| 8 | `test_flow_notice_comes_after_validation` (tests/cli/test_flow_cmd) | `flow_generate(on_start=…)` calls it right before the browser opens. The CLI passes its notice printer (M5) |
| 9 | `test_extra_parts_are_numbered_in_order` (test_app_media_gen) | Extra parts are named `<stem>-<index+1><ext>` via `free_name`, so jpg, png, jpg becomes `x.jpg`, `x-2.png`, `x-3.jpg` (M6) |
| 10 | `test_avif_and_unknown_image_bytes` (test_app_flow_b) | `_type_of`: for kind image, `ftyp` brands `avif`/`avis` give `.avif` and `heic`/`heix`/`mif1` give `.heic`. Unknown image bytes give `("application/octet-stream", <requested ext or .bin>)`, with the extension-mismatch warning (M7) |
| 11 | `test_start_character_needs_the_character_page` (test_flow_page_b) | `start_character` checks that the URL ends with `/character` before typing. A failure after Start goes through `_character_made` (it names the character, not "media") (M8) |
| 12 | (delete) | Remove `ui._method` and its test line (M9) |
| 13 | `test_on_start_runs_on_success`, `test_an_unknown_model_fails_before_the_browser` (test_app_media_gen) | Tests only (M11) |

### Task 3: Plan D's deferred minors

| # | Test | Fix |
|---|---|---|
| 14 | `test_stitch_gives_up_after_repeated_failures` (test_flow_page_d) | `stitch`: 5 failures in a row (HTTP ≥ 400 or an unreadable state) raise `BrowserError` naming the last HTTP status (M1) |
| 15 | `test_names_compare_with_spaces_collapsed` (test_app_flow_d) | `_key(name) = " ".join(name.split()).casefold()` is used in `_free_name` and `_characters` (M5) |
| 16 | `test_character_mismatch_hint_points_at_flow` (test_app_flow_d) | PLAN_MISMATCH hint: "nothing was spent; check the prompt box's attachments in Flow, then re-run" (M6) |
| 17 | `test_link_photo_hint_is_accurate` (test_flow_page_d) | The `link_photo` hint says "do not re-run; add a photo to it in Flow or rename it" (M7) |
| 18 | `test_a_different_stored_name_is_a_warning` (test_app_flow_d) | `create_character_record` returns `(cid, stored_name)`. A stored name that differs from the requested one becomes a result warning, and `name` reports the stored one (M8) |
| 19 | `test_no_rename_without_an_id` (test_app_flow_d) | The description path skips rename when `cid is None`, with a warning (M9) |
| 20 | `test_unnamed_characters_are_listed` (test_flow_rows_d, tests/cli) | `_characters` keeps `name=""`, and the CLI prints "(unnamed)" (M10) |
| 21 | (docstring) | `Ui.batch_rpc`: "token-less batchexecute calls from the page (reads and free writes)" (M11) |
| 22 | `test_long_list_lines_do_not_wrap` (tests/cli/test_flow_cmd) | `cli/output.py`: `Console(soft_wrap=True)` for stdout and stderr |

### Task 4: Docs and live check

| # | Test | Fix |
|---|---|---|
| 23 | (docs) | SKILL.md: a `PLAN_MISMATCH` about attachments or characters means Flow's request was rejected and nothing was spent; check the prompt box in Flow. README unchanged unless the commands change |
| 24 | live (free) | `aistudio flow image "<prompt>" --ingredient <apple-start.jpg upload id>` (a photo that is also a character) returns `quoted`, `declined`, and no `PLAN_MISMATCH` |
