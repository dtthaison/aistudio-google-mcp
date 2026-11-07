# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## Unreleased

### Added
- `aistudio spend [--since 7d|2026-11-01] [--command flow] [--json]`: a local log of every run
  that spent or may have spent (Flow video, image, extend, edit, music, characters, Tools run
  with `--allow-spend`, and AI Studio speech, image, music, transcription and Build), including
  timeouts and failures, with totals per unit. The log is `profiles/<name>/spend.jsonl` and holds
  no prompt text, cookies or e-mail addresses. Quote-only runs are not logged.

### Security
- `certifi` 2026.10.23 and, on Python 3.11 or later, `filelock` 4.2.9, which fix known
  vulnerabilities. Python 3.10 keeps `filelock` 4.1.0, the last release for it; the accepted
  findings and why they do not apply are in `scripts/audit-ignore.txt`.

### Changed
- CI also checks types (mypy), bandit's security rules (ruff `S`), secrets in the whole history
  (gitleaks), known-vulnerable dependencies (pip-audit) and the workflows themselves (zizmor),
  and pins every action to a commit SHA.
- Type fixes found by mypy, with no change in behaviour.
