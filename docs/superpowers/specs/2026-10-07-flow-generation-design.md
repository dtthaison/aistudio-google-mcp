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
quoted:  {"status": "quoted", "declined": true, "credits": 10, "count": 1,
          "plan": "1 video … Veo 3.1 - Lite", "project": "<id>", "uploads"?: [...],
          "warnings"?: [...]}
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
| No `--max-credits` | `status: "quoted"`, `declined: true`, exit 0 | Flow's request is rejected (§10.6) |
| Reject fails on a quote-only run | `status: "quoted"`, `declined: false`, exit 0 | the quote, plus a warning that Flow's request is still waiting and must be rejected there, not approved |
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
   unchanged, plus `"declined": true`. If Reject itself fails, the quote is still returned with
   `"declined": false` and a warning to reject the waiting request in Flow (final review I3).
7. **Result fields** (plan C):
   - `model` is the CLI id (e.g. `veo-3.1-lite`) whenever aistudio knows the label;
   - `flow extend` adds `scene_id`;
   - `flow media` items carry `prompt`;
   - `flow projects` reports `created`, because Flow lists no update time.

## 11. Amendments from plan D discovery (2026-10-08)

Evidence: `docs/wire/findings.md`, "Flow plan D discovery". The user asked for every remaining
feature ("go ahead all the issues an unbuilt"). Everything new here is free, except attaching a
character to `flow video`/`flow image`, which keeps the normal quote-and-budget path (§3.1).

1. **Commands** (these replace the `flow scene` and `flow character` lines of §3):

   ```
   aistudio flow character create NAME (DESCRIPTION | --photo FILE|media-id) [--i-have-rights]
   aistudio flow character list
   aistudio flow character rename <character-id> NAME
   aistudio flow scene list
   aistudio flow scene stitch <media-id>[@START-END]... -o out.mp4
   aistudio flow scene download <scene-id> -o out.mp4
   aistudio flow video|image … [--character NAME|character-id]...
   ```

   - `flow scene` becomes a group, because it now has three subcommands.
   - `character create` takes no `-m`. Only Nano Banana 2.1 has a measured price, and the price
     table (§10.2) still refuses any other picker model.
2. **Names.** A character name is unique in its project, compared case-insensitively.
   - `create` and `rename` refuse a name that another character already has.
   - `rename` sends `rzMKMb` (findings), then reads the name back.
   - `create` from a description names the new character the same way, after its image is made.
3. **Character from a photo.** `--photo` takes a Flow image media id or a local image.
   - A local image is uploaded first, under the §10.3 rule (`--i-have-rights`).
   - aistudio then sends `C4BZMd` (create, with the name) and `Sc7aEb` (link the photo) **from
     the page**, as it does for `as29s`.
     - The photo picker knows photos only by file name, but these RPCs take the exact media id.
     - Neither RPC carries a reCAPTCHA token.
     - Generation RPCs (`StreamChat`, `ogiZ0b`, `fZytfe`) stay page-driven.
   - Nothing is generated and it is free. Credits are still read before and after, and any change
     is reported as a warning.
   - The result has `character_id`, `name`, `media_id` (the character's image) and `files` (that
     image, downloaded).
4. **Characters as ingredients.** `--character` resolves a name or an id against the project's
   characters before anything is sent.
   - Flow's panel shows characters by name only, so the name must be unique. A character
     without an image is refused.
   - Each one is attached from the + panel's **Characters** tab, and the instruction names it:
     "Feature the attached characters: NAME, …".
   - After the free quote, the `StreamChat` request is checked. Its character slot must hold
     exactly the requested ids. Otherwise the request is rejected and the run stops with
     `PLAN_MISMATCH`.
5. **Stitching.** `scene stitch` sends `GjQLTd` with clips `[media_id, null, [start], [end]]` in
   the given order, polls `IxdSwd` until it reports done, and writes the base64 MP4 it returns.
   - It is free and needs no scene record.
   - **Trims:** whole seconds. `@START-END` must satisfy 0 ≤ START < END ≤ the clip's length.
     Without a trim, the whole clip is used (`[]` to `[length]`). Verified live: trims 2–5 and
     0–3 gave a 6.0 s file.
   - **Checks:** every id must be a finished video in the project, and there must be at least
     two clips.
   - `scene download` takes the scene's clips from the project contents, in position order and
     with the scene's own trims (`[6]` slots `[2]`/`[3]`), as Flow's "Download scene" sends them
     (plan D review ruling).
   - A stitch that is still running at `--timeout` (default 300 s) stops with `TIMEOUT`, exit 2,
     like every `TIMEOUT` error. Its hint says nothing was spent (plan D review ruling).
   - Any state other than running or done is a `SERVER_ERROR`.
6. **Lists.**
   - `character list` gives `id`, `name`, `media_id` (null without an image) and `created`.
   - `scene list` gives `id`, `title`, `created` and `clips` (`media_id`, `title`, `seconds`) in
     order.
