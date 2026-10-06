"""Authentication use cases shared by the CLI and MCP layers."""

from __future__ import annotations

from typing import Any

from aistudio._auth.storage import (
    REQUIRED_COOKIES,
    account_info,
    load_api_key,
    load_state,
    missing_cookies,
)
from aistudio.exceptions import AuthError
from aistudio.paths import Profile

_LOGIN_HINT = 'run "aistudio login"'


def auth_status(profile: Profile) -> dict[str, Any]:
    """Report whether the profile holds usable credentials (no network)."""
    api_key_present = load_api_key(profile) is not None
    try:
        state = load_state(profile)
    except AuthError as exc:
        return {
            "status": "error",
            "profile": profile.name,
            "account": None,
            "checks": {"cookies_present": False, "api_key_present": api_key_present},
            "missing_cookies": list(REQUIRED_COOKIES),
            "warnings": [],
            "message": exc.message,
            "hint": exc.hint,
        }
    missing = missing_cookies(state)
    account = account_info(state)
    result: dict[str, Any] = {
        "status": "error" if missing else "ok",
        "profile": profile.name,
        "account": {"email": account.email, "authuser": account.authuser},
        "checks": {"cookies_present": not missing, "api_key_present": api_key_present},
        "missing_cookies": missing,
        "warnings": [] if api_key_present else [f"no web API key captured; re-{_LOGIN_HINT}"],
    }
    if missing:
        result["hint"] = _LOGIN_HINT
    return result
