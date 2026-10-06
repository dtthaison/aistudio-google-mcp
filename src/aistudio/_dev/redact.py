"""In-memory redaction of captured exchanges before anything touches disk (spec §8.1)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any

from aistudio._dev.fixtures import rpc_method_name

SENSITIVE_HEADERS = frozenset(
    {"cookie", "set-cookie", "authorization", "x-goog-api-key", "x-goog-authuser"}
)
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
AUTHUSER_RE = re.compile(r"(authuser=)\d+")
DRIVE_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{24,43}(?![A-Za-z0-9_-])")
REDACTED_KEY = "AIza_REDACTED"
REDACTED_EMAIL = "user@example.com"


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
    """One instance per capture session so the same Drive ID maps to the same fake."""

    ids: dict[str, str] = field(default_factory=dict)

    def _fake_id(self, match: re.Match[str]) -> str:
        real = match.group(0)
        if real not in self.ids:
            self.ids[real] = f"prompt_{len(self.ids) + 1}"
        return self.ids[real]

    def text(self, value: str) -> str:
        value = API_KEY_RE.sub(REDACTED_KEY, value)
        value = EMAIL_RE.sub(REDACTED_EMAIL, value)
        value = AUTHUSER_RE.sub(r"\g<1>0", value)
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
            return {"body": base64.b64encode(raw).decode("ascii"), "body_encoding": "base64"}

    def exchange(self, ex: RawExchange) -> dict[str, Any]:
        return {
            "method": rpc_method_name(ex.url),
            "url": self.text(ex.url),
            "http_method": ex.http_method,
            "request": {"headers": self.headers(ex.request_headers), **self.body(ex.request_body)},
            "response": {
                "status": ex.status,
                "headers": self.headers(ex.response_headers),
                **self.body(ex.response_body),
            },
        }
