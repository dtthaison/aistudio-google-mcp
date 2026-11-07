"""No committed file may hold a real credential or a real person's email.

This repo commits captured wire fixtures, findings and sample media, so a session cookie or token
pasted from a probe is the most likely leak. gitleaks covers generic secrets in CI; this test
covers the values this project handles, and runs with the unit tests on every machine.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BINARY = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".m4a", ".wav", ".mp3", ".ico"}

PATTERNS = {
    "Google OAuth access token": re.compile(r"ya29\.[A-Za-z0-9_-]{30,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "Supabase session cookie": re.compile(r"base64-eyJ[A-Za-z0-9_-]{20,}"),
    "Google session cookie": re.compile(
        r"(?:SAPISID|APISID|HSID|SSID|__Secure-[13]P(?:SID|APISID)[A-Za-z]*)\"?\s*[=:,]\s*"
        r"\"?(?:\"value\"\s*:\s*\")?[A-Za-z0-9_./-]{25,}"),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,}"),
}

# Values that only look secret: each is made up, and a test or doc uses it on purpose.
FAKE = {
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnX3BhcnQtMQ",  # test_redact's JWT
    "someone.real@gmail.com", "jane.doe@gmail.com",  # test_redact's personal-data inputs
    "x10someone.real@gmail.com",  # the same address after an escape in a captured body
    "noreply@anthropic.com",  # commit trailers quoted in docs
}
FAKE_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")


def allowed(value: str) -> bool:
    if value in FAKE:
        return True
    return "@" in value and value.rsplit("@", 1)[1].lower().endswith(FAKE_DOMAINS)


def findings(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if "gitleaks:allow" in line:
                continue
            for kind, pattern in PATTERNS.items():
                for match in pattern.finditer(line):
                    if not allowed(match.group(0)):
                        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                        found.append(f"{shown}:{line_no}: {kind}")
    return found


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / name for name in out.decode().split("\0") if name]


def test_no_committed_file_holds_a_credential_or_a_real_email():
    found = findings(tracked_files())
    assert not found, (
        "Possible secrets or personal data in committed files:\n  " + "\n  ".join(found)
        + "\nRedact captures with `aistudio dev redact`. A value that is really fake goes in "
          "FAKE in tests/unit/test_no_secrets.py, or its line ends with `gitleaks:allow (why)`.")


@pytest.mark.parametrize("planted, kind", [
    ("token = 'ya29.a0AehfTOmIgRpOkGIGlh4Gxpk7YThSG3tlLC4oRQCBbbcba'", "Google OAuth access token"),
    ("key=AIzaSyA1234567890abcdefghijklmnopqrstuv", "Google API key"),
    ('{"name": "SAPISID", "value": "AbCdEfGhIjKlMnOpQrStUvWxYz012/34"}', "Google session cookie"),
    ("sb-sb-auth-token.0=base64-eyJhY2Nlc3NfdG9rZW4iOiJleUpoYkdj", "Supabase session cookie"),
    ("signed in as real.person@gmail.com", "email address"),
])
def test_each_pattern_catches_a_planted_value(tmp_path, planted, kind):
    leak = tmp_path / "capture.json"
    leak.write_text(planted, encoding="utf-8")
    assert [f.rsplit(": ", 1)[1] for f in findings([leak])] == [kind]


def test_fakes_and_marked_lines_pass(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("a = 'me@example.com'\nb = 'jane.doe@gmail.com'\n"
                  "c = 'ya29.a0AehfTOmIgRpOkGIGlh4Gxpk7YThSG3tlLC'  # gitleaks:allow (fake)\n",
                  encoding="utf-8")
    assert findings([ok]) == []
