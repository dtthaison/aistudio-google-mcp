# Google Flow Generation (sub-project 1) — Design

**Date:** 2026-10-07
**Status:** approved in conversation (sections 1–3); written spec awaiting review
**Evidence:** `docs/wire/findings.md`, section "Google Flow (flow.google.com) discovery"
**Parent spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`. This spec adds a
`flow` command group. AI Studio's own video page (Veo) asks for a paid upgrade, so Flow is how
the CLI makes video.

## 1. Purpose

From a terminal or any Claude Code session (through the `aistudio` skill), a person or agent can
make Google Flow videos and images. They can use every generation option Flow offers, plus
reusable characters, extended clips and stitched scenes. They see the credit cost before
anything is spent, and they get the files on disk. Like the rest of the CLI, it uses the saved
Google session in a minimized Chrome and **never a Gemini API key**.

Success criteria:
- Every Flow generation option listed in §3 can be set by a flag and is verified before credits
  are spent.
- A command without `--max-credits` never spends credits.
- A paid result is never lost: every outcome either saves the files or names the media ids so
  they can be downloaded later.
- The person's Flow defaults and other projects are left as they were.

## 2. Scope

The user split "all of Flow" into sub-projects. This spec covers **sub-project 1: the generation
core plus Characters & Scenes**:
- video and image generation with all generation options;
- projects, credits, media listing and download;
- creating characters and using them;
- extending a clip;
- stitching clips into a scene.

Out of scope, each its own later sub-project: **Flow Music**, **Tools**
(My Tools / Community / Templates) and **Flow TV**. Also out: Flow's "Always approve" setting
(the CLI never turns it on) and editing videos in Flow's editor beyond the trims a scene needs.

## 3. Commands

```
aistudio flow credits                                    # Flow credits left (free)
aistudio flow projects                                   # Flow projects (free)
aistudio flow media [--project P] [--type video|image]   # generated media (free)
aistudio flow download <media-id> -o PATH                # (re-)download media (free)

aistudio flow video  (PROMPT | --prompt-file F)
     [-m omni-1.1-flash | veo-3.1-lite | veo-3.1-fast | veo-3.1-quality]
     [--aspect 16:9|9:16] [--count 1-4] [--duration SECONDS]
     [--start-frame IMG] [--end-frame IMG]
     [--ingredient FILE|media-id]... [--character NAME]...
aistudio flow image  (PROMPT | --prompt-file F)
     [-m nano-banana-2.1 | nano-banana-pro | nano-banana-2-lite]
     [--aspect 16:9|4:3|1:1|3:4|9:16] [--count 1-4]
     [--ingredient FILE|media-id]... [--character NAME]...
aistudio flow extend <video-media-id> (PROMPT | --prompt-file F) [--duration SECONDS]
aistudio flow scene  <media-id>[@START-END]... -o out.mp4
aistudio flow character create NAME (PROMPT | --photo FILE) [-m nano-banana-…]
aistudio flow character list

Options for every generating command (video, image, extend, scene, character create):
  --max-credits N  --project P  -o PATH  --force  --timeout SECONDS  --visible  --json
```

- **Model values.** These are the CLI ids. `_flow/models.py` maps each one to Flow's label and
  internal key, e.g. `veo-3.1-lite` ↔ "Veo 3.1 - Lite" ↔ `veo_3_1_t2v_lite`. When `-m` is
  left out, Flow's current default is used and named in the result.
- **Option checks.** Aspect, count and duration are checked against the model table before
  Chrome opens. The table's duration and resolution rules are filled in by the discovery tasks
  in §9.
- **Project.** A Flow project titled **"aistudio"** is created once and reused. `--project`
  takes a project id, an id prefix or an exact title; every write goes there.
- **Input files.** `--prompt-file` follows the existing rules (UTF-8, at most 1 MB).
  Frame, ingredient and photo files must exist, be images (videos for video ingredients), and be
  readable before Chrome opens.
- **Output.** `-o` is a file for a single output and a folder for several. The default is the
  current folder with names `flow-<kind>-<YYYYmmdd-HHMMSS>[-N].<ext>`, with the extension taken
  from the returned type.

### 3.1 Spending

Flow's agent quotes a price and waits ("Would you like me to kick off this 1 video generation,
costing 10 credits?" with Approve / Always approve / Reject). The CLI decides with a pure
function:

```
decide(quote, plan, flags, max_credits) -> QUOTE_ONLY | APPROVE | REJECT_OVER_BUDGET | REJECT_MISMATCH
```

| Condition | Action | Result |
|---|---|---|
| `max_credits` is None | press Reject (amended, §10.6) | `status: "quoted"`, `declined: true`, exit 0 |
| the quoted count/kind, or a plan the agent states, differs from `flags` (§10.1) | press Reject | `PLAN_MISMATCH`, exit 1 |
| `quote.credits > max_credits` | press Reject | `OVER_BUDGET`, exit 1 |
| otherwise | press Approve | continue to the job |

Approve is pressed only when exactly one `role=radio` named "Approve" exists inside the
permission message. "Always approve" is never pressed.

### 3.2 Results (`--json`)

```
quoted:  {"status": "quoted", "credits": 10, "plan": "1 video … Veo 3.1 - Lite",
          "project": "<id>", "session": "<id>"}
