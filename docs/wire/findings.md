# M1 discovery findings

- Date: 2026-10-06
- Code at capture time: `107829f` (capture), `ca4f037` (final redactor)
- Fixtures: `tests/fixtures/` (92 files, 39 methods, 1.6 MB), redacted and leak-checked twice
- Account: one consumer Google account, `authuser=0`

## What was exercised

The user's capture session exercised:

- page load;
- Build mode (applet creation);
- token counting;
- **image generation** (`gemini-3.1-flash-lite-image`);
- **TTS** (`gemini-3.8-flash-tts`, voice "Fola");
- saving that chat as a prompt (`CreatePrompt`).

The session did **not** exercise:

- a plain Gemini text chat (with or without Search, code execution or thinking);
- Gemma;
- prompt rename or delete;
- the model picker chips, or starring a model;
- image editing or transcription;
- video or music generation.

## Hypotheses (spec §12)

| # | Hypothesis | Result | Evidence |
|---|---|---|---|
| H1 | X-Goog-Api-Key is a static web key | **true** | `aistudio login` captured it from the `x-goog-api-key` header of live MakerSuite requests (`api_key_captured: true`). 54 replays signed with it returned 200. |
| H2 | Prompt-library RPCs do not need BotGuard | **likely true** | **Reads:** `ListPrompts` replayed over plain HTTP returned `http-ok` 4/4. **Writes:** `CreatePrompt` replayed returned `http-reached`, a 400 `VALIDATION_ERROR` about base64 decoding. The failure came from the fixture's media, which the pre-fix redactor corrupted. Auth and transport were accepted with no 403 or BotGuard rejection. Not observed: `UpdatePrompt` and `DeletePrompt`. |
| H3 | Errors use `[,[code,"msg"]]` | **true** | The 403 and 400 replies decoded through `wire.parse_error`, with messages such as "The caller does not have permission" and "Invalid value at 'prompt.chunked_prompt…'". |
| H4 | BotGuard token not bound to content | **false** (follow-up session) | `scripts/spikes/tts_rewrite.py` on the speech page: the swapped text with a fresh page-minted token got 403 `[,[7,"The caller does not have permission"]]`. The control (same path, original bytes) got 200 with 2.08 s of audio. The token covers the request content. |
| H5 | Video returns a pollable operation | not observed | No video was generated. |
| H6 | Inline attachments accepted | partial | `CreatePrompt` carries images inline as `prompt.chunked_prompt.chunks[].inline_image.data`, which is base64 bytes. Upload paths for image editing and transcription were not observed. |
| H7 | CountTokens and ListModels work over HTTP | **true** | Both returned `http-ok`: `CountTokens` 4/4 and `ListModels` 2/2. |
| H8 | Catalog metadata comes from one RPC | **likely true** | One `ListModels` response (99 KB) lists 86 models across all picker categories: Gemini, Gemma, image (`nano-banana*`, `*-image`), Veo, TTS, transcribe, Live, Lyria (`lyria-3.5`, `lyria-3-*`), and agents (`antigravity-preview-*`, `deep-research-*`). It also contains price strings (`$`), paid markers and knowledge cutoffs. M2 must still map the field indices. |
| H9 | Lyria song generation is request/job | not observed | `lyria-3.5` is present in the catalog, but no music was generated. |
| H10 | Stars are server-side | **true** (follow-up session) | `UpdateUserPreferences` with field `[6]` = `[["models/<id>", [seconds, nanos]]]` and field mask `["starred_models"]`. Unstar sends an empty list with the same mask. |

## Transport per RPC (plain-HTTP replay, signed by `aistudio`)

| Method | Read-only | Replay verdict | Transport | Fixture |
|---|---|---|---|---|
| ListModels | yes | http-ok | http | ListModels/1–2 |
| ListPrompts | yes | http-ok | http | ListPrompts/1–4 |
| CountTokens | yes | http-ok | http | CountTokens/1–4 |
| GetUserPreferences | yes | http-ok | http | GetUserPreferences/1–2 |
| GetAiStudioBenefitTier, GetGdpProfileAndBenefits, GetLoggingContext, GetWorkspaceAppConfig, GetAppletGalleryConfig, GetPendingArenaInteraction | yes | http-ok | http | respective `/1` |
| ListApplets, ListRecentApplets, ListBundledApplets, ListAppletSecrets, ListUnsetAppletSecrets, ListAppletTrajectories | yes | http-ok | http (Phase 3 Build) | respective |
| ListIncidentsHistory, ListPromos, ListCodeAssistantFeatures, ListCloudProjects | yes | http-ok | http | respective |
| ListCloudApiKeys | yes | http-rejected (403, status 7) | out of scope (Dashboard) | ListCloudApiKeys/1 |
| ListCodeGenSuggestionCards | yes | http-rejected (403, status 7) | out of scope (Build) | ListCodeGenSuggestionCards/1 |
| CreatePrompt | no | http-reached (400, status 3) | http (likely) | CreatePrompt/1 |
| GenerateContent | no | http-rejected (403, status 7) with a stale token | **browser** | GenerateContent/1–2 |
| GenerateAccessToken, GenerateTitle, CreateApplet, ApplyFileSystemOperation, AutocompleteAppletPrompt, StoreRecentApplet, UpdateUserPreferences, CheckUserStatus, CodeAssistantOffline, Create, Log, ListImportedProjects | no/unclassified | skipped (not replayed) | undecided | respective |

`ListImportedProjects` was skipped because its name contains `Import`. The read-only classifier
is conservative on purpose.

## BotGuard

- **Token location:** the last element, index `[4]`, of the `GenerateContent` request array. It is a
  string of about 2,680 characters starting with `!`. Both captured requests (TTS and image) put it there.
- **Enforcement:** replaying `GenerateContent` with a stale token over plain HTTP returns 403
  PERMISSION_DENIED. Generation therefore needs a freshly minted token, which means the browser backend.
- **Route-rewrite spike (H4):** inconclusive, because the run timed out with no marker request.
- **`GenerateContent` scope:** the same method serves TTS (`gemini-3.8-flash-tts`, top-level length 5)
  and image generation (`gemini-3.1-flash-lite-image`, top-level length 11). The model is element `[0]`.
- **Chosen default strategy:** undecided until H4 is resolved.

## Account email

- No MakerSuite RPC response in the capture contained an email address.
- The account's display name and photo appear only as the owner field in `ListPrompts`,
  `CreatePrompt` and `ListApplets`.
- The email was only in the People API batch calls (`people-pa.clients6.google.com`). Those were
  deleted from the fixtures as personal data.
- `AccountInfo.email` therefore stays `None` unless M2 adds a People API lookup. It is not needed
  for any v1 command.

## Fixture caveats

- **Media bytes corrupted before the fix.** The pre-fix redactor corrupted base64 media in
  `CreatePrompt/1`, `GenerateContent/1` and `GenerateContent/2`, and in `Create/2`. Those runs are
  replaced with `<<base64 media omitted…>>` and `<<base64 fragment omitted>>` markers. The JSON
  structure is intact. Media payloads need a targeted re-capture before M4.
- **People API fixtures removed.** The 4 `other_people_pa_clients6_google_com_batch` fixtures were
  deleted: profile data, not AI Studio protocol.
- **Fake IDs are not wire-valid.** Redacted fixtures replace Drive and prompt IDs with `prompt_N`.
  Replays of ID-bearing requests therefore reach validation rather than succeed.

## Gate decision (spec §11)

- **Prompt-library RPCs over HTTP: yes for reads.** `ListPrompts` and `ListModels` succeed over
  plain HTTP. For writes, `CreatePrompt` passed auth and reached validation; success itself is not yet proven.
