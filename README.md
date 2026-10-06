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
uv run aistudio login          # sign in once in a browser window
uv run aistudio auth check     # verify the stored session
uv run aistudio dev capture    # record AI Studio traffic into tests/fixtures
```

Design: `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`
