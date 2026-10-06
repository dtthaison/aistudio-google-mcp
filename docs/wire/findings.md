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
| H4 | BotGuard token not bound to content | **inconclusive** | `scripts/spikes/route_rewrite.py` timed out after 15 min and intercepted no request carrying the marker. Needs a re-run with the user. |
| H5 | Video returns a pollable operation | not observed | No video was generated. |
| H6 | Inline attachments accepted | partial | `CreatePrompt` carries images inline as `prompt.chunked_prompt.chunks[].inline_image.data`, which is base64 bytes. Upload paths for image editing and transcription were not observed. |
| H7 | CountTokens and ListModels work over HTTP | **true** | Both returned `http-ok`: `CountTokens` 4/4 and `ListModels` 2/2. |
| H8 | Catalog metadata comes from one RPC | **likely true** | One `ListModels` response (99 KB) lists 86 models across all picker categories: Gemini, Gemma, image (`nano-banana*`, `*-image`), Veo, TTS, transcribe, Live, Lyria (`lyria-3.5`, `lyria-3-*`), and agents (`antigravity-preview-*`, `deep-research-*`). It also contains price strings (`$`), paid markers and knowledge cutoffs. M2 must still map the field indices. |
| H9 | Lyria song generation is request/job | not observed | `lyria-3.5` is present in the catalog, but no music was generated. |
| H10 | Stars are server-side | not observed | `GetUserPreferences` and `UpdateUserPreferences` use a positional preferences message with a field mask (for example `["has_seen_ssgc_onboarding"]`). Star state was not seen. |

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