7. **Extend's `scene_id`** comes from the project's scene clips after the job (the scene holding
   the new clip). The `rqZuUc` reply is used only if the contents do not show it, because that
   reply's shape was never seen live (plan C review M2).

## 12. Amendments from plan E (2026-10-08)

Evidence: `docs/wire/findings.md`, "Flow plan E discovery". The user asked for all the follow-ups
("go head all").

1. **Hidden copies.** Characters' images (their `media_id`, including the copies Flow makes
   from photos) are not tiles. They are left out when attachment titles are checked for
   duplicates, so a photo used for a character can still be a frame or ingredient by id.
2. **Attachments are verified by id.** After the free quote, the media ids in the `StreamChat`
   request's slot 1 must be exactly the attached ones (uploads by their new id). Otherwise the
   request is rejected and the run stops with `PLAN_MISMATCH`, as for characters (§11.4).
3. **Names compare with spaces collapsed** and case-insensitively. A character listed without a
   name is shown as "(unnamed)" and can be renamed.
4. **Stitching gives up** on 5 replies in a row that fail (HTTP ≥ 400 or unreadable), with a
   `BROWSER_ERROR` naming the last status, rather than waiting for `--timeout`.
5. **File types.** An image whose bytes are AVIF or HEIC (`ftyp` brands) is saved as `.avif` or
   `.heic`. Unrecognised image bytes keep the requested name and get a warning, instead of being
   called JPEG.

## 13. Amendments from plan F (2026-10-08)

Evidence: `docs/wire/findings.md`, "Flow plan F discovery". These follow the plan E review and
the paid `--character` check.

1. **Free generations.** When Flow's agent generates without asking (free models, such as
   Nano Banana 2.1 images), the `StreamChat` stream carries `generate_*` tool results with media
   ids.
   - aistudio follows those media, downloads them, and returns `status: "done"` with the warning
     "Flow generated without asking (free model)".
   - It reads credits before and after. Any charge appears as `credits_spent` with a warning. A
     run without `--max-credits` can still produce these free results, because Flow does not
     ask.
   - A requested model, count or aspect that differs from the tool call's becomes a warning.
2. **Credits around every run.** `flow video|image` reads credits before the run. For a quoted
   run, a charge different from the quote becomes a warning.
3. **Approve and Reject** act only on the enabled (waiting) permission radios. Approve also
   requires that the waiting request shows the checked cost. Already shipped by the review
   fixes; recorded here.
4. **Personality.** `character create … --personality TEXT` and
   `character personality <character-id> TEXT` send `rzMKMb` with
   `entity_info.character_info.personality_notes`. It is free.
5. **Out of scope (findings):**
   - avatars, which need a recorded selfie video;
   - Flow Music, a separate site;
   - Tools, a builder;
   - voice choice, which has no save call.

   **Paid, pending the user's budget:** Omni video edits, extend on Veo 3.1 Fast and Quality, the
   other character models (Nano Banana Pro and 2 Lite), and "Create body".
6. **Unnamed characters** cannot be attached with `--character`: rename them first.

## 14. Amendments from plan G: measured direct-spend actions (2026-10-08)

Evidence: `docs/wire/findings.md`, "Flow paid measurements" (the user approved those runs).

1. **Price table additions:**
   - character on Nano Banana Pro, 0;
   - "Create body" on Nano Banana 2.1, 0;
   - Omni 1.1 Flash edit of an 8-second clip, 20.

   An edit of a clip of any other length has no measured price and is refused. Nano Banana 2
   Lite's first reading was unclear; a clean re-measure gave 0 (item 6).
2. **`character create -m nano-banana-2.1|nano-banana-pro`.** aistudio picks that model in the
   character page's "Select model family", reads the picker back, and prices the action with the
   label shown.
3. **`character body <character-id> PROMPT`** generates a body image for the character with the
   page's model ("Create body"). It is priced from the table (0 on Nano Banana 2.1) and
   downloaded like a character image.
4. **`flow edit <video-media-id> INSTRUCTION --max-credits N`** opens the clip's editor by its
   workflow id and reads the edit model from the box ("Omni 1.1 Flash").
   - It is priced from the table by model and source length. The edit is a direct spend: no
     quote, so `--max-credits` must cover the table price.
   - It types the instruction and presses Start, then follows the new clip (created after Start).
   - It reads credits before and after, and downloads the result. A failure after Start names the
     clip and forbids a re-run.
5. **Extend is always Veo 3.1 Lite.** A Fast clip's Extend menu also offered "Extend (Veo 3.1 -
   Lite)". Flow has no Fast or Quality extend, so none is offered (findings).
6. **Nano Banana 2 Lite characters** were measured cleanly at 0 credits. `-m nano-banana-2-lite`
   is a character-only choice, because Flow's image settings do not offer 2 Lite.
