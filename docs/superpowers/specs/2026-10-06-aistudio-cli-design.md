# aistudio-py — CLI + MCP server for Google AI Studio (web app automation)

- **Date:** 2026-10-06
- **Status:** Draft, awaiting review (rev 2: model catalog, Phases 2 and 3)
- **Reference implementation studied:** `notebooklm-py` v0.8.2 (installed at `~/.venvs/notebooklm`)

## 1. Intent

**What was asked:** Use the `notebooklm` CLI as the model for a CLI that works against
https://aistudio.google.com.

**Decisions made during brainstorming:**

| Question | Decision |
|---|---|
| Purpose | Automate the AI Studio **web app** with a Google session login, the way notebooklm-py automates NotebookLM. This is not a wrapper around the official Gemini API. |
| Model coverage | **Every category in the AI Studio model picker:** Featured, Gemini, Gemma, Images, Video, Audio, Music, Live, Agents. The live catalog drives the commands (§6.2). |
| Phase 1 | Foundation (auth, transports, discovery), model catalog, prompt library, chat (Gemini, Gemma), images (generate and edit), video, speech, transcribe, music (Lyria song generation), MCP |
| Phase 2 | Live (real-time voice and video, real-time Lyria) and Agents (Antigravity plus the template agents) |
| Phase 3 | Build: Apps and Gallery |
| Out of scope | Dashboard (API keys, usage, projects), Omni video editing, master-token login |
| Stack | Python 3.10+, CLI **and** MCP server |
| Transport | **Hybrid:** plain HTTP for RPCs without BotGuard; a CDP-attached browser for BotGuard-protected RPCs |

**Assumptions**, to be corrected if wrong:

- The user accepts that this uses undocumented endpoints. It may break on any Google deploy and may
  violate Google's Terms of Service. The README states this.
- Single user, local machine. The daemon and the MCP server bind to loopback only.
- The user will take part in one discovery capture session (milestone M1), plus a short one at the start of
  Phase 2 and of Phase 3. Without them, the wire formats cannot be written.
- Paid models (badge "Paid" in the picker, e.g. Lyria 3.5 at $0.08/request) are billed to the user's
  account. The CLI warns but does not block (§6.2).

**Success criteria for Phase 1:**

1. `aistudio login`, then `aistudio auth check --test --json`, reports `status: ok`.
2. Full prompt-library CRUD from the CLI, with `--json` output.
3. `aistudio ask` returns a model answer, with thinking and tools, for an ephemeral turn and for a turn appended
   to a saved chat.
4. `models list --category <c> --json` matches the picker for every category, including paid badges and pricing.
5. `generate image` (including `--image` edits), `generate speech` and `transcribe` produce output. `generate video` and
   `generate music` work through `job wait` and `download`.
6. Every capability above is reachable as an MCP tool from Claude Code.
7. The default test suite passes offline, using fixtures only.

Phase 2 and Phase 3 success criteria are set in their spec addenda (§11).

## 2. Background

### 2.1 What we reuse from notebooklm-py (design, not code)

- Profiles live at `$HOME_DIR/profiles/<name>/{storage_state.json, context.json, <browser dir>}`.
  Profile precedence is `--profile` > env var > `config.json` default > `"default"`.
- Login uses a Playwright persistent context with `--disable-blink-features=AutomationControlled`
  and with `--enable-automation` removed. It waits for the landing page, filters `storage_state` to an
  allowlist of Google domains, and writes it atomically under a filelock.
- Cookie upkeep: a `RotateCookies` keepalive poke, at most once every 60 s. Recovery after an auth error is
  single-flight.
- Layering: `cli/` and `mcp/` both sit on top of `_app/`, a shared use-case layer with no click or fastmcp
  imports. `_app/` sits on the client, the client on wire builders and decoders, and those on transport
  and auth.
- Wire code is split into pure positional **params builders** and **rows decoders**. Rows decoders
  carry documented index tables and use `safe_index`, so schema drift raises a clear error.
- CLI conventions:
  - Framework: click.
  - `--json` on every command. Lists come back as `{"<items>": [...], "count": N}`.
  - Errors come back as `{"error": true, "code", "message"}`.
  - Exit codes: 1 for handled errors, 2 for unexpected errors, 130 for interrupt.
  - IDs can be abbreviated by prefix.
  - `use` sets a context; destructive commands take `-y`.
- MCP conventions:
  - Built on `fastmcp` with a stdio entry point.
  - Destructive tools require `confirm=true` and otherwise return a `needs_confirmation` preview.
  - Errors are structured as `{code, message, retriable, hint}`.
  - Tools carry read-only or destructive annotations.
  - `mcp install <client>` writes the client config.

### 2.2 How AI Studio differs from NotebookLM

