---
name: aistudio
description: Use when the user asks about their own Google AI Studio (aistudio.google.com) account — which models they can use and what they cost, their saved prompts or chats, their Build apps (aistudio.google.com/apps) and the prompts behind them, the Build gallery, token counts, TTS voices, generating speech, images or music with AI Studio, editing an image, transcribing audio to text, or video/images with Google Flow — or mentions the aistudio CLI.
---

# aistudio CLI

## Overview

`aistudio` is a CLI installed on this machine. It reads the user's **own** AI Studio account
through their saved Google session, from any directory. Use it instead of Google's public docs
whenever the question is about the user's account: the docs cannot see their models, prompts or
apps.

## Every call

1. Run `aistudio auth check --test --json` once per task.
   - `"status": "ok"` → continue.
   - `AUTH_ERROR` → stop. Tell the user to sign in by typing `! aistudio login` (it opens a
     browser). Do not run `aistudio login` yourself.
2. Add `--json` to every command and read the JSON. Never pipe human output through `head` or
   `cut`: tables and long prompts get truncated.
3. Errors print `{"error": true, "code", "message", "hint"}` and exit 1. Relay the `hint`.
4. Unsure of a flag? Run `aistudio <command> --help`. Never invent flags.

## Commands (read-only, no quota, no browser)

| Question | Command |
|---|---|
| Models, categories, prices, release dates | `aistudio models list --json [--category featured\|gemini\|gemma\|images\|video\|audio\|music\|live\|agents] [--search TEXT]` |
| One model's card | `aistudio models get <id-or-unique-prefix> --json` |
| Saved prompts/chats | `aistudio prompt list --json [--type chat\|video]` |
| Token count | `aistudio count-tokens "text" -m <model> --json` (or `--prompt-file F`) |
| Build apps | `aistudio app list --json` |
| One app + its prompt + builder runs | `aistudio app get <id-or-unique-prefix> --json` |
| Recently opened items | `aistudio app recent --json` |
| Service incidents | `aistudio status --json [--active]` |
| Build gallery / templates | `aistudio gallery list --json [--group showcase\|template] [--tag T] [--search T]`, `aistudio gallery get <slug-or-prefix> --json` |

JSON notes:
- A model's `pricing` is a list of `{direction, label, price_usd, up_to_tokens}`; `pricing_summary`
  holds free-text pricing.
- `command` says which CLI command runs the model.
- `models list` is cached for 1 hour; add `--refresh` for fresh data.

## Commands that change things (only when the user asks)

**Never put the user's text inside double quotes in a shell command**: `` ` ``, `$(…)` and `$VAR`
would run or expand. For speech, images and music, write the text to a file with your file tool and pass
`--text-file` / `--prompt-file`. For other text arguments (`app create`, `app prompt`, `--style`), use single
quotes and write each `'` as `'\''`.

