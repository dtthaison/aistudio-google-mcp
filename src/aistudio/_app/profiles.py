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


def _forget_login(profile: Profile) -> list[str]:
    """What ``auth logout`` removes of the login (not the Chrome folder): the saved cookies and
    API key belong to the old browser's account, so they must not be mixed with a new one's."""
    removed = []
    for path in (profile.storage_state, profile.session):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    return removed


def profile_browser(profile: Profile, spec: str | None = None, *, reset: bool = False
                    ) -> dict[str, Any]:
    """Show, set or reset (back to Chrome) the browser the profile drives. Changing it forgets
    the saved login (storage_state.json, session.json), since another browser may be another
    Google account; run ``aistudio login`` after."""
    if spec and reset:
        raise ValidationError("give a browser or --reset, not both")
    try:
        before: str | None = str(load_spec(profile))
    except ValidationError:
        if not (spec or reset):
            raise
        before = None  # a broken browser.json must not block replacing or resetting it
    if reset:
        removed = reset_spec(profile)
        after = str(load_spec(profile))
        data: dict[str, Any] = {"profile": profile.name, "browser": after, "reset": removed}
    elif spec:
        parsed = parse_spec(spec)
        after = str(parsed)
        save_spec(profile, parsed)
        data = {"profile": profile.name, "browser": after, "saved": True}
    else:
        return {"profile": profile.name, "browser": before}
    cleared = _forget_login(profile) if after != before else []
    if cleared:
        data["cleared"] = cleared
        data["hint"] = f'sign in with the new browser: aistudio -p {profile.name} login'
    return data


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