| | NotebookLM | AI Studio |
|---|---|---|
| Endpoint | `/_/LabsTailwindUi/data/batchexecute` | `alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/<Method>` |
| Body | `f.req=` form-encoded, obfuscated rpc ids | `application/json+protobuf` positional arrays, readable method names |
| Per-request auth | `SNlM0e` CSRF scraped from HTML | `Authorization: SAPISIDHASH …` + `X-Goog-Api-Key` + cookies |
| Anti-abuse | none on RPCs | **BotGuard** token required on `GenerateContent`, so plain cookie replay fails |
| Storage | server-side notebooks | saved prompts are files in the user's Google Drive ("Google AI Studio" folder) |

Methods known from public reports: `ListModels`, `GenerateContent`, `CountTokens`,
`GenerateAccessToken`, `UpdatePrompt`, `PushNewCommit`, `CreateTunedModel`, `ProxyUnaryCall`.
Known open-source workarounds for BotGuard drive a real Chrome over CDP and either call the page's
BotGuard snapshot function, found by code pattern, or inject the request through an XHR hook.

## 3. Architecture

```
cli/  (click)                 mcp/  (fastmcp, stdio)
        \                        /
         └──► _app/  use cases, JSON serialisation, error classification
                        │
              client.py  AIStudioClient (async context manager)
                         namespaces: .models .prompts .chat .media .jobs
                                     .agents .live (Phase 2)  .apps .gallery (Phase 3)
                        │
              _rpc/  registry.py   RPC spec = {method, params builder, rows decoder, transport}
                     params/*.py   pure positional request builders
                     rows/*.py     positional response decoders (index tables, safe_index)
                     wire.py       json+protobuf encode/decode, gRPC-status error arrays
                        │
              _transport/router.py  picks a backend: spec.transport, or --backend override
                 ├─ http.py      httpx.AsyncClient + middleware (retry, auth-refresh, tracing)
                 ├─ browser.py   Playwright connect_over_cdp / ephemeral launch
                 │    └─ botguard.py  BotGuardStrategy (snapshot-call | route-rewrite)
                 └─ stream.py    Phase 2: bidirectional real-time sessions (Live), protocol decided by capture
                        │
              _auth/  login, storage, signer (SAPISIDHASH), refresh, keepalive, cookie import
              _types/ dataclasses: Model, ModelPricing, Prompt, PromptSummary, Turn, Part, GenerateResult,
                      Usage, Citation, MediaFile, Job; AgentRun, LiveSession (P2); App, GalleryItem (P3)
```

**Rule:** only `_rpc/params`, `_rpc/rows` and `_rpc/wire.py` know array indices. When Google moves a
field, one builder or decoder and its fixture change. The client, the app layer, the CLI and MCP do
not.

### 3.1 Package layout

```
pyproject.toml            # uv-managed; entry points: aistudio, aistudio-mcp
README.md                 # includes ToS / account-risk notice
src/aistudio/
  __init__.py  client.py  exceptions.py  paths.py
  _types/      models.py prompts.py chat.py media.py jobs.py agents.py live.py apps.py
  _auth/       login.py storage.py signer.py refresh.py keepalive.py cookies.py
  _transport/  router.py http.py middleware.py browser.py botguard.py daemon.py stream.py
  _rpc/        registry.py wire.py
               params/{models,prompts,generate,media,agents,live,apps}.py
               rows/{models,prompts,generate,media,agents,live,apps}.py
  _app/        models.py prompts.py chat.py media.py jobs.py agents.py live.py apps.py
               serialize.py errors.py
  cli/         main.py session_cmd.py models_cmd.py prompt_cmd.py chat_cmd.py
               generate_cmd.py transcribe_cmd.py job_cmd.py browser_cmd.py dev_cmd.py mcp_cmd.py
               agent_cmd.py live_cmd.py app_cmd.py gallery_cmd.py
               error_handler.py resolve.py context.py
  mcp/         __main__.py server.py _errors.py tools/{models,prompts,chat,media,agents,apps}.py
tests/
  fixtures/<Method>/<n>.json   # redacted captures
  unit/  cli/  mcp/  live/
```

**Dependencies:**

- Core: `click`, `httpx`, `rich`, `filelock`.
- Extras:
  - `[browser]`: `playwright`
  - `[cookies]`: `rookie-cookies`
  - `[mcp]`: `fastmcp`
  - `[live]` (Phase 2): `sounddevice` for microphone and speaker I/O
  - `[all]`, `[dev]`: `pytest`, `pytest-asyncio`, `respx`, `ruff`

## 4. Auth and session

Statements marked **[verify]** are hypotheses. The M1 capture session confirms or corrects them.

### 4.1 Paths

- Home is `$AISTUDIO_HOME`, default `~/.aistudio`. The profile is selected by `$AISTUDIO_PROFILE` or `--profile`.
- Each profile directory `profiles/<p>/` holds:
  - `storage_state.json`: cookies, mode `0600`. The account email and `authuser` are stored under an `"aistudio"` key.
  - `session.json`: the cached web API key, with the time it was scraped.
  - `context.json`: the current prompt ID and default model.
  - `jobs.json`: the local registry of jobs and agent runs (§6.4, §6.5).
  - `models.json`: the cached model catalog (§6.2).
  - `browser.json`: daemon `{pid, cdp_url}`, mode `0600`, present only while the daemon runs.
  - `chrome/`: the persistent Chrome user-data directory.

