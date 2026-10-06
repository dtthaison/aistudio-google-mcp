"""Per-request Google auth headers for the MakerSuite RPC endpoint (spec §4.3)."""

from __future__ import annotations

import hashlib
import time

from aistudio._auth.session import Session, format_cookie_header
from aistudio.exceptions import AuthError

ORIGIN = "https://aistudio.google.com"
API_HOST = "alkalimakersuite-pa.clients6.google.com"

_HASH_COOKIES = (
    ("SAPISID", "SAPISIDHASH"),
    ("__Secure-1PAPISID", "SAPISID1PHASH"),
    ("__Secure-3PAPISID", "SAPISID3PHASH"),
)


def sapisid_hash(secret: str, ts: int, origin: str = ORIGIN) -> str:
    digest = hashlib.sha1(f"{ts} {secret} {origin}".encode()).hexdigest()
    return f"{ts}_{digest}"


def authorization_header(cookies: dict[str, str], ts: int, origin: str = ORIGIN) -> str:
    if not cookies.get("SAPISID"):
        raise AuthError("SAPISID cookie missing; cannot sign requests", hint='run "aistudio login"')
    return " ".join(
        f"{scheme} {sapisid_hash(cookies[name], ts, origin)}"
        for name, scheme in _HASH_COOKIES
        if cookies.get(name)
    )


def build_headers(
    session: Session, *, host: str = API_HOST, ts: int | None = None
) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    cookies = session.cookies_for(host)
    headers = {
        "Authorization": authorization_header(cookies, ts),
        "Content-Type": "application/json+protobuf",
        "Cookie": format_cookie_header(cookies),
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
        "X-Goog-AuthUser": str(session.authuser),
        "X-User-Agent": "grpc-web-javascript/0.1",
    }
    if session.api_key:
        headers["X-Goog-Api-Key"] = session.api_key
    return headers
