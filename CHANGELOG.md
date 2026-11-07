# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## Unreleased

## 0.2.0 - 2026-11-07

### Added
- `aistudio spend [--since 7d|2026-11-01] [--command flow] [--json]`: a local log of every run
  that spent or may have spent (Flow video, image, extend, edit, music, characters, Tools run
  with `--allow-spend`, and AI Studio speech, image, music, transcription and Build), including
  timeouts and failures, with totals per unit. The log is `profiles/<name>/spend.jsonl` and holds
  no prompt text, cookies or e-mail addresses. Quote-only runs are not logged.
- `aistudio flow image --prompts-file prompts.jsonl -o <folder>` makes images for many prompts
  in one run: one JSON string (or `{"prompt": "..."}`) per line, run one at a time in one
  browser session, saved as `<number>-<words of the prompt>-<n>.jpg`. The model, aspect, count,
  characters and ingredients apply to every prompt. A paid model is quoted for every prompt
  first and `--max-credits` is the budget for the whole batch; the first error stops the batch,
  lists what finished and gives `--start-at N` to resume. Nothing is retried.
- `aistudio prompt get <id-or-prefix>` reads one saved prompt or chat in full: title, model,
  system instructions, generation settings and every turn, untruncated. Attachments show as types
  only. `--json` for scripts, `--thoughts` for saved thinking summaries, `-o file.md` to save it as
  markdown (`--force` to overwrite).
- `aistudio flow music list [--search TEXT]`, `flow music get ID` and
  `flow music download ID [-o FILE] [--wav] [--force]`: your Flow Music songs (id, title, length,
  status including failed and unfinished, whether it has lyrics), a song's prompt and lyrics,
  and a free download of a song you already have. None of them spends credits, and `download`
  never overwrites a file without `--force`. `flow music PROMPT` works as before (also as
  `flow music generate PROMPT`).
- `aistudio flow storyboard FILE.yaml`: a whole Flow scene from one file (characters, location
  plates, fixed style and wardrobe sentences, and ordered shots that join by `cut`, `frame` or
  `extend`). It checks the file first, then shows the plan and one total quote and spends
  nothing; `--keyframes` also makes the free keyframes; `--max-credits N -o scene.mp4` makes the
  clips in order and joins them. A state file makes a re-run continue instead of paying again.
  Adds the `pyyaml` dependency.

### Security
- `certifi` 2026.10.23 and, on Python 3.11 or later, `filelock` 4.2.9, which fix known
  vulnerabilities. Python 3.10 keeps `filelock` 4.1.0, the last release for it; the accepted
  findings and why they do not apply are in `scripts/audit-ignore.txt`.

### Added
- `aistudio app diff <app>`: what the last builder turn changed in an app's files, as a summary and
  a unified diff per file. `--turn N` picks another turn; `--against <dir>` compares the current
  code with a folder (for example an earlier `app export`); `--json` for scripts. Free; it
  downloads only the files that differ, each checked against its SHA-256.

### Changed
- CI also checks types (mypy), bandit's security rules (ruff `S`), secrets in the whole history
  (gitleaks), known-vulnerable dependencies (pip-audit) and the workflows themselves (zizmor),
  and pins every action to a commit SHA.
- Type fixes found by mypy, with no change in behaviour.