### 4.2 Login

`aistudio login [--browser chrome|chromium|msedge] [--fresh] [--timeout 300]`:

- Launches a persistent Playwright context on `profiles/<p>/chrome/`. The default is **system Chrome**
  (`channel="chrome"`): a real, warm Chrome profile is the best input for BotGuard, and bundled
  Chromium is known to crash on recent macOS.
- Steps:
  1. Navigate to `https://aistudio.google.com/` and wait for the human to sign in.
  2. Login counts as complete when the URL is on `aistudio.google.com` and the `SAPISID` cookie is present.
  3. Scrape the web API key from the page or its JS bundle **[verify]** into `session.json`.
  4. Snapshot `storage_state`, filter it to the Google-domain allowlist, and write it atomically under a filelock.
- Refuses to run while the daemon owns the profile, because Chrome locks the user-data directory.
- `--browser-cookies BROWSER[::PROFILE]` (`[cookies]` extra) reads cookies from an installed browser
  instead of launching Playwright. When the browser backend first starts, they are injected into the
  persistent profile with `context.add_cookies`.

Other auth commands:

- `auth check [--test] [--browser] --json` returns `{status, checks: {cookies_present, api_key_present, http_probe, browser_probe?}}`.
  - `http_probe` sends `ListModels` over HTTP.
  - `browser_probe` checks that the page loads signed in and that a BotGuard token can be minted.
- `auth refresh` forces the recovery chain in §4.4.
- `auth logout -y` deletes the profile's credentials.

### 4.3 Request signing (HTTP backend)

`_auth/signer.py` builds these headers for each request:

- `Authorization: SAPISIDHASH <ts>_<sha1("<ts> <SAPISID> https://aistudio.google.com")>`, followed by
  `SAPISID1PHASH …` and `SAPISID3PHASH …` computed from `__Secure-1PAPISID` / `__Secure-3PAPISID` when those cookies exist.
- `X-Goog-Api-Key: <web key>` **[verify]**
- `X-Goog-AuthUser: <authuser>`
- `Origin` and `Referer`: `https://aistudio.google.com`
- `Content-Type: application/json+protobuf`
- `X-User-Agent: grpc-web-javascript/0.1` **[verify]**

### 4.4 Refresh

Keepalive: an `accounts.google.com/RotateCookies` poke, at most once per 60 s. It is guarded by the file's mtime and
a cross-process lock, and the rotated cookies are merged back into the file.

On an auth error (HTTP 401/403, or gRPC status 16 or 7 without a quota message), a single-flight recovery runs once:

1. Reload cookies from disk. Another process may already have refreshed them.
2. Poke `RotateCookies`.
3. If the daemon is running, read fresh cookies from it over CDP. Otherwise, if `chrome/` exists, load AI Studio
   headlessly in the persistent profile and re-snapshot the cookies.
4. Otherwise raise `AuthError` with the hint `run "aistudio login"`.

Every browser-backend session ends by merging the browser's cookies back into `storage_state.json`.

## 5. Transport

### 5.1 RPC registry

Each RPC is declared once:

```python
RpcSpec(method="ListPrompts", params=params.prompts.list_prompts,
        rows=rows.prompts.prompt_summaries, transport=Transport.HTTP, idempotent=True)
```

The `transport` values start out as hypotheses and are fixed empirically by `dev replay` (§8.2).
`--backend http|browser` overrides the declared transport for debugging.

### 5.2 HTTP backend

- Sends `POST {base}/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/<Method>`
  through `httpx.AsyncClient`.
- Middleware chain, in order: retry, auth-refresh, tracing (redacted).
- `wire.py`:
  - JSON-encodes the positional array.
  - Decodes the response array.
  - Recognises the gRPC error envelope `[,[code,"msg"]]` **[verify exact shape]** and streamed
    array-of-chunks responses.

### 5.3 Browser backend

- `aistudio browser start [--headed] | status | stop`:
  - Launches system Chrome detached, with `--user-data-dir=profiles/<p>/chrome` and
    `--remote-debugging-port=<free port>` bound to `127.0.0.1`.
  - Writes `browser.json`.
  - `stop` terminates the process and removes `browser.json`.
- Choosing a browser:
  - If `browser.json` exists and its CDP endpoint answers, attach with `connect_over_cdp`.
  - Otherwise launch an ephemeral persistent context (headless by default, or `--headed`) for one command.
- Inside the browser, one dedicated tab stays on `https://aistudio.google.com/prompts/new_chat` and is
  reused. The backend waits until the app's RPC client has initialised.
- MCP server: attaches or launches lazily on the first browser-transport call and keeps the browser
  until the server shuts down.
- If the page crashes or detaches, the backend reattaches once and then raises `BrowserError`.

### 5.4 BotGuard

