"""Profile management use cases."""

from __future__ import annotations

import shutil
from typing import Any

from aistudio._browser.provider import load_spec, parse_spec, reset_spec, save_spec
from aistudio.exceptions import NotFoundError, ValidationError
from aistudio.paths import (
    Profile,
    clear_default_profile,
    get_profile,
    list_profiles,
    set_default_profile,
)

_LIST_HINT = 'see "aistudio profile list"'


def profile_list(current: str) -> dict[str, Any]:
    profiles = [
        {"name": name, "current": name == current,
         "logged_in": get_profile(name).storage_state.exists()}
        for name in list_profiles()
    ]
    return {"profiles": profiles, "count": len(profiles), "current": current}


def profile_browser(profile: Profile, spec: str | None = None, *, reset: bool = False
                    ) -> dict[str, Any]:
    """Show, set or reset (back to Chrome) the browser the profile drives."""
    if spec and reset:
        raise ValidationError("give a browser or --reset, not both")
    if reset:
        removed = reset_spec(profile)
        return {"profile": profile.name, "browser": str(load_spec(profile)), "reset": removed}
    if spec:
        parsed = parse_spec(spec)
        save_spec(profile, parsed)
        return {"profile": profile.name, "browser": str(parsed), "saved": True}
    return {"profile": profile.name, "browser": str(load_spec(profile))}


def profile_create(name: str) -> dict[str, Any]:
    profile = get_profile(name)
    if profile.root.exists():
        raise ValidationError(f"profile {name!r} already exists")
    profile.ensure()
    return {"profile": name, "created": True}


def profile_switch(name: str) -> dict[str, Any]:
    if name not in list_profiles():
        raise NotFoundError(f"no profile named {name!r}", hint=_LIST_HINT)
    set_default_profile(name)
    return {"profile": name, "default": True}


def profile_delete(name: str) -> dict[str, Any]:
    profile = get_profile(name)
    if not profile.root.is_dir():
        raise NotFoundError(f"no profile named {name!r}", hint=_LIST_HINT)
    shutil.rmtree(profile.root)
    clear_default_profile(name)
    return {"profile": name, "deleted": True}
