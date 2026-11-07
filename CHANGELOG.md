# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## Unreleased

### Added
- `aistudio prompt get <id-or-prefix>` reads one saved prompt or chat in full: title, model,
  system instructions, generation settings and every turn, untruncated. Attachments show as types
  only. `--json` for scripts, `--thoughts` for saved thinking summaries, `-o file.md` to save it as
  markdown (`--force` to overwrite).

### Security
- `certifi` 2026.10.23 and, on Python 3.11 or later, `filelock` 4.2.9, which fix known
  vulnerabilities. Python 3.10 keeps `filelock` 4.1.0, the last release for it; the accepted
  findings and why they do not apply are in `scripts/audit-ignore.txt`.

### Changed
- CI also checks types (mypy), bandit's security rules (ruff `S`), secrets in the whole history
  (gitleaks), known-vulnerable dependencies (pip-audit) and the workflows themselves (zizmor),
  and pins every action to a commit SHA.
- Type fixes found by mypy, with no change in behaviour.