> **Revised after M1 follow-up (docs/wire/findings.md):**
>
> - **The token is content-bound.** A swapped body with a fresh token gets 403; the original
>   bytes get 200. So `RouteRewriteStrategy` is disproven.
> - **Headless Chrome is refused.**
> - **Generation therefore uses page driving (`PageDriver`).** A visible Chrome window, at a
>   1600×1000 viewport, fills the page's own controls and triggers Run. The response is captured
>   from the network, and the page mints the token for exactly that request.
> - **Reading pages works headless.** For example, listing voices.
> - **`SnapshotCallStrategy` remains unexplored.**
>
> The original text below is kept for history.


The interface is `BotGuardStrategy.send(page, spec, body) -> raw response`. There are two
implementations, and M1 decides which is the default:

1. **SnapshotCallStrategy.** Locate the page's BotGuard snapshot function by code pattern; its name
   changes per bundle, but the pattern stays stable. Call it with `page.evaluate` to mint a token, insert
   the token into the body at the index given by the spec, then send it with an in-page `fetch`
   so the browser's cookies and TLS are used.
2. **RouteRewriteStrategy.** Arm `page.route` for the method URL, trigger the app's own request with a
   minimal UI action, replace the request body with ours while keeping the minted token, and return
   the response. This works only if the token is not bound to the request content **[verify]**.

If both fail, the error is `BOTGUARD_REJECTED` with the hint `try "aistudio browser start --headed"`.

## 6. Command surface

### 6.1 Global and session commands

Global options: `-p/--profile`, `--backend auto|http|browser` (default `auto` = the transport each RPC
declares), `-v/-vv`, `--quiet`. Every command
accepts `--json`. Prompt IDs can be shortened to a unique prefix.

| Command | Notes |
|---|---|
| `login`, `auth check/refresh/logout` | §4 |
| `browser start/status/stop` | §5.3 |
| `profile list/create/switch/delete` | `delete` requires `-y` |
| `status [--active]` | AI Studio and Gemini API incidents (revised: the prompt-context `use`/`status` pair was dropped together with `ask`, which needs an API key the user declined) |
| `doctor [--wire]` | Checks setup. `--wire` runs read-only RPCs to detect schema drift. |

### 6.2 Model catalog

The live catalog in AI Studio's model picker drives the CLI.

| Command | Transport |
|---|---|
| `models list [--category C] [--paid\|--free] [--starred] [--search TEXT]` | HTTP **[verify]** |
| `models get <id>` | HTTP **[verify]** |
| `models star <id>`, `models unstar <id>` | HTTP **[verify]** |

`--category` values are the picker's filter chips: `featured`, `gemini`, `gemma`, `images`, `video`, `audio`,
`music`, `live`, `agents`. Without `--category`, every model is listed, matching the picker's "All" chip.

Each model is returned as (shape as implemented in M2: `display_name` rather than `name`,
because `name` reads as the `models/<id>` resource name; the unit lives in the pricing label):

```json
{"id": "lyria-3.5", "display_name": "Lyria 3.5", "categories": ["audio", "music"],
 "badges": ["paid"], "description": "Our advanced, full-song generative model …",
 "pricing": [{"direction": "output", "label": "Song generation (*Output per song)",
              "price_usd": 0.08, "up_to_tokens": null}],
 "knowledge_cutoff": "2025-01", "release_date": "2026-09-04", "starred": false,
 "command": "generate music"}
```

Each category maps to one command:

| Category | Command | Transport | Phase |
|---|---|---|---|
| `gemini`, `gemma` | `ask` (§6.3) | browser | 1 |
| `images` | `generate image`, which also edits with `--image` (§6.4) | browser | 1 |
| `video` | `generate video` (§6.4) | browser, job | 1 |
| `audio` | `generate speech`, `transcribe` (§6.4) | browser | 1 |
| `music` | `generate music` (§6.4) | browser, job **[verify]** | 1 |
| `live` | `live`, including real-time Lyria (§6.6) | stream | 2 |
| `agents` | `agent run` (§6.5) | browser, long job | 2 |

`featured` is a filter only and has no command of its own. The home page's "Explore Google models" cards
correspond to the categories as follows:

| Card | Categories |
|---|---|
| Code and Chat | `gemini`, `gemma` |
| Image Generation | `images` |
| Video Generation | `video` |
| Speech and Music | `audio`, `music` |
| Real-time | `live` |

Catalog rules:

- **Category validation.** Every model-taking command checks the model's category first. For example,
  `ask -m lyria-3.5` fails with `VALIDATION_ERROR` and the hint
  `lyria-3.5 is a Music model; use "aistudio generate music"`.
- **New models need no code change.** They appear through the catalog. A model whose category has no
  command yet is listed with `"command": null, "unsupported_category": true`.
- **Paid models.** Before the request, the CLI prints a one-line notice on stderr, such as
  `lyria-3.5 is paid: $0.08/request`. JSON results and MCP results carry `cost_notice`. Nothing is
  blocked.
- **Caching.** The catalog is cached for 1 hour in `profiles/<p>/models.json`. `models list --refresh`
  bypasses the cache.

### 6.3 Chat and prompt library

