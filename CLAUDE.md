# aistudio: notes for coding agents

An unofficial CLI that drives Google AI Studio, Google Flow and Google Flow Music through the
user's own signed-in session. Read `CONTRIBUTING.md` for setup and the checks; this file is the
rule table.

## Rules and what enforces them

A rule that something enforces is checked for you; a rule marked "judgment" is on you. When a
reviewer corrects the same mistake twice, add a check for it and a row here, in the same change.

| Rule | Enforced by |
|---|---|
| No credential, session cookie, token or real person's email in a committed file | `tests/unit/test_no_secrets.py`, gitleaks (CI job `security`, pre-commit) |
| Captured wire data goes through `aistudio dev redact` before it is committed | the same secret checks; judgment for anything they cannot recognise |
| A command spends credits only when the user passes `--max-credits` (never a default) | `tests/cli/test_spend_guard.py` |
| Live tests never run in CI or by default | `addopts` in `pyproject.toml` deselects `live` and `live_slow` |
| Lint, bandit security rules, and types pass | `ruff check src tests`, `mypy` (CI job `test`, pre-commit for ruff) |
| A security-rule exception sits on its own line with a reason: `# noqa: S110 (why)` | ruff `S` rules in `pyproject.toml` |
| Locked dependencies have no known vulnerability, or an accepted one with a reason and review date | `scripts/audit-deps.sh` with `scripts/audit-ignore.txt` (CI job `security`) |
| Workflows pin actions to commit SHAs and keep the token read-only | zizmor (CI job `security`, pre-commit), Scorecard |
| Ask the user before any live run that spends quota or credits, with the exact cost | judgment |
| Never answer Flow's rights question (`--i-have-rights`) for the user | judgment |
| Never delete the user's AI Studio Build apps or anything in Flow or Flow Music | judgment |
| Facts learned from the live site (request shapes, prices, failures) go in `docs/wire/findings.md` | judgment |
| A user-visible change updates `README.md` and `integrations/claude-code/aistudio/SKILL.md` | judgment |

## Shape of the code

- `src/aistudio/cli/`: click commands; thin, they call one use case each.
- `src/aistudio/_app/`: use cases, one async function per command, with the browser and
  downloader injected so tests replace them.
- `src/aistudio/_flow/`, `_browser/`, `_rpc/`: page driving and wire parsing; pure parsers stay
  free of Playwright.
- `tests/conftest.py` `FakeUi`: the scripted stand-in for a browser page.
