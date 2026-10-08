# Plan H: The Remaining Deferred Minors (Plan F and Plan G Reviews)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Fix every minor still deferred from the plan F and plan G final reviews. (User: "go ahead".)

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md`, §13 and §14. These fixes change messages and edge cases only; no spec change is needed.

## Global Constraints

- Same rules as plans A–G: no spending outside the budget rules, a paid result is never lost, and no secrets in output.
- ruff is clean and the suite is green after each task.

## Review Focus

1. A free stream still running at the 90 s limit gives `TIMEOUT` with "still generating (free)", not `AGENT_REPLY` "nothing was spent".
2. A personality containing `"`, `$(…)` or a newline produces a hint that is safe to paste into a shell.
3. A free run whose download fails keeps every warning, including "could not restore defaults", in the error.
4. A failure after Start in `flow edit` or `character body` names the clip or character it was for.
5. A clip whose record has no workflow id gives "Flow did not name the editor for media X; nothing was spent", not NOT_FOUND.

---

### Task 1: Free generations and charges (plan F review)

| # | Test | Fix |
|---|---|---|
| 1 | `test_a_later_node_does_not_overwrite_the_call` (test_flow_rows) | `agent_generated`: merge call values (a later node without `media_id` adds keys, never erases them). The placeholder falls back to `node[3]` |
| 2 | `test_an_unverified_model_is_noted` (test_app_flow) | `_free_run`: when a made item has no model, add "Flow did not name the model; it was not checked" |
| 3 | `test_a_free_stream_still_running_is_a_timeout` (test_flow_page, test_app_flow) | `submit_for_quote` notes `generating=True` when it saw a `generate_*` call without a result. `flow_generate` then raises `WaitTimeoutError("Flow was still generating (free) when aistudio stopped waiting", hint="check the project in Flow before re-running")` |
| 4 | `test_charge_wording_is_neutral` (test_app_flow) | "credits fell by N during this run" (free path), and "credits fell by N during this run; the quote was Q" |
| 5 | `test_a_failed_credit_read_does_not_block_a_quote` (test_app_flow) | The `before` read in `flow_generate` is caught: `None`, plus the note "could not read Flow credits before the run" |
| 6 | `test_a_hint_never_starts_with_a_separator` (test_app_flow) | Join hints with a helper: `_with_note(hint, note)` |
| 7 | `test_free_media_beside_a_quote_are_mentioned` (test_app_flow) | Quote result: warn "Flow also made …" when `turn.generated` is not empty |
| 8 | `test_saving_errors_keep_the_free_runs_warnings` (test_app_flow) | Free path: when `_save_all` raises, fold the run's warnings into the error hint |

### Task 2: Personality and direct-spend messages (plan F M5, plan G M1, M2, M5)

| # | Test | Fix |
|---|---|---|
| 9 | `test_personality_hint_is_shell_safe` (test_app_flow_d) | `shlex.quote(personality)` in the warning, shortened to 200 characters with "…" |
| 10 | `test_after_start_failures_name_the_subject` (test_app_flow_d) | `_spent(exc, media_ids, project_id, about=None)`: edit passes `f"editing {media_id}"`, body passes `f"the body of character {cid}"` |
| 11 | `test_a_record_without_a_workflow_is_named_as_such` (test_app_flow_d) | `flow_edit` and `flow_extend` check kind, status and length before `_workflow_of`. A missing workflow raises `BrowserError("Flow did not name the editor for media X; nothing was spent")`. `media_details` returns the record whose id matches |
| 12 | (rename) | `flow_edit(…, instruction)` becomes `text` internally, so it no longer shadows `instruction()` |