| Command | Transport |
|---|---|
| `count-tokens "…" [-m MODEL] [-f FILE]` | HTTP **[verify]** |
| `ask "…"` | browser |
| `prompt list [--limit N] [--type chat\|structured]` | HTTP **[verify]** |
| `prompt get <id> [--format json\|md]` | HTTP **[verify]** |
| `prompt create --title T [--model M] [--system TEXT\|--system-file F]` | HTTP **[verify]** |
| `prompt rename <id> TITLE`, `prompt delete <id> -y` | HTTP **[verify]** |
| `prompt export <id> PATH`, `prompt import FILE` | HTTP **[verify]** |

`ask` options:

- Model and instructions: `-m MODEL`, `--system TEXT|--system-file F`.
- Sampling: `--temperature`, `--top-p`, `--max-tokens`.
- Thinking: `--thinking off|low|high|<budget>`.
- Tools: `--search`, `--url-context`, `--code-exec`.
- Structured output: `--schema FILE.json`.
- Inputs: `-f/--file` (repeatable inline attachments, up to the web limit **[verify]**) and `--prompt-file F`.
- Streaming: `--stream/--no-stream`. The default is to stream when stdout is a TTY, and never with `--json`.
- Saving: `--prompt <id>`, `--save [TITLE]`, `--no-save` (rules below).

Conversation state: in AI Studio, a chat *is* a saved prompt.

- With `--prompt <id>`, or a prompt set by `use`, `ask` loads the chat history, appends the user and model turns, and
  saves them back. Add `--no-save` to keep the turn ephemeral.
- With no prompt context, the turn is ephemeral. `--save [TITLE]` creates a library entry and returns its ID.
- JSON output: `{text, thoughts?, model, finish_reason, usage: {prompt_tokens, output_tokens,
  thinking_tokens?, total_tokens}, citations: [{uri, title?}], prompt_id?, turn_index?}`.

### 6.4 Media (images, video, audio, music)

> **As shipped (2026-10-08).**
> - `generate image "…" [--prompt-file F] [-m] [-o FILE] [--force] [--visible]` uses Gemini image
>   models (Imagen refuses this session).
> - `generate music "…" [--prompt-file F] [-m] [-o FILE] [--force] [--visible]` uses Lyria and is
>   synchronous. It returns `text` with the lyrics or `<instrumental>`.
> - Both return `{files: [{path, mime_type, bytes}], model, text?, warnings?}`.
> - Video comes from Google Flow (`aistudio flow video`, see `2026-10-07-flow-generation-design.md`),
>   because AI Studio's Veo page asks for a paid upgrade.
> - **2026-10-09:** `generate image --image IN` (repeatable) edits or combines images, and
>   `transcribe AUDIO [-m] [--timestamps|--no-timestamps] [--diarize|--no-diarize]
>   [--smart|--no-smart] [--vocabulary TEXT] [--timeout S] [-o FILE]` returns
>   `{text, model, audio, segments?: [{speaker, start, end, text, words?}], file?, warnings?}`.
>   Both upload the input to the user's Google Drive (AI Studio's folder). `--vocabulary` turns
>   timestamps and speaker labels off, because AI Studio refuses those combinations. A transcript
>   cut off at the model's output limit is kept, with a warning.
> - **2026-10-09 (plan J):** `transcribe` also takes `.mp4` and `.webm` audio (sent labelled
>   `audio/mp4` / `audio/webm`). A reply with plain text instead of a transcript is kept, with a
>   warning that it may be a refusal.
> - The table below is the original plan. `--count`, `--aspect`, `--lyrics`, `--duration` and
>   job commands were not built.

| Command | Behaviour | JSON |
|---|---|---|
| `generate image "…" [-m] [--count N] [--aspect R] [--image IN ...] -o DIR` | synchronous. Each `--image` (repeatable) supplies an input image to edit or compose. | `{files: [{path, mime_type}], model}` |
| `generate speech "…" [--text-file F] [-m MODEL] [--voice V] [--style TEXT] [-o FILE.wav] [--force] [--headless]` | synchronous; page-driven in a visible window (§5.4). Expression tags such as `<laugh>` go inline in the text. `--speakers` (multi-speaker) comes later. | `{files: [{path, mime_type, seconds}], model, voice, style}` |
| `voices list [--search TEXT]` | reads the speech page's voice panel (headless) | `{voices: [...], count}` |
| `transcribe AUDIO [-m] [--diarize] [--timestamps] [-o FILE.txt]` | synchronous | `{text, segments?: [{start, end, speaker?, text}], model}` |
| `generate music "…" [-m] [--lyrics F \| --instrumental] [--duration S] [--no-wait] [-o FILE]` | long-running **[verify]**, same wait and download rules as video | `--no-wait`: `{job_id, status}`. Otherwise `{job_id, status, files?}` |
| `generate video "…" [-m] [--aspect R] [--duration S] [--image FILE] [--no-wait] [-o FILE]` | long-running. By default it waits, then downloads to `-o` if given. `--no-wait` returns immediately. | `--no-wait`: `{job_id, status}`. Otherwise `{job_id, status, files?}` |
| `job list`, `job status <id>`, `job wait <id> [--timeout S]` | | `{job_id, kind, status, error?}` |
| `download <job_id> PATH` | ID-pinned, never "latest" | `{files: [...]}` |

