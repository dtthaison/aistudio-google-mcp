# aistudio-py

Unofficial CLI and MCP server that automates the Google AI Studio web app
(https://aistudio.google.com) with your own Google session.

> **Warning:** this project uses undocumented internal endpoints of AI Studio. It can break
> whenever Google ships a change, and automating a personal Google session may violate
> Google's Terms of Service. Use a dedicated Google account, and use it at your own risk.

## Install (development)

```bash
brew install uv
uv sync --extra dev
```

## Quick start

```bash
uv run aistudio login                       # sign in once in a browser window
uv run aistudio auth check --test           # verify the stored session with one real call
uv run aistudio models list --category music
uv run aistudio models get lyria-3.5
uv run aistudio prompt list --type chat
uv run aistudio count-tokens "hello world" -m gemini-flash-latest
uv run aistudio app list                    # your Build apps
uv run aistudio app get <id-prefix>         # one app and its builder runs
uv run aistudio gallery list --tag google-maps
uv run aistudio dev capture                 # record AI Studio traffic into tests/fixtures
```

Design: `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`

## Use from any Claude Code session

```bash
uv tool install --editable '.[browser]'      # puts `aistudio` on PATH (~/.local/bin); tracks this repo
mkdir -p ~/.claude/skills/aistudio
cp integrations/claude-code/aistudio/SKILL.md ~/.claude/skills/aistudio/SKILL.md
```

Then, in any project, ask Claude things like "which music models do I have in AI Studio and what
do they cost?" or "list my AI Studio Build apps and their prompts". The `aistudio` skill makes
Claude run the CLI with `--json` against your own account. Remove with
`uv tool uninstall aistudio-py` and `rm -r ~/.claude/skills/aistudio`.