| Action | Command |
|---|---|
| Star / unstar a model (saved to the account) | `aistudio models star <id> --json`, `aistudio models unstar <id> --json` |
| Download an app's code into a folder | `aistudio app export <app-id-or-prefix> <dir> --json` |
| TTS voices (opens a hidden Chrome; no quota) | `aistudio voices list --json [--search T]` |
| Text to speech (uses quota; minimized Chrome; writes a .wav) | `aistudio generate speech --text-file <file> [--voice V] [--style S] -o out.wav --json` |
| Image (uses quota, about $0.034; minimized Chrome) | `aistudio generate image --prompt-file <file> [-m <images model>] -o out.jpg --json` |
| Edit or combine images (same price) | the same command plus `--image <file>` (repeatable) |
| Speech to text (uses quota, a fraction of a cent per short clip) | `aistudio transcribe <audio file> [--timestamps] [--diarize] [--vocabulary '<names>'] -o out.txt --json` |
| Music (uses quota, $0.04 a clip / $0.08 a song; minimized Chrome; writes .mp3) | `aistudio generate music --prompt-file <file> [-m lyria-3.5] -o out.mp3 --json` |
| Flow credits left (free; minimized Chrome) | `aistudio flow credits --json` |
| Flow video quote, or a free Nano Banana 2.1 image (spends nothing) | `aistudio flow video\|image --prompt-file <file> [-m MODEL] [--aspect A] [--count N] [--duration S] --json` |
| Flow video / image, paid (spends Flow credits) | the same command plus `--max-credits <quote>` and `-o <path>` |
| Flow projects and media ids (free) | `aistudio flow projects --json`, `aistudio flow media [--project P] [--type video\|image] --json` |
| Download Flow media (free) | `aistudio flow download <media-id> -o <path> --json` |
| Frames (video) and reference images (video or image) | `--start-frame X`, `--end-frame X` (video only), `--ingredient X` (repeatable); X is a media id from `flow media`, or a local image plus `--i-have-rights` |
| Flow character from a description (Nano Banana 2.1: 0 credits) | `aistudio flow character create '<Name>' '<description>' -o robot.png --json` |
| Flow character from a photo (free; nothing is generated) | `aistudio flow character create '<Name>' --photo <image media id> --json` (a local file also needs `--i-have-rights`) |
| Flow characters: list, rename, personality (free) | `aistudio flow character list --json`, `aistudio flow character rename <id> '<Name>' --json`, `aistudio flow character personality <id> '<notes>' --json` (or `--personality` on create) |
| Feature characters in a Flow video / image | `--character '<Name or id>'` (repeatable) on `flow video\|image`; the quote rules below still apply |
| Flow scenes: list, join clips, download a scene (free) | `aistudio flow scene list --json`, `aistudio flow scene stitch <id> <id>@START-END -o out.mp4 --json`, `aistudio flow scene download <scene-id> -o out.mp4 --json` |
| Character portrait model / full body (0 credits measured) | `aistudio flow character create '<Name>' '<description>' -m nano-banana-pro\|nano-banana-2-lite --json`, `aistudio flow character body <id> '<prompt>' -o body.png --json` |
| Edit a Flow clip (Omni 1.1 Flash, 8-second clips: **20 credits, no quote from Flow**) | `aistudio flow edit <media-id> '<instruction>' --max-credits 20 -o out.mp4 --json` |
| Extend a Flow video (Veo 3.1 Lite: 10 credits, **no quote from Flow**) | `aistudio flow extend <media-id> '<what happens next>' --max-credits 10 -o next.mp4 --json` |

- `models list --json` gives each model `starred`: `true`/`false`, or `null` when the stars could
  not be read. Say "unknown", not "not starred".
- An AI Studio tab open at the same time can overwrite a star change.
- Speech: expression tags such as `<laugh>` go inside the text.
- `--image` and `transcribe` upload the file to the user's Google Drive (AI Studio's folder):
  say so when the file is private. `--vocabulary` turns timestamps and speaker labels off
  (AI Studio cannot combine them); pass brand or person names there, or they may be dropped.
  Without it, AI Studio's defaults return `segments` with speakers and word times. A long
  recording may need `--timeout`; a transcript cut off at the output limit comes with a warning.
- Generation spends the user's quota or money: run one request per user ask and never retry in a
  loop. An existing output file is never overwritten without `--force`, which needs the user's OK.
- **Free Flow models generate at once.** With the default image model (Nano Banana 2.1) Flow
  does not ask, so `flow image` without `--max-credits` already makes and downloads the images
  (`"status": "done"`, `credits_spent` 0, a warning saying Flow did not ask). Run it only when
  the user wants the images. A `TIMEOUT` error saying Flow "was still generating without asking"
  means Flow may still finish the images: tell the user to check the project in Flow, never
  re-run it blindly. Video models quote first (verified). Other image models are
  refused until measured (`VALIDATION_ERROR` naming the model): use `-m nano-banana-2.1`.