- Every job is recorded in `jobs.json` with its remote operation reference, in case AI Studio does
  not list operations on the server **[verify]**.
- Job statuses: `pending`, `running`, `completed`, `failed`. `download` requires `completed`.
- `generate music` covers request-based song models such as Lyria 3.5. Real-time Lyria belongs to the
  `live` category (§6.6). If capture shows song generation is synchronous, the command simply returns
  files, as `generate image` does.

### 6.5 Agents (Phase 2)

AI Studio's "Build with Agents" section lists agents in the picker's `agents` category:

- **Antigravity Preview:** a general-purpose autonomous agent running in a remote, Google-hosted Linux
  environment.
- **Template agents:** AI Talk Radio (text source → simulated radio show with hosts, callers and music),
  Customer Support (scans a website into a knowledge base and answers questions), Data Analyst
  (interactive BI over the Northwind dataset), Document Processor (reconciles expenses and invoices
  and builds HTML slideshow reports), Repo Maintainer (analyses a codebase, answers questions and
  writes patches).

| Command | Behaviour |
|---|---|
| `agent list` | The `agents` category of the catalog, with each agent's declared inputs |
| `agent run <agent> "task" [-f FILE ...] [--url URL] [--repo PATH\|URL] [--no-wait]` | Starts a run and returns `{run_id, status}`. By default it waits and streams progress events to stderr. |
| `agent status <run_id>`, `agent wait <run_id> [--timeout S]` | `{run_id, agent, status, steps?, error?}` |
| `agent send <run_id> "message"` | Follow-up turn on a run that is waiting for input |
| `agent artifacts <run_id>` | Lists produced files (audio, HTML reports, patches, …) |
| `agent download <run_id> PATH [--artifact ID]` | ID-pinned download, never "latest" |
| `agent cancel <run_id> -y` | Stops a run |

- Runs are recorded in `jobs.json` with `kind: "agent"`, so `job list` shows them too.
- Run statuses: `pending`, `running`, `needs_input`, `completed`, `failed`, `cancelled`.
- Each template agent's input flags (`--url`, `--repo`, `-f`) are validated against the inputs
  the catalog declares for it **[verify]**.
- `--repo PATH` uploads a local repository archive. It honours `.gitignore` and refuses to upload files that
  look like secrets (`.env`, key files) unless `--include-secrets` is passed.
- The wire protocol (likely an Interactions-style session API) is designed from Phase 2 capture data.

### 6.6 Live (Phase 2)

| Command | Behaviour |
|---|---|
| `live [-m MODEL] [--voice V] [--system TEXT]` | Interactive session: microphone in, speaker out (`[live]` extra). Prints the transcript as it goes. Ctrl-C ends the session. |
| `live -m MODEL --input IN.wav --output OUT.wav [--transcript FILE]` | Non-interactive: streams a file in and records the reply. Usable from scripts and MCP. |
| `live -m <real-time Lyria> "prompt" [--bpm N] [--scale S] [--duration S] -o FILE` | Real-time music steered by prompt and parameters for a fixed duration, recorded to a file |

- `stream.py` carries a bidirectional session. The protocol (WebSocket, or WebChannel as reported for
  AI Studio Live) is decided by the Phase 2 capture. The BotGuard strategy from §5.4 applies to the session
  handshake.
- MCP gets only the non-interactive file forms (`live_file`, `live_music`).

### 6.7 Build: Apps and Gallery (Phase 3)

| Command | Behaviour |
|---|---|
| `app list`, `app get <id>` | Your apps from Build → Apps |
| `app export <id> PATH` | Downloads the app's source as a zip or a directory |
| `app create "description" [--platform web\|android] [--no-wait]` | Long-running agentic build. Returns `{app_id, job_id}`. |
| `app delete <id> -y` | Deletes an app |
| `gallery list [--search TEXT]`, `gallery get <id>` | Public gallery apps |
| `gallery remix <id> [--title T]` | Copies a gallery app into your apps and returns the new `app_id` |

- Deploy and publish actions stay out of scope until Phase 3 capture shows what they involve.

### 6.8 Output and errors

- Lists come back as `{"<items>": [...], "count": N}`.
- Errors come back on stdout as `{"error": true, "code", "message", "hint?"}`.
- Error codes: `AUTH_ERROR`, `RATE_LIMITED`, `QUOTA_EXCEEDED`, `BOTGUARD_REJECTED`, `SCHEMA_DRIFT`,
  `NOT_FOUND`, `VALIDATION_ERROR`, `NETWORK_ERROR`, `SERVER_ERROR`, `BROWSER_ERROR`, `TIMEOUT`.
  Unhandled exceptions use `UNEXPECTED_ERROR` (exit 2) and Ctrl-C uses `INTERRUPTED` (exit 130).
