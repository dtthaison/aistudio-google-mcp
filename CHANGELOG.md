# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## Unreleased

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
