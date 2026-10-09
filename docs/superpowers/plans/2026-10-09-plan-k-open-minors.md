# Plan K: CI, Readable Transcripts, and the Still-Open Deferred Minors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Close what the 89-item audit of earlier deferred minors found still open, plus the bugs it found next to them. (User: "do 1-3 dont need mcp just cli enough".) Items 1 (GitHub CI, `87075aa`) and 2 (one timed line per sentence, `19dc541`) are done.

**Specs:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` and `docs/superpowers/specs/2026-10-07-flow-generation-design.md`. These change messages, edge cases and one fallback, plus spec text that drifted.

## Global Constraints

- No spending; live checks are free reads or a free Flow personality write of a test character's own text. Never delete Flow or Build test items.
- A paid result is never lost; no secrets in output; ruff clean and suite green after each item.

## Not in this plan

- Item 89 (relabelling several files): latent, so a guard is enough (item 14).
- The audit's "small gaps" in fixed items (37, 43/86, 50, 7): no change.

## Review Focus

1. A long free Flow stream that has not ended at the 90 s limit gives TIMEOUT ("check Flow"), never AGENT_REPLY "nothing was spent".
2. A failed credit read before a direct spend never blocks it; the result says the charge was not verified.
3. Downloaded or stitched bytes that are not a video are never saved as `.mp4` without a warning.
4. `prompt list --type T --limit N` returns up to N prompts of type T from up to 1000, not only those among the newest N.
5. An extend whose scene Flow has not listed yet reports no scene id and a note, never an unverified one.

---

### Task 1: The open minors

| # | Test | Fix |
|---|---|---|
| 1 | `test_prompt_list_ids_survive_a_pipe` (test_cli_library) | `prompt list` id column `no_wrap=True, min_width=<longest id>` (#69) |
| 2 | `test_type_filter_looks_past_the_limit` (test_app_library) | With `--type`, request 1000, filter, cut to `--limit` (#10) |
| 3 | `test_count_tokens_refuses_huge_files` (test_cli_chat) | `count-tokens --prompt-file` cap 50 MB (#6) |
| 4 | `test_build_notice_comes_after_the_checks` (test_app_build) | `create_app`/`prompt_app` take `on_start`, called just before the browser; the CLI prints its notice there (#20) |
| 5 | `test_export_announces_the_file_count` (test_export), CLI | `progress(0, total)` before downloading; the CLI says "Downloading N files…" then prints every 10 or every 5 s (#24) |
| 6 | `test_video_bytes_are_checked` (test_app_flow) | `_type_of("video")`: `ftyp` → mp4, EBML → webm, else `.bin`; `_stitched` warns on `.bin` (#40d) |
| 7 | `test_an_unreported_id_drops_the_personality_with_a_note` (test_app_flow_d) | No id: personality None plus a note naming the command |
| 8 | `test_character_output_without_an_id` (test_cli_flow) | "Created character (id not reported) …" |
| 9 | `test_a_failed_credit_read_does_not_block_direct_spend` (test_app_flow_d) | `_credits_before(page)` (never raises) in extend, edit, character create, body and generate |
| 10 | `test_a_stitch_poll_error_counts_toward_the_limit` (test_flow_page_d) | A `batch_rpc` error while polling counts as a failure |
| 11 | `test_flow_payloads_reject_nan` (test_flow_wire) | Flow's `payloads` rejects NaN/Infinity like `wire.decode` |
| 12 | `test_extend_without_a_listed_scene_says_so` (test_app_flow_d, test_flow_page_b) | Drop the unverified `rqZuUc` scene-id fallback; `scene_id` null plus a note (#51) |
| 13 | `test_an_unfinished_reply_is_a_timeout` (test_flow_page, test_app_flow) | `AgentTurn.answered`; no StreamChat reply by the deadline → TIMEOUT "Flow's agent had not finished replying" (#78) |
| 14 | `test_relabelling_takes_one_file` (test_browser_ui) | `set_files` with `types` refuses several files when one needs relabelling (#89) |
| 15 | live: `test_live_catalog_categories_match_model_families` | veo→video, lyria (not realtime)→music, gemma→gemma, *-tts→audio; run it live (free) (#11) |
| 16 | live capture, then `test_personality_is_read_back` | Capture the `rzMKMb` personality reply (free; a test character's own text). Compare the stored text, or read it back from the project (#76) |
| 17 | (docs) | Flow spec §3: `--project` takes a full id or link; `flow extend` has no `--prompt-file`/`--duration`. SKILL: `flow edit`/`character body` OVER_BUDGET is aistudio's refusal too |
