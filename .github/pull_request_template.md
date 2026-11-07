## Summary

<!-- What changes for a user of the CLI, one bullet per theme. Lead with the user-visible part. -->
-

## Test plan

<!-- Real commands and their results. Tick only what you ran. -->
- [ ] `uv run ruff check src tests`
- [ ] `uv run mypy`
- [ ] `uv run pytest -q`: N passed
- [ ] Live, free: <!-- the command you ran against the real site and what it showed, or "none" -->
- [ ] Live, paid: <!-- the command and what it cost, or "not run" and why -->

## Checklist

- [ ] No cookies, tokens, emails or other private data in the diff (captures went through `aistudio dev redact`)
- [ ] A command that spends credits still needs `--max-credits`
- [ ] `README.md` and `integrations/claude-code/aistudio/SKILL.md` updated for user-visible changes
- [ ] New facts about the live site are in `docs/wire/findings.md`

## Not verified

<!-- Anything you could not check, and why. Delete this section if everything is verified. -->
