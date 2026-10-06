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
| Build gallery / templates | `aistudio gallery list --json [--group showcase\|template] [--tag T] [--search T]`, `aistudio gallery get <slug-or-prefix> --json` |

JSON notes:
- A model's `pricing` is a list of `{direction, label, price_usd, up_to_tokens}`; `pricing_summary`
  holds free-text pricing.
- `command` says which CLI command runs the model.
- `models list` is cached for 1 hour; add `--refresh` for fresh data.

## Not available yet

These are not in the CLI yet. Say so plainly; do not drive the browser or call Google's internal
endpoints yourself.

- Generating speech, images, video or music.
- Chatting with a model (`ask`).
- Exporting Build apps' code.

## Build or change an app (only when the user asks)

```bash
aistudio app create "<idea>" --json             # new app
aistudio app prompt <app-id-or-prefix> "<change>" --json
```

- Both take a few minutes, use the user's Build quota, and open a minimized Chrome window.
  Run them in the background if your harness supports that.
- Result: `{app_id, url, title, status, files, seconds}`. `status: "timeout"` (exit 2) means it
  is still building: give the user the `url`.
- Pass the user's own words as the idea/change; do not expand them.

## Ask first

Get the user's explicit OK before:
- `aistudio auth logout` and `aistudio profile delete`, which delete credentials;
- any `aistudio dev …` command, which opens a browser and records traffic.

`--profile NAME` selects another signed-in Google account (`aistudio profile list --json`).
