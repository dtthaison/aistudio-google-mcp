"""In-memory redaction of captured exchanges before anything touches disk (spec §8.1)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aistudio._dev.fixtures import dump_fixture, iter_fixtures, load_fixture, rpc_method_name

SENSITIVE_HEADERS = frozenset(
    {
        "cookie", "set-cookie", "authorization", "proxy-authorization",
        "x-goog-api-key", "x-goog-authuser", "x-client-data",
        # Resumable-upload session URLs/ids are bearer capabilities.
        "x-goog-upload-url", "x-goog-upload-control-url", "x-guploader-uploadid",
    }
)
# Credential patterns carry boundaries so they never fire inside base64 media.
API_KEY_RE = re.compile(r"(?<![A-Za-z0-9+/_\-])AIza[0-9A-Za-z_\-]{35}(?![A-Za-z0-9_\-])")
# Batch bodies (e.g. People API multipart) embed per-request HTTP headers.
EMBEDDED_HEADER_RE = re.compile(
    r"(?im)^(authorization|proxy-authorization|cookie|x-goog-api-key)[ \t]*:[^\r\n]*"
)
SAPISIDHASH_RE = re.compile(r"(SAPISID[0-9A-Z]*HASH)\s+[^\s\"\\]+")
PHOTO_URL_RE = re.compile(r"(https?://lh[0-9]\.googleusercontent\.com/a-?/)[A-Za-z0-9_\-=]+")
GAIA_ID_RE = re.compile(r"(?<![0-9])1[0-9]{20}(?![0-9])")
_FAKE_GAIA_PREFIX = "1" + "0" * 12
REDACTED_NAME = "Redacted Name"
# Long standard-base64 runs are media (images, audio). Identifier and personal-data
# patterns skip them: a Drive id or name cannot be distinguished from random bytes there,
# and rewriting them corrupts the payload.
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
ACCESS_TOKEN_RE = re.compile(r"ya29\.[0-9A-Za-z_\-.]+")
REFRESH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/])1//0[0-9A-Za-z_\-]{40,}(?![A-Za-z0-9+/=_\-])"
)
JWT_RE = re.compile(r"eyJ[0-9A-Za-z_\-]+\.[0-9A-Za-z_\-]+\.[0-9A-Za-z_\-]*")
SECRET_PARAM_RE = re.compile(
    r"(?i)(?<![0-9A-Za-z_])(access_token|token|upload_id|sig|signature)=[^&\s\"'\\]+"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+(?:@|%40|\\u0040)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
AUTHUSER_RE = re.compile(r"(authuser=)\d+")
DRIVE_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{24,43}(?![A-Za-z0-9_-])")
REDACTED_KEY = "AIza_REDACTED"
REDACTED_EMAIL = "user@example.com"

# Applied to the whole text, in order; tokens first so later patterns never see partial secrets.
_CREDENTIALS: tuple[tuple[re.Pattern[str], str], ...] = (
    (EMBEDDED_HEADER_RE, r"\1: REDACTED"),
    (SAPISIDHASH_RE, r"\1 REDACTED"),
    (ACCESS_TOKEN_RE, "ya29.REDACTED"),
    (REFRESH_TOKEN_RE, "1//REDACTED"),
    (JWT_RE, "JWT_REDACTED"),
    (SECRET_PARAM_RE, r"\1=REDACTED"),
    (API_KEY_RE, REDACTED_KEY),
)
# Applied outside long base64 runs only.
_PERSONAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (PHOTO_URL_RE, r"\1REDACTED"),
    (EMAIL_RE, REDACTED_EMAIL),
    (AUTHUSER_RE, r"\g<1>0"),
)
_SENSITIVE = (
    EMBEDDED_HEADER_RE, SAPISIDHASH_RE, PHOTO_URL_RE, GAIA_ID_RE, ACCESS_TOKEN_RE,
    REFRESH_TOKEN_RE, JWT_RE, SECRET_PARAM_RE, API_KEY_RE, EMAIL_RE, DRIVE_ID_RE,
)


@dataclass
class RawExchange:
    url: str
    http_method: str
    request_headers: dict[str, str]
    request_body: bytes
    status: int
    response_headers: dict[str, str]
    response_body: bytes


@dataclass
class Redactor:
    """One instance per capture session so the same ID maps to the same fake.

    ``extra`` holds literal strings to remove (e.g. the account's display name).
    """

    extra: tuple[str, ...] = ()
    ids: dict[str, str] = field(default_factory=dict)
    gaia_ids: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._extra = [re.compile(re.escape(s), re.IGNORECASE) for s in self.extra if s.strip()]

    def _fake_gaia(self, match: re.Match[str]) -> str:
        real = match.group(0)
        if real.startswith(_FAKE_GAIA_PREFIX):
            return real
        if real not in self.gaia_ids:
            self.gaia_ids[real] = "1" + str(len(self.gaia_ids) + 1).zfill(20)
        return self.gaia_ids[real]

    def _fake_id(self, match: re.Match[str]) -> str:
        real = match.group(0)
        if real not in self.ids:
            self.ids[real] = f"prompt_{len(self.ids) + 1}"
        return self.ids[real]

    def text(self, value: str) -> str:
        for pattern, replacement in _CREDENTIALS:
            value = pattern.sub(replacement, value)
        parts: list[str] = []
        last = 0
        for run in _BASE64_RUN_RE.finditer(value):
            parts.append(self._personal(value[last:run.start()]))
            parts.append(run.group(0))
            last = run.end()
        parts.append(self._personal(value[last:]))
        return "".join(parts)

    def _personal(self, value: str) -> str:
        for pattern in self._extra:
            value = pattern.sub(REDACTED_NAME, value)
        for pattern, replacement in _PERSONAL:
            value = pattern.sub(replacement, value)
        value = GAIA_ID_RE.sub(self._fake_gaia, value)
        return DRIVE_ID_RE.sub(self._fake_id, value)

    def headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {
            k.lower(): self.text(v)
            for k, v in headers.items()
            if k.lower() not in SENSITIVE_HEADERS
        }

    def body(self, raw: bytes) -> dict[str, str]:
        try:
            return {"body": self.text(raw.decode("utf-8"))}
        except UnicodeDecodeError:
            pass
        # Binary bodies cannot be rewritten safely; withhold any that look sensitive.
        latin = raw.decode("latin-1")
        if any(pattern.search(latin) for pattern in _SENSITIVE):
            return {"body": "", "body_encoding": "withheld"}
        return {"body": base64.b64encode(raw).decode("ascii"), "body_encoding": "base64"}

    def exchange(self, ex: RawExchange) -> dict[str, Any]:
        url = self.text(ex.url)
        return {
            "method": rpc_method_name(url),
            "url": url,
            "http_method": ex.http_method,
            "request": {"headers": self.headers(ex.request_headers), **self.body(ex.request_body)},
            "response": {
                "status": ex.status,
                "headers": self.headers(ex.response_headers),
                **self.body(ex.response_body),
            },
        }

    def record(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Re-redact an existing fixture record, e.g. after adding a pattern."""
        out = dict(rec)
        out["url"] = self.text(rec["url"])
        for side in ("request", "response"):
            part = dict(rec[side])
            part["headers"] = self.headers(part.get("headers", {}))
            encoding = part.get("body_encoding")
            if encoding is None:
                part["body"] = self.text(part.get("body", ""))
            elif encoding == "base64":
                del part["body_encoding"]
                part.update(self.body(base64.b64decode(part["body"])))
            out[side] = part
        return out


def redact_fixtures(root: Path, redactor: Redactor) -> tuple[int, int]:
    """Re-run ``redactor`` over every fixture under ``root``; return (files, changed)."""
    files = changed = 0
    for path in iter_fixtures(root):
        files += 1
        record = load_fixture(path)
        cleaned = redactor.record(record)
        if cleaned != record:
            path.write_text(dump_fixture(cleaned), encoding="utf-8")
            changed += 1
    return files, changed
