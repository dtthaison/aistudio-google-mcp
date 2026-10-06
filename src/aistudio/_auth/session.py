"""Runtime credentials for signing requests, built from a profile's stored state."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from aistudio._auth.storage import account_info, load_api_key, load_state
from aistudio.paths import Profile


def _domain_matches(host: str, domain: str) -> bool:
    d = domain.lstrip(".").lower()
    host = host.lower()
    return host == d or host.endswith("." + d)


def format_cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


@dataclass
class Session:
    cookies: list[dict[str, Any]]
    api_key: str | None = None
    authuser: int = 0

    @classmethod
    def load(cls, profile: Profile) -> Session:
        state = load_state(profile)
        return cls(
            cookies=list(state["cookies"]),
            api_key=load_api_key(profile),
            authuser=account_info(state).authuser,
        )

    def cookies_for(self, host: str, now: float | None = None) -> dict[str, str]:
        now = time.time() if now is None else now
        jar: dict[str, str] = {}
        # Shorter domains first so the most specific matching domain wins on name clashes.
        for c in sorted(self.cookies, key=lambda c: len(c.get("domain", "").lstrip("."))):
            expires = c.get("expires", -1)
            if expires not in (-1, None) and expires < now:
                continue
            if _domain_matches(host, c.get("domain", "")):
                jar[c["name"]] = c["value"]
        return jar

    def cookie_header(self, host: str, now: float | None = None) -> str:
        return format_cookie_header(self.cookies_for(host, now))
