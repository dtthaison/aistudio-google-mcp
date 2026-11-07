# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once the first release is tagged.

## Unreleased

### Added
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

### Changed
- CI also checks types (mypy), bandit's security rules (ruff `S`), secrets in the whole history
  (gitleaks), known-vulnerable dependencies (pip-audit) and the workflows themselves (zizmor),
  and pins every action to a commit SHA.
- Type fixes found by mypy, with no change in behaviour.