- Exit codes:
  - `0`: success
  - `1`: handled error
  - `2`: unexpected error, or a wait timeout (including a Flow job still running at `--timeout`)
  - `3`: `status --fail-if-active` found an active incident
  - `130`: interrupt

## 7. MCP server

- Entry point `aistudio-mcp [--profile P] [--output-dir DIR]`, using stdio. Logs go to stderr.
- A lifespan opens one `AIStudioClient` with keepalive. The browser is attached lazily (§5.3).
- Tools map 1:1 onto `_app/` operations:
  - Phase 1: `models_list`, `models_get`, `count_tokens`, `prompt_list`, `prompt_get`, `prompt_create`,
    `prompt_rename`, `prompt_delete`, `prompt_export`, `chat_ask`, `generate_image`, `generate_speech`,
    `transcribe`, `generate_video`, `generate_music`, `job_status`, `job_wait`, `job_download`.
  - Phase 2: `agent_list`, `agent_run`, `agent_status`, `agent_send`, `agent_download`, `agent_cancel`,
    `live_file`, `live_music`.
  - Phase 3: `app_list`, `app_get`, `app_export`, `app_create`, `app_delete`, `gallery_list`, `gallery_remix`.
- Tools that take a model validate its category (§6.2). Results from paid models include `cost_notice`.
- Read tools are annotated read-only. `prompt_delete` is annotated destructive and returns a
  `needs_confirmation` preview unless `confirm=true`. `agent_cancel` and `app_delete` follow the same rule.
- Media tools write into `--output-dir`, which defaults to `$AISTUDIO_HOME/output/`, and return absolute paths.
  `generate_image` also returns a small downscaled `ImageContent` preview.
- Errors come back as `ToolError {code, message, retriable, hint}`.
- `aistudio mcp install claude-code|claude-desktop|cursor` writes the client config.

## 8. Discovery tooling

### 8.1 `aistudio dev capture [--out tests/fixtures]`

- Opens the profile's Chrome headed and records every request and response to
  `alkalimakersuite-pa.clients6.google.com` and the upload hosts. Streamed bodies are captured once complete.
- Prints a checklist for the user to work through in the UI:
  1. Open the model picker and click every filter chip. Open one model card, then star and unstar a model.
  2. Open the library, then open a prompt.
  3. Chat four times with a Gemini model: once plain, once with Search, once with code execution, once with
     thinking. Then chat once with a Gemma model.
  4. Save, rename, and delete a prompt.
  5. Count tokens.
  6. Generate an image, then edit it. Generate speech, then transcribe a short audio file.
  7. Generate a video, then a song with a music model.
- Phase 2 and Phase 3 each start with their own shorter checklist (Live session, one run per agent type,
  Build/Gallery flows).
- Writes one JSON file per call to `<out>/<Method>/<n>.json` with the fields `{method, url, request: {headers, body},
  response: {status, headers, body}}`.
- **Redaction happens in memory, before anything is written.**
  - Removed: `Cookie`, `Set-Cookie`, `Authorization`, `X-Goog-Api-Key`, email addresses, `authuser`.
  - Replaced with stable fakes: Drive and prompt IDs (`prompt_1`, …).
  - Prompt text is kept, so the checklist tells the user to use throwaway content.

### 8.2 `aistudio dev replay <fixture> [--backend http|browser]`

Re-sends a captured request, re-signed with the current credentials. If it succeeds over HTTP, the RPC's
`transport` becomes `http`; if it is rejected over HTTP but succeeds in the browser, `browser`. The outcomes are written into
`registry.py` by hand, with the fixture cited.

## 9. Error handling

- `AIStudioError` is the base class. Subclasses: `AuthError`, `RateLimitError`, `QuotaExceededError`, `BotGuardError`,
  `SchemaDriftError`, `NotFoundError`, `ValidationError`, `NetworkError`, `ServerError`, `BrowserError`,
  `WaitTimeoutError`. The name avoids shadowing the builtin `TimeoutError`.
- gRPC status mapping:

  | Status | Error |
  |---|---|
  | 3 | `ValidationError` |
  | 5 | `NotFoundError` |
  | 8 | `RateLimitError`, or `QuotaExceededError` when the message mentions quota |
  | 7, 16 | `AuthError` (triggers §4.4) |
  | 13, 14 | `ServerError` (retriable) |
  | any other | `ServerError` (not retriable) |

- `SchemaDriftError` carries `{method, index_path, expected}` and the hint
  `re-capture <Method> with "aistudio dev capture"`.
- Retry policy:
  - Idempotent RPCs retry on 429, 503, and status 14 with exponential backoff and jitter, at most 3 attempts.
  - Non-idempotent RPCs retry only when the connection failed before a response arrived.
  - **Generation is never retried automatically.**
- Credentials, cookies, and the API key are never logged. `-vv` logs the method name and redacted headers.

## 10. Testing

- **Golden params:** for each fixture, `build_params(inputs) == fixture.request.body`.
- **Catalog:** category → command mapping, category validation errors, `unsupported_category` handling,
  paid `cost_notice`, cache expiry.
