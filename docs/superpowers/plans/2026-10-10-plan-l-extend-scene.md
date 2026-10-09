# Plan L: Extend a Clip That Is Itself an Extension

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Each item is one RED→GREEN cycle.

**Goal:** `aistudio flow extend <id>` works when `<id>` is an extension, so extensions can be
chained. (User: "yes", to "build it into the CLI properly".)

**Spec:** `docs/superpowers/specs/2026-10-07-flow-generation-design.md` §3 and §10.7 (`flow extend`).
**Evidence:** `docs/wire/findings.md`, "Extending an extension" (2026-10-09).
- The extension clip has no editor of its own.
- `/project/<pid>/scene/<scene-id>` offers "Add clip" → "Extend (Veo 3.1 - Lite)".
- The scene opens with the first clip selected. Clips are `div.clip`, marked `is-first`,
  `is-last` and `selected`. Clicking the last one selects it, and the side panel then shows its
  prompt.

## Global Constraints

- Extend is a direct spend (10 credits, Lite). Every check runs before Start, and a failed check
  says "nothing was spent".
- No live spend in this plan: the scene path was proven live by the approved 10-credit run. The
  live check here is free (open the scene, select its last clip, read Extend's label, stop).

## Review Focus

1. The scene path never presses Start unless the scene's last clip is the selected one and the
   panel shows that clip's prompt.
2. A clip that is listed in the project's workflows keeps the clip-editor path, even when it is
   the last clip of a scene.
3. A clip with no editor that is not a scene's last clip gets an error that names what to do
   ("extend the last clip of its scene"), never a raw selector error.
4. The scene path reads Extend's model the same way, so the price check is unchanged.

---

### Task 1

| # | Test | Change |
|---|---|---|
| 1 | `test_click_selector_clicks_the_first_match` (test_ui_roles) | `Ui.click_selector(selector)`: Playwright `locator(selector).first.click()` through `_act` |
| 2 | `test_a_scene_extension_selects_the_last_clip_first` (test_flow_page_b) | `FlowPage.extend_label_in_scene(project_id, scene_id, last_prompt)`: open the scene, check the URL, `click_selector(".clip.is-last .clip-body")`, `wait_for(".clip.is-last.selected")`, check the panel shows the first 40 characters of the prompt, then the shared Add clip → Extend label read |
| 3 | `test_a_scene_extension_is_refused_unless_the_last_clip_is_selected` (test_flow_page_b) | Last clip not selected, or the panel lacks its prompt, or the URL differs: BrowserError "…; nothing was spent", and Add clip is never pressed |
| 4 | `test_a_clip_without_an_editor_says_what_to_do` (test_flow_page_b) | `extend_label`: a missing editor box gives BrowserError "Flow has no editor for this clip … extend the last clip of its scene …; nothing was spent" |
| 5 | `test_an_extension_is_extended_through_its_scene` (test_app_flow_d) | `flow_extend`: a clip not in `contents.workflows` that is the last clip of a scene uses `extend_label_in_scene(project, scene.id, source.prompt)`; otherwise the clip-editor path (unchanged) |
| 6 | (docs) | findings "Extending an extension" marked done; SKILL: chained extends work on the newest clip |
