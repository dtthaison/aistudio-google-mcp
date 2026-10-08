# Plan F: Free Generations, Personality, Plan E's Deferred Minors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Make `flow image` work with free models, which Flow generates without a quote. Add character personality. Fix plan E's deferred minors. (User: "go head all".)

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md`, §13. Evidence: `docs/wire/findings.md`, "Flow plan F discovery".

## Global Constraints

- Same rules as plans A–E: no spending outside the budget rules, a paid result is never lost, and no secrets in output.
- ruff is clean and the suite is green after each task.

## Review Focus

1. A free image run without `--max-credits` → files are downloaded, `credits_spent` is 0 and was read before and after, and the warning says Flow did not ask.
2. A stream that has both a permission request and a generated result → the permission path wins: nothing is downloaded without Approve.
3. A free generation that reports `status` other than "success" → that media is named and not downloaded; nothing pretends it succeeded.
4. A quoted run whose real charge differs from the quote → a warning with both numbers.
5. Personality text with quotes, newlines or 2,000 characters → sent intact, and checked against the read-back.

---

### Task 1: Free generations

| # | Test | Fix |
|---|---|---|
| 1 | `test_generated_results_are_read_from_the_stream` (test_flow_rows) | `rows.agent_generated(payload) -> list[Generated]` (media_id, status, model, aspect) from `generate_*` tool results (calls give model and aspect, results give media id and status) |
| 2 | `test_a_turn_reports_what_flow_generated` (test_flow_page) | `submit_for_quote` collects `generated` from every payload; `AgentTurn.generated: tuple[Generated, ...]`. A permission request still wins |
| 3 | `test_a_free_image_is_downloaded_without_approval` (test_app_flow) | `flow_generate`: when there is no quote but `turn.generated` is not empty, take the successful ids, read `media_details`, save, read credits after, and return `done` with a warning. Unsuccessful ones go into a warning |
| 4 | `test_credits_are_read_before_and_a_different_charge_warns` (test_app_flow) | `before = await page.credits()` at the start. A quoted run warns when `before - credits_left != quote` |

### Task 2: Personality

| # | Test | Fix |
|---|---|---|
| 5 | `test_personality_sends_its_field_mask` (test_flow_page_d) | `FlowPage.set_personality(pid, cid, text)`: `rzMKMb` `[[pid, cid, None, [1, None, [None, None, text]]], [["entity_info.character_info.personality_notes"]]]`. HTTP ≥ 400 is a `BrowserError` |
| 6 | `test_create_with_personality_and_the_personality_command` (test_app_flow_d, tests/cli) | `flow_character(..., personality=None)` sets it after the character exists (a failure becomes a warning). `flow_set_personality(profile, cid, text)`. CLI: `character create … --personality TEXT`, `character personality ID TEXT` |

### Task 3: Plan E's deferred minors

| # | Test | Fix |
|---|---|---|
| 7 | `test_an_unnamed_character_cannot_be_attached` (test_app_flow_d) | `_characters` refuses an empty name, with a rename hint by id. The owner hint uses the id when the name is empty |
| 8 | (live test) | `test_live_flow.py`: drop the "every character has a name" assertion, and count duplicates with the same key as aistudio (spaces collapsed, any case) |
| 9 | `test_avif_with_a_mif1_major_brand` (test_app_flow_b) | `_type_of` looks for `avif`/`avis` among the compatible brands before mapping `mif1` to HEIC. `_save_all` warns when unknown bytes are saved without `-o` |
| 10 | `test_stitch_give_up_names_an_unreadable_reply` (test_flow_page_d) | The message says "(last reply HTTP 200, unreadable)" for a 200 reply that could not be read |
| 11 | `test_flow_generate_calls_on_start_before_the_browser`, `test_attachments_with_a_character` (test_app_flow*) | Tests only |

### Task 4: Docs and live check

| # | Test | Fix |
|---|---|---|
| 12 | (docs) | README and SKILL.md: free models generate without a quote (results are downloaded, and nothing is charged), personality, and the out-of-scope list |
| 13 | live (free) | `flow image "<prompt>" --count 1 -o x.png` gives `done`, 1 file, `credits_spent` 0. `character personality` on "Paper boat" gives the read-back. `-m live` passes |
