# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## Unreleased

### Added
- `aistudio flow music list [--search TEXT]`, `flow music get ID` and
  `flow music download ID [-o FILE] [--wav] [--force]`: your Flow Music songs (id, title, length,
  status including failed and unfinished, whether it has lyrics), a song's prompt and lyrics,
  and a free download of a song you already have. None of them spends credits, and `download`
  never overwrites a file without `--force`. `flow music PROMPT` works as before (also as
  `flow music generate PROMPT`).

### Security
- `certifi` 2026.10.23 and, on Python 3.11 or later, `filelock` 4.2.9, which fix known
  vulnerabilities. Python 3.10 keeps `filelock` 4.1.0, the last release for it; the accepted
  findings and why they do not apply are in `scripts/audit-ignore.txt`.

### Changed
- CI also checks types (mypy), bandit's security rules (ruff `S`), secrets in the whole history
  (gitleaks), known-vulnerable dependencies (pip-audit) and the workflows themselves (zizmor),
  and pins every action to a commit SHA.
- Type fixes found by mypy, with no change in behaviour.