- **A BotGuard strategy works: not yet determined.** Generation is confirmed to need a fresh token (403 without one).
- **Decision: proceed to the M2 plan** (HTTP backend, model catalog, prompt library). It does not
  depend on BotGuard.
- **M3 (`ask` and generation) is gated** on three things, all done in a short session before M3 is planned:
  1. re-running the H4 spike;
  2. capturing a plain text chat;
  3. capturing prompt rename and delete.

  If neither BotGuard strategy works then, revisit spec §5.4 before M3.

## Follow-up session (2026-10-06, afternoon)

### H4 resolution: generation must be page-built

- **Swap run.** Route interception plus a text swap, with a fresh token minted by the page:
  **403**.
- **Control run.** The same interception path, sending the original bytes: **200**, audio
  returned.
- **Consequence.** Neither `RouteRewriteStrategy` (spec §5.4 #2) nor any "mint once, reuse" scheme
  can work. Generation requests must be built by the page itself, with the token minted for that
  exact content.
- **Proven route.** Drive the page UI (fill, select, Run) and capture the response from the
  network.
- **Remaining alternative.** `SnapshotCallStrategy`, calling the page's BotGuard function with our
  own body. It stays unexplored.

### TTS (speech page, `https://aistudio.google.com/generate-speech`)

- **Request** (`GenerateContent`, fixtures 1 and 3):

  | Field | Meaning |
  |---|---|
  | `[0]` | model |
  | `[1][0][0][0][1]` | text; inline expression tags such as `<gasp>`/`<laugh>` live inside it |
  | part `[40][1]` | style instruction |
  | `[3][4..6]` | temperature, topP, topK |
  | `[3][14]` | `[3]` (audio) |
  | `[3][15][0][0][0]` | voice, e.g. `Fola` |
  | `[4]` | BotGuard token |

- **Response.** A streamed list of chunks, `resp[0][i]`. Each chunk carries
  `[0][0][0][0][2] = ["audio/l16; rate=24000; channels=1", base64 PCM]` plus usage at `[2]`.
  Concatenating the PCM and adding a WAV header gives valid audio: fixture 3 is 10.56 s.
- **Page controls** (accessible names; `scripts/spikes/tts_controls.py`):
  - Text: "Create new dialog" opens the textbox "Speech block text".
  - Voice: "Speaker 1 - <voice>" opens the voice panel. The panel has a "Search voices" box;
    the filters Language, Gender, Pitch, Style and Use case; voice buttons by name (Tova, Bodi,
    Lumi, Sola, Koda, Nika, Tavi, …, with "View all N" groups); "New voice"; and sample playback.
  - Style: "Style" opens the textbox "Describe the voice style".
  - Expressions: "+ Expression".
  - Multi-speaker: "Add speech block".
  - Model: the button naming the current model opens the picker with a search box.
  - Run: "Run" (⌘↵).
  - Also present: "Get code", "Open voice settings", "Model settings".

### TTS end-to-end drive (`scripts/spikes/tts_drive.py`, `tts_voices.py`)

- **Headed Chrome works.** It drove model → voice → style → text and pressed Run (⌘↵ in
  "Speech block text"). The outgoing request carried exactly those choices (`[0]` model,
  `[3][15][0][0][0]` voice, part `[40][1]` style, text with `<laugh>`). Result: **200**, 4.64 s
  of audio.
- **Headless Chrome is refused for generation.** The identical request got **403**: BotGuard
  rejects headless. Reading the page, including the voice panel, works headless.
- **The viewport must be desktop-sized** (1600×1000). Below that the run-settings panel holding
  the model button is hidden.
- **Run by shortcut.** Clicking "Run" can be blocked by a leftover overlay. Pressing ⌘↵ (Ctrl+↵ off
  macOS) in the text box is reliable.
- **Voice panel behaviour:**
  - After clicking a voice the panel stays open; Escape closes it.
  - The speaker button then reads "Speaker 1 - <voice>".
  - Voice names come from the "Favorite <name>" / "Remove from favorites <name>" buttons.
  - 10 "View all N" groups each open a full list, with a back button.
  - Examples: Tova, Bodi, Lumi, Sola, Koda, Nika, Tavi, Kira, Ludo, Mako, Fola, Zephyr,
    Sulafat, Sadaltager, Varo.
- **Model picker.** The run-settings button label contains the current model id. The picker has a
  "Search" box. An option's label contains its model id.

### Speech style and voice-panel fixes (2026-10-07, free probes)

Each probe blocked GenerateContent inside the page (patched XHR/fetch) and read the request
body, so nothing was generated.

- **The style box saves after a delay.** Filling "Describe the voice style" then pressing ⌘↵ at
  once sent no style (0/3). After a 1–1.5 s wait: 10/11. After 3 s: 6/6. The box has its
  `input`/`blur`/`keydown` listeners from the moment it appears, and no storage changes when it
  saves, so there is no signal to wait on: `set_style` waits 3 s. When sent, the style is at
  `[1][0][0][0][40][1]`.
- **Voice cards can sit off-screen.** The voice panel is a sliding side panel with a blur
  backdrop. In a minimized window its cards were measured at x=1631 in a 1600-wide viewport. A
  pointer click there can land on the backdrop ("intercepts pointer events"). A favorited voice
  appears twice (Favorites and its group). Focusing the card and pressing Enter selected the voice
  every time (`Ui.activate`).
- **The current voice's card is named "<Voice> (Current)".** Right after the page opens the
  Speaker button has no label yet, so `select_voice` first waits for it.
- **The voice resets to Fola** on each page load: choosing a voice does not change the account.

### Image and music generation (2026-10-07)

Free probes blocked every request after Run inside the page. Then one approved real run of each
was made: one image ($0.034) and one clip ($0.04).

- **Pages.** `/generate-image` and `/generate-video` redirect to `/prompts/new_image` (Imagen) and
  `/prompts/new_video` (Veo 3.1). `/prompts/new_chat?model=<lyria id>` redirects to
  `/new_music?model=<id>`. `/prompts/new_chat?model=<gemini image model>` stays a chat page
  titled "Generate images with Nano Banana …".
- **Imagen** sends `GenerateImage = [null, prompt, <BotGuard token>, [count, null, "1:1"]]`. For
  this account it fails with HTTP 400 `[3,"Request contains an invalid argument."]` and the page
  shows "Failed to generate image". It is not offered.
- **Veo**: the page says "Upgrade to unlock Veo 3.1" / "Continue to upgrade", and Run sends
  nothing. It is not offered.
- **Gemini image models**: ⌘↵ in "Enter a prompt" sends `GenerateContent` with
  `["models/<id>", [[[[null, prompt]], "user"]], null, <config: [14] = [2, 1], [29] =
  [null, "1K"]>, <token>, …]`.
  - The response streams `[mime, base64]` at `chunk[0][0][0][0][0][2]` (`image/jpeg`, about
    450 KB), plus a large opaque signature at part `[14]`.
  - The finish reason (1) is in the last chunk. It took 15.3 s minimized.
  - No API-key prompt appeared.
  - **The run shortcut is an account setting** (2026-10-29, free probes with the request blocked
    in the page). One account's Run button reads "Run ⌘↵" (`Run keyboard_command_key
    keyboard_return`), another's reads "Run ↵" (`Run keyboard_return`). On the second, ⌘↵ in
    "Enter a prompt" sent nothing and `generate image` timed out after 180 s, while focusing Run
    and pressing Enter sent `GenerateContent`. `ImagePage.run` therefore presses the Run button.
- **Lyria**: the prompt sits behind a "Describe the music you want to generate…" button, which
  reveals the "Prompt input" box. "Generate" runs it (⌘↵ does nothing there).
  - The request is `["models/<id>", [[[[null, prompt]], "user"]], null, <config [14] = [3]>,
    <token>]`.
  - The response is a text part (`"<instrumental>"`, or the lyrics), then
    `["audio/mpeg", base64]` (MP3), then finish 1. It took 18.7 s.

## Build create/update discovery (`scripts/spikes/build_drive.py`)

- **A minimized window works.** Both create and update succeeded from a **minimized** Chrome window,
  each returning 200 from `CodeAssistantOffline`. BotGuard accepts a backgrounded browser, so the
  background-daemon design for generation is viable.
- **Create, step by step:**
  1. Fill the idea in the `/apps` prompt box and press ⌘↵.
  2. `CreateApplet` runs; it carries no BotGuard token.
  3. `CodeAssistantOffline` runs; it carries a BotGuard token.
  4. The page streams progress through `StreamCodeAssistantOfflineGeneration`.
  5. `ProvisionAndInitializeApplet` (Cloud Run preview host) and `SaveApplet` follow.
  6. The URL becomes `/apps/<uuid>`.
- **Update.** Type into the app editor's prompt box and press ⌘↵: a second `CodeAssistantOffline`.
- **Editor controls.** The editor has per-file buttons ("Open index.html", "Open src/App.tsx",
  "Open src/index.css", "Open metadata.json"), "View changes", "Restore code from this checkpoint",
  "Copy app", and sharing and publish settings. It has no "Code" tab. How file contents are
  fetched for export is still to be identified from these fixtures.
- **The builder renamed the app.** After the update the agent renamed "TapCount" to
  "Tally - Simple Big Button Counter".
- **Never recorded:**
  - `ListAppletSecrets` returns each Build app's secrets, including the provisioned
    `GEMINI_API_KEY`, **base64-encoded**.
  - Cloud console calls carry project and billing details.

  The redactor also decodes base64 and redacts any credential found inside.
- **Editor Send is disabled while loading.** On a freshly opened `/apps/<id>`:
  - The chat box ("Make changes, add new features, ask for anything") appears about 6 s after
    navigation, once `ProvisionAndInitializeApplet` and `LoadCodeAssistantInteractionHistory`
    have run.
  - Its **Send** button stays `aria-disabled=true` for about 1 s more.
  - ⌘↵ pressed in that window is silently ignored; the text stays in the box and nothing is sent.
  - `app prompt` therefore waits for Send to be enabled before submitting.
  - The `/apps` create page has no Send button, and an immediate ⌘↵ works there.
- **Changed 2026-10-29 (free probes, minimized, writes blocked in the page).**
  - The `/apps` box is now `<div role="textbox" contenteditable="plaintext-only">` ("Describe
    an app and let Gemini do the rest"), with no `<textarea>`. The editor still has a textarea.
  - `/apps` now has a **Send** button. On an account set to run with Enter ("Run ↵"), ⌘↵ there
    only sends `AutocompleteAppletPrompt`; Send sends `CreateApplet`. Both submit through Send.
  - Before `CreateApplet` the page calls `ListImportedProjects`, `ListCloudProjects` and
    `ListCloudApiKeys`, and on one account also `GenerateCloudApiKey` and `CreateCloudProject`
    (blocked in the probe, so their effect is unknown).
  - Headless, the page stops after `ListCloudApiKeys` (spinner; once "Failed to list API keys:
    permission denied"). Minimized it goes on, so headless probes of create are misleading.
  - `Waa/Create` (anti-abuse token, `waa-pa.clients6.google.com`) must not be blocked in a
    probe: without it neither create nor speech sends anything.
- **Create now builds two options (2026-10-29, one approved real `app create`).** After
  `CreateApplet` (200, app `a6af8f55…` "Tap Counter") the page sent no `CodeAssistantOffline`
  within 60 s, yet the app lists a run. Its editor shows "Option A" / "Option B" and "Waiting for
  both options to finish…", and streams several `StreamCodeAssistantOfflineGeneration` at once;
  `GetPendingArenaInteraction` and `ListCodeAssistantOfflineGenerations` load on open. The RPC
  that starts the two runs is not mapped yet. The app had no saved code when checked.
- **The two options ("arena"), mapped by free probes with every write blocked in the page.**
  - `GetPendingArenaInteraction([app])` → `[1, [[option_app, run, x], [option_app, run, x]]]`.
    Each option is its own hidden app (not in `ListApplets`; `app export` says "no app"), with
    its own run. `GetApplet(option_app)` has a snapshot time at `[0][13]` once it is built.
  - Each run streams as `StreamCodeAssistantOfflineGeneration([run, null, null, option_app, 1])`.
    The page only notices a finished option through this stream; block it and it waits forever.
  - When both are built the page shows "Pick an option to preview", "Preview Option A" and
    "Skip comparison". Previewing shows "Select Option A" and "Exit variant preview". Select
    opens "Why did you pick variant A?" (reasons: "Better instruction following", "Better design
    & aesthetics", "Better functionality"; Cancel / Submit).
  - Submit sends `ResolveArenaInteraction([app, 1, chosen_option_app, null, [s, ns] spent,
    "React", <engagement JSON: schemaVersion "arena-engagement-v6", arenaInstanceId,
    variantCount 2, outcome "PICK", …>])`. "Skip comparison" sends the same with `4` and the
    option shown first.
  - **The A/B letters are shuffled per page load**: the same app was "Option A" in one load and
    the first option of the other in the next. Choose by option app id, never by letter.
  - Unresolved, the parent app has no saved code, so `app export` fails.
- **Drive access.** `ListPrompts` for an account that never allowed AI Studio to use Google
  Drive returns `[16, "OAuth error: unauthorized_client."]`. The History page shows "Allow Drive
  access" ("Allow access to Google Drive to load Prompts"); signing in again does not help.
- **Speech is unchanged** (2026-10-29): after "Create new dialog", ⌘↵ in "Speech block text"
  sends `GenerateContent` on both a "Run ⌘↵" and a "Run ↵" account.

## Export, chat gate, status, stars (`scripts/spikes/chat_export_discovery.py`)

- **Export is plain HTTP.**
  - `GetApplet([app_id])` returns the app; `[0][6]` is the latest snapshot time `[s, ns]`.
  - `GetCodeAssistantSnapshot([app_id, null, ts])` returns `[["model", summary, _, ts, [[path, sha256], …]]]`.
  - Each file is a plain `GET https://aistudio.google.com/_/upload/<app_id>/file/<sha256>` with
    session cookies only. The body is the raw file, and its SHA-256 equals the listed hash.
  - Both RPCs replayed `http-ok`.
- **Playground chat is gated by an API key.** On a new chat (default model, with Code execution,
  Search and URL context switches all on by default), pressing Run calls `BatchGetProjectUsageLimits`
  and opens **"Link an API key"** ("Your project does not have an API key yet"; choose a project,
  "Create key and link", "Set up billing", "Link API key"). No `GenerateContent` is sent. Speech
  generation earlier did not hit this gate, so it is per model.
- **`ListIncidentsHistory`** returns incidents: `[id, title, _, [[state, "YYYY-MM-DD HH:MM",
  [epoch], message], …], severity?, [products]]`. Plain HTTP works.
- **Account tier calls are opaque.** `GetAiStudioBenefitTier` returns `[1]`,
  `GetGdpProfileAndBenefits` returns `[1]`, and `GetUserRestrictions` returns `[]`. They carry no
  user-meaningful limits, so no `account` command for now.
- **Never recorded:** `EvaluateTargeting` sends bare billing-account ids. The redactor now also
  masks bare `XXXXXX-XXXXXX-XXXXXX` ids.

## Google Flow (flow.google.com) discovery (2026-10-07)

Free probes were read-only or blocked every request after Run inside the page. Then **one
approved real video** was made: Veo 3.1 Lite, 8 s, x1, which cost 10 credits (1,050 → 1,040).
The scratch scripts live in `/tmp` and are not kept.

- **Access.** The saved Google session opens Flow signed in. The account shows "PRO" (Google AI
  Pro), and the account menu reads "1,050 Google Flow credits". No API key is involved.
  `flow.google.com/?pli=1` lists projects (`/project/<uuid>`); new projects are titled by date.
- **RPC.** The app uses Google `batchexecute` at
  `flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=<id>`. The body is
  `f.req=[[[rpcid, "<json>", null, "generic"]]]&at=<xsrf>`, and the response is framed as
  `)]}'`, then a length, then `[["wrb.fr", rpcid, "<json>"…]]`.
- **Agent.** The agent chat is
  `…/google.internal.labs.aisandbox.proto.flow.agent.v1.FlowCreationAgentService/StreamChat`.
  An approval is sent as a chat message
  `[session, [[[["Approve"]]]], ["projects/<id>", null, [<~2.4 KB token>, 1], null, null, 2], null, 1]`.
  The token is reCAPTCHA (the page footer says "protected by reCAPTCHA"), so generation is
  page-driven.
- **Project page.** It is agent-only.
  - **Editor:** the prompt is a ProseMirror editor (`flow-rich-text-editor.prompt-input
    .ProseMirror`, placeholder "What do you want to create?"). Playwright `fill` does not reach
    it; focus plus keyboard typing does.
  - **Title:** "Editable text" is the **project title** input, not the prompt.
  - **Buttons:** "Start generation", "Add media menu" (Upload, New collection, Create character,
    New scene), "Add ingredients to the prompt box", "Agent instructions", and a `tune` settings
    button.
- **Tune panel (defaults).**
  - **Agent spending:** "Always: agent will ask for confirmation before generating media" /
    "Never: agent will generate media and spend credits automatically".
  - **Image:** aspect 16:9 / 4:3 / 1:1 / 3:4 / 9:16, outputs x1–x4, model "Nano Banana 2.1" or
    "Nano Banana Pro".
  - **Video:** aspect 16:9 / 9:16, outputs x1–x4, model "Omni 1.1 Flash", "Veo 3.1 - Lite",
    "Veo 3.1 - Fast" or "Veo 3.1 - Quality".
  - **Watermark:** a "Visible watermarking" switch, but a visible watermark is required in this
    region.
  - **Keyboard only:** in a minimized window, pointer clicks on the panel and its options time
    out. Focus plus Enter works. Escape does not close the panel; reloading the page does.
- **Generation flow.**
  1. The agent restates its plan ("I'm going to generate an 8-second video … using Veo 3.1 -
     Lite").
  2. It asks permission in a `flow-permission-message`: "Would you like me to kick off this 1
     video generation, costing 10 credits?" The options are `role=radio` in the radiogroup
     "Permission options": **Approve / Always approve / Reject**. Focus plus Enter on "Approve"
     works.
  3. The question persists server-side: it was still waiting when the project was reopened later.
  4. After approval: "scheduled and is waiting in the queue due to high demand". A ▶ tile
     appeared after about 60 s.
- **Media records.**
  - `bOKtO`, `jwpduf` (polled every 5 s) and `as29s` return
    `[media_id, project_id, workflow_id, …, [[created], prompt, …, [null, [[model_key, 1, null,
    null, 2, 1]], [[prompt]], null, 1], null, [status], 1, <poster url>, [], null, <bytes>], …,
    [null, null, [<seconds>]]]`.
  - Model key `veo_3_1_t2v_lite`; status 2 = running, 3 = done; 8 seconds; 1,285,983 bytes.
  - `jwpduf` starts with `[null, <credits left>, …]` (1040 after the run).
- **Downloads.** `as29s` holds signed CDN links
  `https://flow-content.google/{video|image}/<media_id>?Expires=…&KeyName=labs-flow-prod-cdn-key&Signature=…`,
  valid for about 6 h. A plain GET **without cookies** returns `video/mp4` (1280×720, "Veo"
  watermark) or the `image/jpeg` poster.
- **Characters** (`/project/<id>/character`): "Build and reuse characters for consistent videos".
  - Starters: archetypes (Eccentric, Professional, Wildcard, Familiar, Wicked, Fantastical).
  - Inputs: Upload and Add from project.
  - Controls: "Format", "Select model family" (Nano Banana Pro / 2 Lite / 2.1), and its own
    "Start generation".
- **Ingredients panel.** Tabs All / Images / Videos / Voices / Characters / Avatars / Uploads,
  "Upload media", "Add to prompt", a "Select project" picker, and "Trim start/end" sliders for
  video ingredients.
- **Tools** (`/project/<id>/tools`): My Tools / Community / Templates. Out of scope.
- **Prices** (third-party, consistent with the observed 10): Veo 3.1 Lite 10, Fast 20, Quality
  100; Omni Flash 4–15 depending on resolution and length.

### Flow core build, live findings (2026-10-07)

- **Saving tune settings.** Changes in the tune panel are kept only after its **Save** button,
  which sends `Kcr7Ub`. Without Save, a reload brings back the old defaults. `apply_defaults`
  presses Save, waits for `Kcr7Ub`, reloads, then reads the settings back.
- **The permission request is structured, but thin.** The `StreamChat` stream carries
  `ask_for_permission` with only `num_videos`, `num_images`, `total_cost` and `num_total`. The
  model, length and aspect are sent only after Approve, in the job record. The agent does not
  always restate its plan in text (one run said "using Veo 3.1 - Lite", a later one only asked
  the price question). So, before spending, aistudio checks:
  - kind, count and cost from the quote;
  - model, aspect and outputs from the saved and re-read settings;
  - the length after the job, as a warning.

### Flow plan B discovery: lists (2026-10-08, free)

- **Projects:** `UpteDb` is loaded by `flow.google.com/`. Its payload `[0]` is a list of
  `[project_id, [title, null, [created_s, ns], thumbnail_url?, thumbnail_media_id?]]`, with titles
  such as "aistudio" and "Oct 07 - 13:57".
- **Project contents:** `Zzl0ze` is loaded by `/project/<id>`.
  - `[1]` holds workflows: `[workflow_id, null, null, [title, [created], null, null, media_id, …,
    [updated]], project_id]`.
  - `[2]` holds **media records**, in the same shape as `as29s`, including the signed
    `flow-content.google` links.
  - `[3]` holds **voices**, e.g.
    `["achernar", 3, "Achernar", ["achernar", …, [["Achernar", "Female, soft, high pitch", true, <sample url>]]]]`.
  - `ngNC2` repeats the project title and its thumbnail media id.
- **Paid check (CLI, approved):** `flow video -m veo-3.1-lite --duration 8 --max-credits 10`
  finished in 96 s. Credits went 1,040 → 1,030 and the downloaded MP4 was 2,286,873 bytes.
- **Compact prompt box.** Flow can show the prompt box collapsed, with an "Expand" button and an
  "Agent" chip. "Start new session" appears only after Expand. `new_session` expands first.
- **Attachments are media ids.** The `StreamChat` request lists attached media next to the prompt:
  `[[[[prompt]]], [[media_id, …]]], ["projects/<id>", null, [<token>, 1], …]`. Frames and
  ingredients are therefore referenced by Flow media id.
- **Upload.** The working route was pointed out by the user. One test upload was made with their
  OK.
  1. Open the prompt box's **+** ("Add ingredients to the prompt box") and choose **Upload media**.
     Only a dispatched `click` event opens the file chooser; Enter and Space do nothing. Neither
     "Add media → Upload" nor a drop does.
  2. Set the file. A dialog appears: **"Rights to use this image: Make sure you have the necessary
     rights to any content or files that you upload, including content of minors…"** with
     **Cancel** and **I agree**.
  3. After **I agree**, the page refreshes its reCAPTCHA token (`/recaptcha/enterprise/reload`),
     then uploads with batchexecute **`maseQ`** (about 640 KB for a 450 KB JPEG, so base64), then
     calls `as29s` for the new media.
  4. The upload becomes a workflow titled with the file name ("apple.jpg") and a media record in
     `Zzl0ze[2]` (here `91f49a6c…`).

  The CLI may press "I agree" only with an explicit `--i-have-rights` for that run (user
  decision).
- **Expiry.** A banner reads: "Your oldest creations will expire soon. Opt-in to keep them around."

### Flow plan B discovery: extend, scenes, characters (2026-10-08, free)

- **Scenes.** "Add media → New scene" (a dispatched click on the menu item) creates and opens
  `/project/<id>/scene/<scene-id>`. The probe left an empty scene, "Untitled Scene 10-08
  01:38:21", in the "aistudio" project.
  - The editor lists the project's clips by title as buttons ("apple.jpg", "Red apple rolling on
    table").
  - Controls: "Add clip", "Zoom in/out", "Toggle aspect ratio", playback, "Download scene",
    "Move to trash", "Done editing scene", and a prompt box with "Start generation".
- **Extend.** Not found yet. A dispatched click on a video tile opens no detail view, and the
  visible actions show "Reuse prompt" but no "Extend". It may sit behind a real click or hover on
  the tile, or be an agent request with the video attached (attachments are media ids).
- **Characters** (`/project/<id>/character`).
  - The description goes into the same ProseMirror editor. The model picker reads "Nano Banana
    2.1".
  - The page also has "Upload" and "Add from project".
  - A dispatched click on "Start generation" (with every request blocked) sent nothing. The real
    trigger and its cost prompt are still unknown.

### Flow plan B discovery: characters and extend spend directly (2026-10-08, free, blocked)

- **Tile ⋮ menu** (on a hovered media tile): Favorite, Reuse prompt (the ⟲ icon), Add to scene,
  Add to prompt, Download, Copy, Rename, Share, Publish to YouTube, Set project cover, Flag
  output, Move to trash. There is no Extend here.
- **Video editor.** A real mouse click on a video tile opens `/project/<id>/edit/<workflow_id>`.
  - Controls: Save frame, Download, Save to projects, Reuse prompt, the timeline's **Add clip**,
    and a prompt "Describe how to edit this video…" whose model is Omni 1.1 Flash (an edit).
  - **Add clip** opens a menu with "Add clip" and **"Extend (Veo 3.1 - Lite)"**; the model
    follows the video default. Extend shows "Prompt to extend: What happens next?" and the
    timeline previews 8 → 16 s.
  - "Start generation" sends `rqZuUc` = `["projects/<id>", [workflow_id], null, null, 2]`, then
    (blocked here) the generation. **No credit quote is shown.**
- **Characters.** "Start generation" sends `C4BZMd` = `[[project_id, null, null, [1, "Untitled
  character", []]]]`, which creates the character record (`/character/<id>`). It then sends
  **`ogiZ0b`** directly: `[…, 3, "BELUGA", …, [<reCAPTCHA token>, 1]], [[[prompt]]], …`. There is
  no agent and no quote; the page shows "image 99%".
- **Consequence:** plan B's character and extend commands cannot approve against Flow's quote.
  They need a known price and an explicit budget, plus a credit check before and after.
- **Leftovers in the "aistudio" project from these probes:** an empty "Untitled character"
  record and the empty scene.

### Flow plan B: measured costs and result shapes (2026-10-08, approved real runs)

| Action | Model | Credits (before → after) |
|---|---|---|
| Character from a description | Nano Banana 2.1 | **0** (1,030 → 1,030) |
| Extend an 8 s clip by 8 s | Veo 3.1 Lite (`veo_3_1_extension_lite`) | **10** (1,030 → 1,020) |

- **Character.**
  - `C4BZMd` → `[[project_id, character_id, null, [1, "Untitled character", []], null, null,
    [created], [updated]]]`.
  - `ogiZ0b` (BELUGA, the prompt, a reCAPTCHA token) → an image media record with a
    `flow-content.google/image/…` link and size `[1376, 768]`.
- **Extend.**
  - `rqZuUc` `["projects/<id>", [workflow_id], null, null, 2]` creates a **scene** ("Untitled
    Scene <date>") holding the source clip. `uwAyfb [scene_id, project_id]` reads it back.
  - `fZytfe` starts the extension:
    `[[[[null, source_media_id, 169, 192], [null, null, [[[[prompt]]]]], "veo_3_1_extension_lite", …`.
    Its reply is `[null, <credits left: 1020>, [[workflow "Apple rolls off table" …]], [[media
    record …]]]`, so the credits are charged at the start.
  - The result is a new 8 s clip (its own media id), plus the scene joining both clips
    (downloadable with "Download scene").

### Flow plan D discovery: names, photos, character attachments, scenes (2026-10-08, free)

Every probe here spent nothing (credits 1,010 before and after). Requests that would generate were
blocked; the RPCs that went through are free.

- **Project contents, more slots.** `Zzl0ze` (8 entries) also holds:
  - `[4]` **scenes**: `[scene_id, title, null, [created], [updated], 2, null, []]`.
  - `[5]` **characters**: `[project_id, character_id, null, [1, name, [[[ref_workflow_id]]]],
    image_media_id, [w, h], [created], [updated]]`. A character never given an image has
    `[1, name, []]`, `null`, `null`.
  - `[6]` **scene clips**: `[[workflow_id, null, null, [title, [created], null, null, media_id,
    …], project_id], scene_id, [position, [seconds], [], [seconds], [1]]]`, where `position` is
    `null` for the first clip and `1` for the second.
- **Rename a character.** The "Character name" input (or "Edit name") sends `rzMKMb`:
  `[[project_id, character_id, null, [1, name]], [["entity_info.display_name"]]]` (a field
  mask). It returns the character record. Sent from the page with its own session (a no-op
  rename to the same name), it returned 200 and the record with a new update time.
- **Character from a photo.** On the character page, "Add from project" opens a picker (Images /
  Uploads tabs, options by file name, "Add media").
  - On a new character page, "Add media" sends `C4BZMd` (create, as for a description), then
    `Sc7aEb`: `[photo_media_id, null, null, project_id, null, null, null, [null, null,
    [character_id, [0]]], null, <client UUID>, <client UUID>]`.
  - `Sc7aEb` copies the photo into a new workflow (titled with the file name) and media, and
    makes it the character's image. No generation runs, and it is free. Afterwards the page shows
    "Portrait" in place of Upload / Add from project.
  - The probe linked `apple.jpg` to the empty leftover character `88c8e5c5`, which now has an image.
  - Typing a description and "Start generation" then sends `ogiZ0b` (a Nano Banana generation from
    the description), which was blocked.
- **Characters as ingredients.** The prompt box's + panel has a **Characters** tab
  (`mat-list-item`, `role=tab`). Characters are `role=option` buttons named by the character name
  only, and the thumbnails are opaque `lh3.googleusercontent.com` links (no ids). Clicking an option
  attaches it at once and closes the panel (there is no "Add to prompt").
  - `StreamChat` then carries the character **by id in its own slot**: `[session, [[[[prompt]]],
    null, null, [[character_id]]], ["projects/<id>", null, [<token>, 1], null, null, 3]]` (media
    attachments use slot 1).
  - A quote with a character attached (Omni 1.1 Flash default) was 15 credits; it was rejected.
- **Stitching ("Download scene").** The scene editor's "Download scene" sends `GjQLTd`
  `[[[media_id, null, [], [seconds]], …]]` (the clips in order), which returns a job name
  `[null, [["projects/<n>/locations/<region>/jobs/<uuid>"]]]`. The page then polls `IxdSwd`
  `[[[job]]]` every 3 s: `[2, "", "", 0]` while running, then `[3, "", "", 3, <base64 MP4>]`.
  - Two 8 s clips gave a 14,065,014-byte MP4 in about 15 s; the page saves it from a `blob:` URL.
  - `GjQLTd` needs no scene record: from the project page it accepted any clips by media id.

### Flow plan E discovery: hidden copies and attached media (2026-10-08, free)

- **Character images are hidden copies.** A photo character's image is a copy that `Zzl0ze[1]`
  lists under the photo's file name. Example: "apple.jpg" appears three times (`91f49a6c`, the
  upload, plus `1306ca3e` and `938a64f3`, two characters' images). Each title still shows **one**
  tile in the project grid and one option in every + panel tab (All, Images, Uploads). The extra
  ids are exactly the characters' `media_id`s (`Zzl0ze[5][4]`).
- **Attached media in the request.** Attaching "apple-start.jpg" with its tile's "Add to prompt"
  sent `StreamChat` message `[[[[prompt]]], [["ea396dda…"]]]`: slot 1 lists the attached media
  ids (the original upload, not the copy). Characters use slot 3 (plan D).
- **Two attachments (plan E review check, free).** Attaching "apple-start.jpg" then "apple.jpg"
  by their tiles sent slot 1 `[["ea396dda…"], ["91f49a6c…"]]`, one entry per media **in attach
  order**. The quote was 10 credits and was rejected.
- **Tiles below the window.** Once the project held more media, those two tiles sat at
  y = 1,290 and 1,667 in a 1,000 px viewport. A mouse hover at their box never reached them, so
  their ⋮ menus never opened. Scrolling the caption into view first fixes it.
- **The Characters tab acts on its highlighted item.** The first option is highlighted
  (`asset-item-active`). A dispatched click on the third option ("Paper boat") attached nothing,
  and focus plus Enter on it attached the **first** character (`21f9fe8d`, caught by the slot-3
  check). Typing the name into "Search assets" leaves it as the only, highlighted option, and the
  click then attaches it (slot 3 carried `5f54e5a5`; quote 10 credits, rejected).
- **Answered and superseded requests stay in the chat** with `aria-disabled` radios. One run with
  a character showed two 10-credit requests: the first disabled, the second waiting. Approve and
  Reject must act only on enabled radios. Before Approve, aistudio checks that the waiting
  request shows the cost it checked ("costing N credits").

### Flow plan F discovery: free generations, personality, avatars, music, tools (2026-10-08)

- **Free generations skip the quote.** With "Confirm before generating: Always", a Nano Banana 2.1
  image request (0 credits) was generated at once, with no permission request.
  - Two quote-only `flow image` runs made 1 and 2 images; credits stayed at 1,010, and aistudio
    reported `AGENT_REPLY`.
  - The `StreamChat` stream carries a tool call `["generate_image", "generate_image",
    [[["model_usage_key", [null, null, "BELUGA"]], ["aspect_ratio", …], ["prompt", …],
    ["model_display_name", [null, null, "🍌 Nano Banana 2.1"]], …]], <placeholder>]`.
  - It then carries a tool result with the same name and `[["media_id", …], ["display_name", …],
    ["status", [null, null, "success"]], ["workflow_id", …], …]`.
  - The whole stream arrived in about 47 s; the media record (`bOKtO`) came at about 41 s.
- **Personality.** The character page's "Character personality" text box sends `rzMKMb`
  `[[project_id, character_id, null, [1, null, [null, null, "<text>"]]],
  [["entity_info.character_info.personality_notes"]]]` (blocked in the probe).
  - Reply (2026-10-09, free; "Paper boat"'s own text written back): the character record
    `[[project_id, id, null, [1, name, [refs, null, "<notes>"]], null, null, [created],
    [updated]]]`. aistudio compares the notes at `[0][3][2][2]` with what it sent.
  - The project read (`Zzl0ze` `[5]`) keeps the same record, so the notes are at row `[3][2][2]`.
- **Voice.** Choosing a voice in "Select a voice" (30 voices, plus a "Describe the voice
  performance style" box) sent nothing, and neither did "Close" or "Done". No save call was
  found.
- **Character model picker:** Nano Banana Pro, Nano Banana 2 Lite, Nano Banana 2.1. Only 2.1
  has a measured price (0).
- **"Create body"** opens Upload / Add from project for a body reference; generating it is not
  measured.
- **Avatars:** "Create my avatar" asks for a recorded selfie video and a few spoken words
  (experimental, English only). This is not automatable.
- **Flow Music** is a separate site (`flowmusic.app`). **Tools** (`/project/<id>/tools`) is a
  builder: My Tools / Community / Templates / Create New.
- **Omni edit.** A clip's editor names "Omni 1.1 Flash" for edits; its price and whether it
  asks first are not measured.
- **Fresh uploads (user-confirmed rights, a synthetic gradient PNG).** The upload became media
  `d2780832`. Right after "Upload media" the + panel stays over the grid, so the new tile's ⋮ could
  not be reached; a reload fixes it, so aistudio now uploads everything first. Each tile's image
  carries its id (`<img … data-media-id="d2780832…">`). A quote with `--start-frame d2780832…` was
  10 credits, carried that exact id, and was declined.

### Flow paid measurements (2026-10-08, user-approved)

| Action | Model | Credits (before → after) |
|---|---|---|
| Video with a character (`--character "Paper boat"`) | Veo 3.1 Lite, 8 s | **10** (1,010 → 1,000), quoted |
| Edit an 8 s clip ("make the video black and white") | Omni 1.1 Flash (`abra_edit`) | **20** (1,000 → 980), **no quote** |
| Character from a description | Nano Banana Pro | **0** (1,030 → 1,030) |
| Character from a description | Nano Banana 2 Lite | unclear (made; the after-read failed, and credits rose 980 → 1,030 for an unknown reason) |
| "Create body" for a character | Nano Banana 2.1 | **0** (1,030 → 1,030) |
| Edit an 8 s clip through `aistudio flow edit` ("old sepia film") | Omni 1.1 Flash | **20** (1,030 → 1,010) |
| An 8 s video (quoted) | Veo 3.1 Fast | **20** (1,010 → 990) |
| Character from a description (clean re-measure, reads right around it) | Nano Banana 2 Lite | **0** (990 → 990) |

- **Omni edit.** The clip editor's box ("Describe how to edit this video…", model chip "Omni 1.1
  Flash") generates **directly**: no permission request, no agent. Start sent `WuwhI`, `jIps6`,
  `mYWVGd` and `nzlxg` after a reCAPTCHA reload. The new clip (model key `abra_edit`, 8 s) showed
  up in `as29s`/`jwpduf`. A probe that blocked requests at Start broke Playwright's route handler
  (`Route.abort` TypeError) and left the request hanging; nothing was spent.
- **Extend follows the source clip.** With the video default set to Veo 3.1 Fast, a Lite clip's
  Extend menu and panel still said "Extend (Veo 3.1 - Lite)". The panel has no model picker. No
  project holds a Fast or Quality clip, so measuring extend on those needs one generated first.
- **Credits can rise on their own.** Between the edit (980) and the next read, credits went to
  1,030 (+50). The account menu shows only the total. A before/after reading is trusted only
  when both reads sit right around one action.
- **Character model picker.** "Select model family" → `menuitem` "🍌 Nano Banana Pro" (or 2 Lite or
  2.1) switches the picker for this character. Start then sends `C4BZMd` + `ogiZ0b` as for 2.1.
- **Create body.** On a character page, "Create body" switches the prompt box to body mode. The
  first time it shows Upload, Add from project, an Ingredient chip and Clear prompt. For a
  character that already has a body, only the "Ingredient" button appears, so that is the
  body-mode marker. Typing a prompt and pressing Start sent
  `WuwhI`, a reCAPTCHA reload and `ogiZ0b`. The reply is the body image (a new media), at 0
  credits on Nano Banana 2.1.
- **Extend is always Veo 3.1 Lite.** On a Lite clip with the default set to Fast, and on a Fast
  clip (`5f09b8eb`) with the default at Omni, the Extend menu offered "Extend (Veo 3.1 - Lite)"
  both times. Flow offers no Fast or Quality extension, so neither was measured.
- **Two uploads in one run.** Right after the second upload, a reload listed the project without
  it, so its tile caption never appeared and the attach timed out (nothing was sent). aistudio
  now reloads until every uploaded file's tile is shown. Re-run: both uploaded, attached in
  order, 10-credit quote, declined.

### Flow settings restore after a timeout (2026-10-09, free probes)

- A paid Veo 3.1 Fast run queued for more than 10 minutes. aistudio's 600 s wait ran out, and
  restoring the video settings (Fast 9:16 back to Omni 16:9) then failed. The clip finished later.
- These did **not** reproduce the failure:
  - saving settings while a free image generated;
  - the Fast 9:16 → Omni 16:9 round trip on its own;
  - saving after the project page sat open for 11 minutes.
- The remaining difference is a paid video still queued, which was not re-run, to avoid spending.
- **Change:** a failed restore is retried once on a freshly loaded project, and the warning now
  carries the reason. `flow video` also waits 1,200 s by default (images still 600 s).
- **Paid re-test (2026-10-09, user-approved, 10 credits).**
  - Run: Veo 3.1 Lite, 9:16, `--max-credits 10 --timeout 30`. aistudio stopped waiting while the
    clip was still generating (`status: timeout`).
  - The restore (Lite 9:16 → Omni 1.1 Flash 16:9) worked on the first try, in 8.6 s, with no
    warning. The clip finished and downloaded.
  - So a paid video still generating does not by itself break the restore. What remains is the
    original case: about 10 minutes of waiting with a Fast clip queued.
  - That cannot be forced (a Lite clip finishes in a minute or two), so the retry and the reason
    stay as the mitigation.
  - Free quotes vary: one of two identical quotes planned "2 generations, mixed" (likely a start
    image plus the video) for 10 credits. aistudio flagged the kind and count mismatch, which is
    why a paid run would have rejected that plan.
  - Flow's balance rose from 970 to 1,010 over this run, so balances cannot confirm a charge.

## AI Studio transcription and image editing (2026-10-09, free: GenerateContent blocked)

- **Transcribe page.** `/prompts/new_chat?model=gemini-3.5-transcribe` stays a chat URL showing
  "Turn speech to transcriptions".
  - Controls: Drive, Upload, Record Audio, an `input[type=file]` that accepts `audio/*` (.aac, .aif,
    .flac, .m4a, .mp3, .ogg, .opus, .wav and more), and run settings switches "Word timestamps",
    "Speaker labels" and "Smart transcription", plus a "Custom vocabulary" input.
  - After a file is set, a "Remove media" chip and a **"Transcribe"** button appear.
  - "Transcribe" sent `GenerateContent ["models/gemini-3.5-transcribe", [[[[null, null, null, null,
    null, [<drive file id>]]], "user"]], null, [null, null, null, 32768, 1, 0.95, 64, …], <token>, …]`.
    There was **no API-key gate**, although the page shows "No API key selected".
  - Price (catalog): $2 per million audio tokens in, $12 per million text tokens out.
- **Image editing.** On an image model's chat page, "Insert images or files" offers Drive, Upload
  files, Camera and Sample Media. The page's `input[type=file]` accepts images, several at once;
  each attached file shows a "Remove media" chip.
  - Run sent `GenerateContent` with `[[[null ×5, [<drive file id>]], [null, "<prompt>"]], "user"]`.
    There was no gate.
- **Uploads go to Google Drive.** Setting a file calls `GetAppFolder` and Drive's `files` upload
  (and `CheckImage` for images), so the request names a Drive file id. AI Studio keeps the
  uploads in the user's Drive.
- **Live runs (2026-10-09, small quota).**
  - Transcription of a 5 s Vietnamese clip (macOS `say -v Linh`) returned its text in the part's
    **slot 22**, `[None × 22, [text]]`, not in slot 1.
  - "Custom vocabulary" is refused together with word timestamps ("custom_vocabulary is
    incompatible with word timestamps") and with speaker labels ("… with diarization"). The page
    turns both on by default.
  - With the vocabulary "Yuri Handmade", the brand was transcribed correctly.
  - An edit of a red/white checkerboard ("make the red squares blue") came back blue and white,
    and otherwise unchanged.
  - A run with the default switches (all on) sent the 270 KB clip **inline**: part
    `[null, null, ["<mime>", "<base64>"]]` (slot 2), not a Drive file id in slot 5. The run
    settings carried `[null, null, null, null, 1, 1]` in generation-config slot 31. aistudio's
    request check counts both forms as a sent file.
  - A 174 s, 7.7 MB AIFF (24 numbered paragraphs) was also sent **inline** (10.2 M base64
    characters). The reply was one slot-22 part with the whole transcript (504 words, end time
    174.1 s), plus an empty final chunk with the finish reason. Nothing was missing or repeated.
  - Every live transcript came in slot 22, also with the switches off. Text only in slot 1 would
    be a chat-style reply (e.g. a refusal); aistudio warns about it.
- **Audio types (2026-10-09, small quota).**
  - The transcribe page's input accepts
    `audio/*,.aac,.aif,.aiff,.alaw,.flac,.m4a,.mp3,.mulaw,.oga,.ogg,.opus,.wav,.mpeg,.mpg,audio/mpeg`.
  - Playwright labels files by name, so `.mp4` and `.webm` arrive as `video/*`. The page showed no
    chip for them ("did not take 1 file").
  - Labelled `audio/mp4` and `audio/webm`, both transcribed. The request carried
    `[null, null, ["audio/mp4" | "audio/webm", <base64>]]`.
- **Drive copies (2026-10-09, free probes, no run).**
  - Attaching a file still uploads it to Drive (`GetAppFolder`, then `POST
    content.googleapis.com/upload/drive/v3/files?uploadType=…&key=…`), even when the request then
    sends the audio inline. The reply is `{kind: "drive#file", id, name, mimeType}`.
  - The upload carries `Authorization: Bearer …` (an OAuth token the page fetches itself).
    `gapi.client.drive` is loaded, but `gapi.client.getToken()` is empty, and a direct
    `gapi.client.drive.files.get` returned 401.
  - So aistudio cannot trash its uploads without handling an OAuth token or the signed session
    against Drive. The session's permission check refused that, so the copies stay (README).
  - Runs did not appear in `aistudio prompt list`: these copies belong to no saved prompt.

## Session cookies go stale after browser runs (2026-10-09)

- HTTP commands read `storage_state.json`, which only `aistudio login` wrote. Chrome rotates the
  Google session cookies while it runs, so after some browser runs the saved copy stopped
  working: a GenerateContent call failed with "Request had invalid authentication credentials"
  (`AUTH_ERROR`), although the Chrome profile was still signed in.
- Fix: `open_browser` copies the browser's cookies (`context.cookies()`, at most 10 s) into
  `storage_state.json` when it closes. `storage_state()` was not used, because it opens a helper
  page for every visited origin.
  - It keeps the saved account (`email`, `authuser`) and origins.
  - It never replaces a saved session with one missing `SID`/`SAPISID` (signed out), and never
    creates or repairs the file.
  - It reads the file after the cookies, just before the write, so a logout from another session
    meanwhile is not undone.
  - A failed save only logs its error type; no cookie values are logged.
- Verified live: after a free browser run, `aistudio auth check --test` went from `AUTH_ERROR` to
  `ok` without a new login.
- If the user signs into another account in aistudio's Chrome, HTTP commands now follow it (the
  saved `authuser` stays).

## Voice panel reads (2026-10-09, free probes)

- `voices list` returned 63, 53 and 52 voices on three runs, and once failed with "the page did
  not show what aistudio expected in time".
- The panel opens on a library view with 31 voice buttons and 10 "View all N" groups (N = 7, 5,
  8, 6, 6, 11, 7, 7, 6, 7).
- After "View all N", the library's 31 buttons stay for about 0.25 s, then the group's N show.
  The reader took the first non-empty read, which was often the stale library.
- `labels()` listed the buttons, then read each one. When the view swapped in between, reading a
  vanished button waited out Playwright's timeout, which caused the error.
- Fix: `labels()` reads all visible labels in one `evaluate_all` (Playwright's visibility rule:
  a non-empty box and no `visibility: hidden`). The library is read until two reads agree, and
  each group until exactly N voices show.
- Two runs after the fix both returned the same 70 voices; the earlier 63 had missed 7.

## Extending an extension (2026-10-09; free probes, one approved 10-credit run)

- `aistudio flow extend <id>` fails for a clip that is itself an extension: "no visible
  '[contenteditable=true]'", before Start, so nothing is spent.
  - The clip is not in the project list's workflows. Its own record names workflow `8263e7a5…`.
  - `/edit/<that workflow>` opens an empty editor (black stage, no clip, no prompt box).
- Extensions live in their **scene**. `/project/<pid>/scene/<scene-id>` has the prompt box and
  "Add clip" → "Extend (Veo 3.1 - Lite)".
  - The scene opens with the **first** clip selected: "Add clip" sits after it, and an Extend
    there would continue from clip 1.
  - Clicking a thumbnail of the last clip in the timeline (images below the player; clips are not
    buttons) selects it. The side panel then shows that clip's prompt, and "Add clip" moves to
    the end of the timeline.
- With the last clip selected, the normal `FlowPage.extend` steps ran (10 credits). The scene
  now joins three clips, and `flow scene download` gives one 23 s file (the clips are 8 s, 8 s
  and 7 s).
- Done in the CLI (plan L, 2026-10-10).
  - A clip with no listed workflow that is the last clip of a scene is extended through the
    scene page. aistudio clicks `.clip.is-last .clip-body`, requires `.clip.is-last.selected`
    and the clip's prompt in the panel, then reads "Extend (…)".
  - Any other clip without an editor gets "extend the last clip of its scene instead".
  - Live (free, no `--max-credits`): the newest clip went through the scene path and stopped at
    OVER_BUDGET "costs 10 credits". The middle clip got the new message.
  - Review hardening (2026-10-10, free).
    - The scene page counts its clips (`.timeline-contents .clip`, visible); a 3-clip scene showed
      3. aistudio requires the count to match the project read, so a clip shown but not yet
      listed can never be the one extended.
    - "What happens next?" is not on the scene page until Extend is chosen, so `extend()`'s panel
      check still tells the Extend box apart there.

## The minimized window's startup flash (2026-10-29, throwaway profiles, macOS)

- **A new Chrome shows its window while it starts.** It was about 1 s at full size before
  `Browser.setWindowBounds` could minimize it, then the Dock animation, once per command.
- **No launch hides it.** Off-screen `--window-position` is clamped onto a display (it landed on
  the external monitor above). `open -g -j` still lets Chrome make itself the front app. Hiding
  the app with `NSRunningApplication.hide` returned false.
- **A small window works.** `--window-size=500,375 --window-position=0,10000` with no launch
  viewport starts Chrome at its minimum size in the main screen's bottom-left corner, even when
  the profile last closed maximized. `page.set_viewport_size(1600x1000)` after the minimize
  keeps the window minimized and small, and the page lays out at 1600×1000.
- **Focus is a race either way.** Chrome kept the front after the minimize in 1 of 5 runs with
  the old launch and 2 of 5 with the small window.
- muse-mcp avoids windows by running headless; AI Studio generation refuses headless (TTS above).

## Saving a frame (2026-10-29; free probes, three saved frames)

- **"Save frame" is a button on both players**: a clip's editor (`/edit/<workflow>`) and a scene
  (`/scene/<id>`). An extension's own editor is empty, so its frames are saved from its scene.
- **It captures in the page and uploads.** The player draws on `canvas.video-canvas` (no
  `<video>` element). Save frame sends a reCAPTCHA reload, then **`maseQ`**, the upload call:
  `[[null, 22, null, null, null, project_id, null, "", null, null, [<token>, 1]], <base64 PNG>,
  "image/png", 1, null ×4, "Saved frame from <clip title>", null, <UUID>, <UUID>]`. There is no
  rights dialog. A probe that blocked `maseQ` created nothing.
- **Reply**: `[[media_id, project_id, workflow_id, …, [null, [… "image/jpeg"], [w, h]]],
  [workflow_id, null, null, ["Saved frame from <clip title>", [created], null, null, media_id],
  project_id]]`. Flow keeps it as a JPEG at the clip's full size: 1080×1920 for a 9:16 clip,
  1920×1080 for 16:9. Credits stayed at 1,020 for all three saves.
- **Frame-exact.** Each saved image matched exactly one frame of the downloaded MP4 (mean
  difference about 0.35 of 255; the neighbouring frames 2.4–6): 3.000 s (frame 72), the last
  frame (191 of 192), and an extension's 2.5 s (frame 60) saved through its scene.
- **Seeking.** A click on the timeline's ruler row (`.time-unit`, 8 px below its top) moves the
  playhead there. In a 1,600 px window the ruler shows 102 px per second and starts at x = 21,
  so about 4.25 px per frame. Arrow keys and End do nothing. The player shows
  `.timecode-value` and `.duration-timecode-value` as `MM:SS:FF` at 24 fps (3.5 s reads
  `00:03:12`). A click can land one frame early (3.25 s read `00:03:05`), so aistudio aims at
  the middle of the frame, reads the timecode, and corrects once.
- **Long scenes.** The ruler shows about 15 s; a click past the timeline's right edge missed (the
  timecode stayed). aistudio presses "Zoom out" until the frame is on screen.
- **A clip's record can say 8 s for a 7 s clip.** An extension listed with 8 seconds had 168
  frames. aistudio takes clip lengths from the timeline (clip boxes and the total timecode).
- **Flaky loads.** During the live check, Flow's pages twice timed out on load (`Page.goto`
  30 s) and `scene list` once returned no scenes, while plain HTTP to flow.google.com answered
  in 0.6 s. A re-run a few minutes later worked.
- Done in the CLI: `aistudio flow frame save <media-id> [--at S] [-o file]`.