- **Golden rows:** each fixture's response decodes into the expected typed objects. Truncated or
  shifted fixtures raise `SchemaDriftError`.
- **Signer:** a known-answer test vector for `SAPISIDHASH` and the 1P/3P variants.
- **HTTP transport:** respx serves fixtures. A table-driven test covers gRPC status → exception
  mapping and the retry and refresh behaviour.
- **Browser backend:** tested through `BotGuardStrategy` with a fake page object. No real browser runs in the default suite.
- **CLI:** click `CliRunner` with a fake client. Snapshots of the JSON envelopes, plus exit-code assertions.
- **MCP:** an in-memory fastmcp client against a fake client, including the confirm gating.
- **Live (opt-in):**
  - `pytest -m live` runs auth check, `models list` for every category, prompt create → get → rename → delete (with cleanup), one
    `ask` on a cheap model, and one image.
  - `-m live_slow` adds video and music.
  - Requires `AISTUDIO_PROFILE`.
- Lint and format with `ruff`. Run the suite with `uv run pytest`.

## 11. Milestones

| # | Deliverable | Exit criterion |
|---|---|---|
| **Phase 1** | | |
| M0 | Scaffold, paths and profiles, storage, `login`, signer, `auth check` (cookies only) | `aistudio login` persists cookies; signer KAT passes |
| M1 | `dev capture` and `dev replay`, then a **capture session with the user** | Fixtures for every Phase 1 RPC; transport per RPC decided; BotGuard strategy chosen |
| M2 | HTTP backend, `wire.py`, models, prompt library CLI | Golden tests pass; live prompt CRUD round-trip passes |
| M3 | Browser daemon and backend, BotGuard strategy, `ask` | Live `ask` passes, both ephemeral and appended to a saved chat |
| M4 | Model catalog (categories, pricing, star, category validation), media (image and edit, speech, transcribe, video, music), jobs, download | Live catalog, image and speech tests pass; video and music pass in `live_slow` |
| M5 | Phase 1 MCP server and `mcp install` | MCP tests pass; tools work from Claude Code |
| **Phase 2** | | |
| M6 | Phase 2 capture session, then a spec addendum for Live and Agents (protocols, success criteria) | Addendum approved |
| M7 | Agents: `agent` commands, run tracking, artifacts, MCP tools | Live run of Antigravity and one template agent passes |
| M8 | Live: `stream.py`, `live` (file mode, then interactive), real-time Lyria, MCP file tools | Live file round-trip passes |
| **Phase 3** | | |
| M9 | Phase 3 capture session, then a spec addendum for Build | Addendum approved |
| M10 | Apps and Gallery commands and MCP tools | Live app list, export, and gallery remix pass |

**Gate after M1:** stop and revisit this design before M2 if either of these holds:

- prompt-library RPCs cannot run over HTTP;
- neither BotGuard strategy works in headed or headless Chrome.

## 12. Hypotheses to verify in M1

| # | Hypothesis | If false |
|---|---|---|
| H1 | `X-Goog-Api-Key` is a static web key that can be scraped from the page or bundle | Capture it from live traffic at login and refresh it on rejection |
| H2 | Prompt-library RPCs (list/get/create/update/delete) do not need BotGuard | Route them to the browser backend; HTTP is left with models and token counting only |
| H3 | Errors use a `[,[code,"msg"]]` array envelope | Adjust `wire.py` to match the captured shape |
| H4 | The BotGuard token is not bound to the request content | Use `SnapshotCallStrategy` only |
| H5 | Video generation returns an operation that can be polled by reference | Poll through the UI's own polling RPC, or the Drive file |
| H6 | Inline attachments are accepted up to a usable size | Add an upload step (Scotty/Drive) in M3 |
| H7 | `CountTokens` and `ListModels` work over plain HTTP | Route them to the browser backend |
| H8 | The catalog metadata (categories, badges, pricing, cutoff, release date, starred) comes from one RPC such as `ListModels` | Merge several RPCs in `rows/models.py`. If pricing exists only in the page bundle, scrape it at catalog refresh. |
| H9 | Lyria song generation is a request or a pollable job, not a real-time stream | Move `generate music` onto the Phase 2 stream transport |
| H10 | Star and unstar are server-side preferences | Store stars locally in `models.json` |

## 13. Risks

- **Breakage:** an undocumented API can change without notice. Mitigations: the wire code is isolated,
  `SchemaDriftError` points at the failing field, `doctor --wire` checks for drift, and `dev capture` is cheap to re-run.
- **Account and ToS:** automated use of a personal session may be flagged. The README warns about this and
  recommends a dedicated Google account. Default request pacing stays modest, and nothing loops on retries.
- **BotGuard escalation:** Google may tighten checks against headless or CDP-driven browsers. Headed daemon mode is the
  fallback, and the strategy interface lets the approach be replaced.
- **Local exposure:** while the daemon runs, any local process can use the CDP port to drive the signed-in browser. The port
  binds to 127.0.0.1, and `browser.json` has mode `0600`. The README documents stopping the daemon when it is not needed.