- **Flow: never pass `--max-credits` unless the user gave a budget for this request.**
  1. Run without it first. The result has `"status": "quoted"`, `credits` and `plan`, and Flow's
     request is declined (nothing waits in Flow). If `"declined": false`, Flow's request is still
     waiting: tell the user to press Reject on it in Flow, never Approve.
  2. Show the user the cost and ask.
  3. Re-run with `--max-credits <credits>`.

  For `flow video`/`flow image`, `OVER_BUDGET` and `PLAN_MISMATCH` mean Flow's offer was rejected
  and nothing was spent. For `flow extend`/`flow character create`, `OVER_BUDGET` is aistudio's own
  refusal before anything was sent.
  A quote-only run with a local file (`--i-have-rights`) still uploads the file. Pass the reported
  `uploads[].media_id` in the paid run, because the same file name cannot be uploaded twice.
  `"status": "timeout"` means credits were spent: give the user `project_url`.
  After a `--max-credits` run, any other error may mean credits were spent (the hint says so and
  names the media): never re-run it without the user's OK.
  A `VALIDATION_ERROR` saying Flow generates without asking means Flow's "Confirm before
  generating" is set to Never. Tell the user; aistudio will not change it.
- **Never pass `--i-have-rights`** unless the user confirmed they have the rights to that exact
  file. It answers Flow's "Rights to use this image" question for them.
- Flow character names are unique per project (Flow attaches characters by name). If
  `--character` says several characters share a name, show the user `flow character list` and ask
  which one to rename; never pick one yourself. A `PLAN_MISMATCH` about characters or media means
  Flow's request carried other attachments than asked (e.g. a leftover in Flow's prompt box): it
  was rejected and nothing was spent. Tell the user to check the prompt box in Flow before a
  re-run. A character's image id is not an ingredient: use `--character` instead.
- After `flow extend`, `flow scene download <scene_id>` gives the source and the extension joined.
  Stitch trims are whole seconds (`<id>@2-5`).
- `flow edit` costs 20 credits with no quote: ask the user before passing `--max-credits 20`.
  Only 8-second Veo 3.1 Lite clips have a measured price. Flow always extends with Veo 3.1 Lite
  (also for Fast clips), so there is no Fast/Quality extend.
- `flow extend` and `flow character create` are charged without a quote. aistudio checks its own
  measured price table. Ask the user before passing `--max-credits` for an extend, and relay any
  warning that Flow charged a different amount. "no measured price" is final: do not retry with a
  higher `--max-credits`.
- Image and music results: `files[]` holds `path`, `mime_type` and `bytes`. Music `text` is the
  lyrics, or `<instrumental>`. Relay any `warnings` (e.g. the file's extension does not match its
  type).
- Browser commands (`app create/prompt`, `generate …`, `transcribe`, `voices list`, every `flow …`) share one
  Chrome
  profile: run them one at a time. A "profile is in use" error means another one is running;
  wait for it rather than retrying in a loop.
- `app export` refuses a non-empty folder. Never retry with `--force` (which overwrites files)
  without the user's OK.

## Not available yet

These are not in the CLI yet. Say so plainly; do not drive the browser or call Google's internal
endpoints yourself.

- Video in AI Studio (Veo asks for a paid upgrade): use `aistudio flow video` (Flow credits).
- Imagen: AI Studio's Imagen page refuses this kind of session. Use a Gemini image model.
- Chatting with a model: it requires a Gemini API key, which is not supported.

## Build or change an app (only when the user asks)

```bash
aistudio app create "<idea>" --json             # new app
aistudio app prompt <app-id-or-prefix> "<change>" --json
```

- Both take a few minutes, use the user's Build quota, and open a minimized Chrome window.
  Run them in the background if your harness supports that.
- Result: `{app_id, url, title, status, files, seconds}`. Read `status` from the JSON, not the
  exit code. `"timeout"` means aistudio stopped waiting before the builder finished: give the user
  the `url` to check.
- Pass the user's own words as the idea/change; do not expand them.

## Ask first

Get the user's explicit OK before:
- `aistudio auth logout` and `aistudio profile delete`, which delete credentials;
- any `aistudio dev …` command, which opens a browser and records traffic.
- `--force` on `aistudio app export`, which overwrites files in the target folder.

`--profile NAME` selects another signed-in Google account (`aistudio profile list --json`).
