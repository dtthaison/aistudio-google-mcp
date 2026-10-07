---
name: aistudio
description: Use when the user asks about their own Google AI Studio (aistudio.google.com) account — which models they can use and what they cost, their saved prompts or chats, their Build apps (aistudio.google.com/apps) and the prompts behind them, the Build gallery, token counts, or TTS voices — or mentions the aistudio CLI.
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
would run or expand. For speech, write the text to a file with your file tool and pass
`--text-file`. For other text arguments (`app create`, `app prompt`, `--style`), use single
quotes and write each `'` as `'\''`.

| Action | Command |
|---|---|
| Star / unstar a model (saved to the account) | `aistudio models star <id> --json`, `aistudio models unstar <id> --json` |
| Download an app's code into a folder | `aistudio app export <app-id-or-prefix> <dir> --json` |
| TTS voices (opens a hidden Chrome; no quota) | `aistudio voices list --json [--search T]` |
| Text to speech (uses quota; minimized Chrome; writes a .wav) | `aistudio generate speech --text-file <file> [--voice V] [--style S] -o out.wav --json` |

- `models list --json` gives each model `starred`: `true`/`false`, or `null` when the stars could
  not be read. Say "unknown", not "not starred".
- An AI Studio tab open at the same time can overwrite a star change.
- Speech: expression tags such as `<laugh>` go inside the text. An existing output file is never
  overwritten without `--force`, which needs the user's OK.
- Browser commands (`app create/prompt`, `generate speech`, `voices list`) share one Chrome
  profile: run them one at a time. A "profile is in use" error means another one is running;
  wait for it rather than retrying in a loop.
- `app export` refuses a non-empty folder. Never retry with `--force` (which overwrites files)
  without the user's OK.

## Not available yet

These are not in the CLI yet. Say so plainly; do not drive the browser or call Google's internal
endpoints yourself.

- Generating images, video or music.
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
