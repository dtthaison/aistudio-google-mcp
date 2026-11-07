# Contributing

Thanks for helping. aistudio drives Google's web apps with your own signed-in session, so two
things matter more here than in most projects: never commit anything from a real session, and
never spend someone's quota or credits without asking.

## Setup

macOS or Linux, Python 3.10 or later, [uv](https://docs.astral.sh/uv/) and Google Chrome.

```sh
uv sync --extra dev
uv run pre-commit install        # fast checks before each commit
brew install gitleaks            # or see https://github.com/gitleaks/gitleaks#installing
```

## The checks

CI runs these on every pull request. Run them before you push:

```sh
uv run ruff check src tests      # lint, including bandit's security rules
uv run mypy                      # types
uv run pytest -q                 # unit tests; live tests are skipped
scripts/audit-deps.sh            # known-vulnerable dependencies
gitleaks detect --redact --source .   # secrets anywhere in the history
```

The rule table in [`CLAUDE.md`](CLAUDE.md) lists every rule and what enforces it.

## Live tests and captures

Tests marked `live` or `live_slow` drive the real site with a signed-in profile. They are
opt-in: `uv run pytest -m live`. Some of them spend AI Studio quota or Flow credits, so read a
test before you run it.

Wire captures (`aistudio dev capture`) are redacted as they are written, but read every file
before you commit it. Cookies, tokens, emails and file ids must never reach the repo; the
secret checks above catch the common ones, not all.

## Pull requests

- One topic per pull request, branched from an up-to-date `main`.
- Fill in the template: what changed for users, and the test plan with real commands and
  results. Say plainly what you could not verify.
- A user-visible change updates `README.md` and `integrations/claude-code/aistudio/SKILL.md`.
  What you learned about the live site goes in `docs/wire/findings.md`.
- Paid runs: write down what each one cost.

## Reporting problems

Bugs: open an issue with the bug template. Security problems: use private reporting, see
[`SECURITY.md`](SECURITY.md). Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md).
