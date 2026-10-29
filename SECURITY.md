# Security

aistudio works with your signed-in Google session: the cookies of the account you used for
AI Studio and Flow. A bug in it can expose that session or act on your account, so please
report anything like that privately.

## Reporting

Use GitHub's private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Please don't open a public issue for it. Include what you found,
how to reproduce it and the aistudio version (`aistudio --version`).

## What aistudio promises

- Your session stays in your profile folder (`~/.aistudio/profiles/<name>/`, or under
  `$AISTUDIO_HOME`). The folders are owner-only (0700) and the session files are 0600.
- It only talks to Google: `aistudio.google.com`, its internal RPC host
  (`alkalimakersuite-pa.clients6.google.com`), `flow.google.com` and the hosts those pages load.
  There is no telemetry.
- It never spends Flow credits without `--max-credits`, and never approves a Flow quote above
  that budget. It reads your credits before and after, and warns if Flow charged something
  other than its quote.
- It never agrees to Flow's "Rights to use this image" statement unless you pass
  `--i-have-rights`.
- It controls Chrome through Playwright's pipe, not a network port: its Chrome opens no
  remote-debugging port that another program could attach to.
- `aistudio dev capture` runs everything it records through the redactor
  (`src/aistudio/_dev/redact.py`) before writing it to disk.

A way around any of these is a security bug.

## Out of scope

- What Google does with your account, prompts or generated content.
- Google refusing or limiting automated use of your account. See the warning in the README.
- What a generated app, image, clip or transcript contains: aistudio saves it as Google
  returns it.