done:    {"status": "done", "files": [{"path", "mime_type", "bytes", "media_id"}],
          "credits_spent": 10, "credits_left": 1040, "model": "veo-3.1-lite",
          "project": "<id>", "warnings"?: [...]}
timeout: {"status": "timeout", "media_ids": [...], "project_url": "...", "credits_spent": 10}  # exit 2
character create → {"status": "done", "character": {"name", "media_id"}, "files": [...], ...}
character list   → {"characters": [{"name", "media_id"}], "count"}
credits → {"credits": 1040};  projects → {"projects": [{"id", "title", "updated"}], "count"}
media   → {"media": [{"media_id", "kind", "model", "prompt", "created", "seconds"?}], "count"}
```

Human output prints the same facts. Warnings go to stderr.

## 4. Approach

Flow's main page is agent-only, and every generation needs a fresh reCAPTCHA token from the
page. So the CLI **drives the agent and verifies before approving** (approach A):
- Set this run's options in Flow's own controls (the tune panel) and restore them afterwards.
- Send an explicit instruction built from the flags.
- Approve only when the quote fits the budget and the agent's plan matches the flags.

Characters use their direct page. Calling the RPCs over plain HTTP was rejected because of the
reCAPTCHA token, and direct editors alone were rejected because they don't offer text-to-video.

## 5. Architecture

| Unit | Responsibility | Depends on |
|---|---|---|
| `_flow/wire.py` | `batchexecute` framing (`)]}'`, lengths, `wrb.fr` envelopes, JSON inside strings) | — |
| `_flow/rows.py` | Decoders for media records (`jwpduf`, `as29s`, `bOKtO`), credits (`jwpduf[1]`), projects, and the quote and plan from agent text | `_flow/wire` |
| `_flow/models.py` | The model table: CLI id, label, internal key, kind, and allowed aspects, counts and lengths | — |
| `_flow/page.py` | `FlowPage(ui)`: open or create the project; read and set tune defaults (returns the previous ones); type the prompt; attach frames, ingredients and characters; submit; wait for the permission message; approve or reject; collect media responses | `Ui` |
| `_flow/character_page.py`, `_flow/scene_page.py` | Character creation page; scene editor | `Ui` |
| `_app/flow.py` | Use cases; `decide()`; building the instruction text; restoring defaults in `finally` | the above |
| `_media/save.py` | The never-lose writer, moved from `_app/media.py`; AI Studio and Flow share it | — |
| `cli/flow_cmd.py` | The click group, flag checks, output | `_app/flow` |

Changes to existing layers:
- `Ui.type_text(selector, text)`, which focuses an element and types into it with the keyboard
  (rich-text editors ignore `fill`);
- a signed-link download for `https://flow-content.google/` only, with no cookies, following no
  redirects to other hosts, and allowing only 2xx responses.

## 6. Data flow (`flow video`; image, extend and scene differ only where noted)

1. **Before Chrome opens:** check the prompt, flags, files, output path and budget.
2. Open a minimized Chrome. Open the target project, creating "aistudio" once if needed.
3. **Tune.** Read the current defaults, then set this run's model, aspect and count. Both are
   done with the keyboard, and the page is reloaded afterwards to close the panel.
4. Attach start/end frames, ingredients (uploads or media ids) and characters.
5. Type the instruction, e.g. "Generate exactly 1 8-second video with Veo 3.1 - Lite. Do not make
   a storyboard or any other clips. Prompt: …", and submit.
6. Wait for the permission message (or an agent reply that is not a quote; see §7). Parse the
   quote and the plan, then call `decide()` and act on the result.
7. **On approve:** follow the job through the page's own media responses until status 3 (done),
   a failure, or `--timeout`. Collect the media ids and signed links.
8. **In `finally`:** restore the tune defaults and close Chrome.
9. Download the signed links over plain HTTP, save each file with the never-lose writer, and
   report `credits_left` from the last `jwpduf`.

Per command:
- **extend:** submits an extend instruction for `<video-media-id>`.
- **scene:** drives the scene editor (clips, trims) and downloads the combined video.
- **character create:** uses the character page (name, prompt or photo, model family).

The same §3.1 rule applies to any of these when Flow asks to spend.

## 7. Errors

Every error uses the existing JSON envelope. Nothing is spent unless noted.

