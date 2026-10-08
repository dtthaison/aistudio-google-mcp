# Plan G: Measured Direct-Spend Actions (Character Models, Body, Edit)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle: write the named test, watch it fail, fix, run the suite.

**Goal:** Turn the measured paid actions into commands:
- `character create -m nano-banana-2.1|nano-banana-pro`;
- `character body <id> PROMPT`;
- `flow edit <video-id> INSTRUCTION --max-credits N`.

The user asked for "go head all" and approved the measurements.

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md`, §14 (with §10.2, the direct-spend rules). Evidence: `docs/wire/findings.md`, "Flow paid measurements".

## Global Constraints

- **Direct spends refuse before any click** unless the table price for the label shown is covered by `--max-credits` (a price of 0 needs no flag).
- **Credits are read before and after.** A different charge is a warning.
- **A failure after Start names the media or character and forbids a re-run.** A failure before Start keeps its own message (`page.started`).
- **Housekeeping:** no secrets in output, ruff is clean, and the suite is green after each task.

## Review Focus

1. **`flow edit` on a 6-second clip:** refused ("no measured price"), nothing clicked.
2. **`flow edit` when the box names another model than Omni 1.1 Flash:** refused before Start.
3. **The edit's new clip** is told apart from the source clip and older clips, by creation time after Start and by id not known before.
4. **`character create -m nano-banana-pro` when the picker does not switch:** refused, nothing generated.
5. **`character body` on a character whose page model is Pro (not measured for body):** refused before Start.

---

### Task 1: Prices and character model choice

| # | Test | Fix |
|---|---|---|
| 1 | `test_measured_prices` (test_flow_models) | `PRICES` adds `("character", "Nano Banana Pro"): 0`, `("body", "Nano Banana 2.1"): 0` and `("edit", "Omni 1.1 Flash"): 20`. `check_direct_spend` is unchanged |
| 2 | `test_choose_character_model_reads_the_picker_back` (test_flow_page_b) | `FlowPage.choose_character_model(label)`: click the picker, then the menuitem `…label$`, then read the label back. A different label is a `BrowserError` ("nothing was spent") |
| 3 | `test_character_with_a_chosen_model` (test_app_flow_d) | `flow_character(model=None)`: `find_model(model, "image")` runs before the browser. After `open_character`, choose the model if the label differs, then price it with the label read back |

### Task 2: Character body

| # | Test | Fix |
|---|---|---|
| 4 | `test_body_types_into_the_body_prompt_and_reads_the_image` (test_flow_page_d) | `FlowPage.open_body(pid, cid) -> label`: go to the character page, read the picker, then click "Create body". `start_body(prompt) -> list[FlowMedia]`: type into the last editor, wait for the prompt, set `started`, click the last Start, then wait for `ogiZ0b` media (BrowserError naming the character when missing) |
| 5 | `test_character_body_is_priced_and_downloaded` (test_app_flow_d) | `flow_character_body(profile, cid, prompt, *, max_credits, project, out, overwrite, …)`: check the character exists, call `open_body`, `check_direct_spend("body", label)`, `start_body`, read credits, save the image. Before Start, failures stay as they are; after Start they name the character |
| 6 | (CLI) | `flow character body CHARACTER_ID PROMPT [--max-credits] [--project] [-o] [--force] [--visible] [--json]` |

### Task 3: Omni edit

| # | Test | Fix |
|---|---|---|
| 7 | `test_edit_label_reads_the_edit_box_model` (test_flow_page_b) | `FlowPage.edit_label(pid, workflow_id)`: go to the editor, check the URL, and read the model from the body text: `Describe how to edit this video… add pen_magic <model> arrow_forward` |
| 8 | `test_edit_follows_only_the_new_clip` (test_flow_page_b) | `FlowPage.edit(prompt, *, timeout_s) -> FlowOutcome`: type into the last editor, wait, set `started`, click the last Start, then `_follow(log, 1, timeout_s, "video", since)` |
| 9 | `test_edit_is_priced_by_model_and_length` (test_app_flow_d) | `flow_edit(profile, media_id, instruction, *, max_credits, project, out, overwrite, timeout_s, …)`: the source must be a finished video with a workflow. The price comes from `("edit", label)`, only for an 8 s source (otherwise refuse with "no measured price for N-second clips"). Follow the extend pattern for credits, the timeout result and the never-lose rule |
| 10 | (CLI) | `flow edit MEDIA_ID INSTRUCTION [--max-credits] [--project] [-o] [--force] [--timeout] [--visible] [--json]` |

### Task 4: Docs and live checks

| # | Test | Fix |
|---|---|---|
| 11 | (docs) | README and SKILL.md: the new commands with their prices; an edit costs 20 credits and gives no quote (ask the user first) |
| 12 | live (free) | `character create -m nano-banana-pro` gives 0 credits. `character body` on "Paper boat" gives 0 credits and an image. `flow edit` is checked with `--max-credits 0`: `OVER_BUDGET` before any click. A real edit (20 credits) runs only with the user's OK |
