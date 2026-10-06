"""Persisted Google session: Playwright ``storage_state`` plus account metadata (spec §4)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import AuthError
from aistudio.paths import Profile

REQUIRED_COOKIES = ("SID", "SAPISID")
_ALLOWED_DOMAIN = re.compile(r"(^|\.)(google\.com|googleapis\.com)$")
_LOGIN_HINT = 'run "aistudio login"'


@dataclass(frozen=True)
class AccountInfo:
    email: str | None = None
    authuser: int = 0


def _valid_cookie(cookie: Any) -> bool:
    return (
        isinstance(cookie, dict)
        and all(isinstance(cookie.get(key), str) for key in ("name", "value", "domain"))
        and isinstance(cookie.get("expires", -1), (int, float))
    )


def _valid_account(meta: Any) -> bool:
    if meta is None:
        return True
    if not isinstance(meta, dict):
        return False
    authuser = meta.get("authuser")
    email = meta.get("email")
    authuser_ok = authuser is None or (isinstance(authuser, int) and not isinstance(authuser, bool))
    return authuser_ok and (email is None or isinstance(email, str))


def _domain_allowed(domain: str) -> bool:
    return bool(_ALLOWED_DOMAIN.search(domain.lstrip(".").lower()))


def filter_state(state: dict[str, Any]) -> dict[str, Any]:
    cookies = [c for c in state.get("cookies", []) if _domain_allowed(c.get("domain", ""))]
    origins = [
        o for o in state.get("origins", [])
        if _domain_allowed(urlparse(o.get("origin", "")).hostname or "")
    ]
    return {"cookies": cookies, "origins": origins}


def save_state(profile: Profile, state: dict[str, Any], account: AccountInfo) -> None:
    data = filter_state(state)
    data["aistudio"] = {"email": account.email, "authuser": account.authuser}
    profile.ensure()
    write_private_json(profile.storage_state, data)


def load_state(profile: Profile) -> dict[str, Any]:
    path = profile.storage_state
    if not path.exists():
        raise AuthError(f"profile {profile.name!r} is not logged in", hint=_LOGIN_HINT)
    try:
        data = read_json(path)
    except (ValueError, OSError) as exc:
        raise AuthError(f"cannot read {path}: {exc}", hint=_LOGIN_HINT) from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("cookies"), list)
        or not all(_valid_cookie(c) for c in data["cookies"])
        or not _valid_account(data.get("aistudio"))
    ):
        raise AuthError(f"{path} is not a valid storage state", hint=_LOGIN_HINT)
    return data


def account_info(state: dict[str, Any]) -> AccountInfo:
    meta = state.get("aistudio") or {}
    return AccountInfo(email=meta.get("email"), authuser=int(meta.get("authuser") or 0))


def missing_cookies(state: dict[str, Any]) -> list[str]:
    names = {
        c.get("name") for c in state.get("cookies", []) if _domain_allowed(c.get("domain", ""))
    }
    return [name for name in REQUIRED_COOKIES if name not in names]


def save_session_info(profile: Profile, api_key: str | None) -> None:
    profile.ensure()
    write_private_json(
        profile.session,
        {"api_key": api_key, "scraped_at": datetime.now(timezone.utc).isoformat()},
    )


def load_api_key(profile: Profile) -> str | None:
    if not profile.session.exists():
        return None
    try:
        data = read_json(profile.session)
    except (ValueError, OSError):
        return None
    return data.get("api_key") if isinstance(data, dict) else None


def delete_credentials(profile: Profile) -> list[str]:
    removed: list[str] = []
    for path in (profile.storage_state, profile.session):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    if profile.chrome_dir.exists():
        shutil.rmtree(profile.chrome_dir)
        removed.append(f"{profile.chrome_dir.name}/")
    return removed