| Situation | Code / status | Notes |
|---|---|---|
| No `--max-credits` | `status: "quoted"`, exit 0 | — |
| Over budget | `OVER_BUDGET` (new), exit 1 | rejected in Flow; hint gives the quote |
| Plan differs from flags | `PLAN_MISMATCH` (new), exit 1 | rejected; shows both versions |
| Agent asks a question instead of quoting | `AGENT_REPLY` (new), exit 1 | the agent's text; hint: rephrase or add flags |
| Not enough credits | `QUOTA_EXCEEDED` | — |
| reCAPTCHA or Flow refuses | `BOTGUARD_REJECTED` | hint `--visible` |
| Signed out | `AUTH_ERROR` | — |
| Job fails (e.g. a safety filter) | `SERVER_ERROR` | Flow's reason, and credits before and after |
| `--timeout` passes during the job | `status: "timeout"`, exit 2 | **spent**: media ids and project link; `flow download` later |
| Download fails after the job | `NETWORK_ERROR` / `SERVER_ERROR` | **spent**: media ids and a `flow download` hint |
| Restoring tune defaults fails | warning in the result | names the setting to fix |
| Another browser command is running | existing profile-in-use `BROWSER_ERROR` | — |

## 8. Safety

- Credits are spent only through `decide()` returning APPROVE, with an explicit `--max-credits`
  and a single "Approve" radio.
- Tune defaults are restored in `finally`. Your other projects are never written to; only the
  "aistudio" project or the `--project` one is.
- The never-lose rule covers every downloaded file.
- No tokens, cookies, signed-link signatures or XSRF values go into logs, fixtures or output.
  Signed links appear in output only as media ids, never as the link itself.
- The Claude Code skill tells agents to pass `--max-credits` only with a budget the user stated.
  A plain `flow video` is safe because it only quotes.

## 9. Testing and discovery

- **Unit, test-first:**
  - decoders run against synthetic fixtures built from the recorded shapes (tokens and
    signatures replaced);
  - the full `decide()` matrix (no budget / under / at / over / mismatch on each field /
    several outputs);
  - model-table validation;
  - `FlowPage`, `CharacterPage` and `ScenePage` with `FakeUi`;
  - the use cases with a fake browser and a fake downloader;
  - the CLI commands.
- **Free live tests (`live`):** credits, projects, media list, and a quote-only run that ends
  with Reject so nothing is left waiting.
- **Paid live tests (`live_slow`), each run only with the user's OK:** one Lite video (about 10
  credits), one image, one character, one extend, one scene.
- **Discovery before code**, as the first step of the matching plan task. Each is a free probe
  first, and one approved real run where credits are involved. Results go into
  `docs/wire/findings.md`:
  1. extend: the controls and how the agent words the request;
  2. the scene editor: adding clips, trims, export or download;
  3. character creation: naming, photo upload, Format options, cost prompt;
  4. ingredient and frame upload;
  5. whether the agent's plan is available as structured data in `StreamChat` (otherwise parse
     its text);
  6. allowed length and resolution per model.

  Until an item is mapped, its command is not shipped. Each plan task ends with that command
  working against real evidence.

## 10. Amendments from plan A and plan B discovery (2026-10-08)

Evidence: `docs/wire/findings.md`, Flow sections dated 2026-10-07 and 2026-10-08. The user's
decisions are recorded in the conversation.

1. **Checking the plan (§3.1).** Flow's permission request carries only counts and cost. The
   model, aspect and outputs are guaranteed by the saved and re-read tune settings, a plan the
   agent *states* must match, and a different length is reported after the job.
2. **Direct-spend actions.** Character creation (`C4BZMd` + `ogiZ0b`) and Extend (`rqZuUc` +
   `fZytfe`) spend without a Flow quote. They use a **measured price table** kept in
   `_flow/models.py`. The table holds: character on Nano Banana 2.1, 0 credits; extend with
   Veo 3.1 Lite, 10 credits.
   - The command refuses unless `--max-credits` covers the table price. With a price of 0,
     `--max-credits` is not needed.
   - Credits are read before and after. A difference from the table price is reported as a
     warning.
   - An action with no table price (e.g. extend on Veo 3.1 Fast) is refused until it has been
     measured.
3. **Uploads.** A local file for `--start-frame`, `--end-frame`, `--ingredient` or `--photo` is
   uploaded through **+ → Upload media**. Flow's "Rights to use this image" dialog is accepted
   only with `--i-have-rights` for that run; without it the command stops before uploading. The
   Claude Code skill tells agents to ask the user first.
4. **Attachments** are Flow media ids. They are attached with a tile's "Add to prompt" (or as the
   upload's result), and the instruction names their role (start frame, end frame, ingredient).
5. **Extend** produces a new clip and a scene joining the source and the extension. `flow
   extend` downloads the new clip and reports both ids. Stitching scenes (`flow scene`) moves to
   a later plan; its "Download scene" output has not been mapped yet.
6. **Quote-only runs decline Flow's request** (plan C). A request left waiting could later be
   approved in Flow with the restored settings rather than the quoted ones. The result is
   unchanged, plus `"declined": true`.
7. **Result fields** (plan C):
   - `model` is the CLI id (e.g. `veo-3.1-lite`) whenever aistudio knows the label;
   - `flow extend` adds `scene_id`;
   - `flow media` items carry `prompt`;
   - `flow projects` reports `created`, because Flow lists no update time.
