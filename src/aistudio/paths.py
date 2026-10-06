"""Locations of the AI Studio home directory and per-profile files (spec §4.1)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import ValidationError

DEFAULT_PROFILE = "default"
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def home_dir() -> Path:
    return Path(os.environ.get("AISTUDIO_HOME") or Path.home() / ".aistudio")


def config_path() -> Path:
    return home_dir() / "config.json"


def validate_profile_name(name: str) -> str:
    if not _PROFILE_NAME.match(name) or ".." in name:
        raise ValidationError(
            f"invalid profile name {name!r}", hint="use letters, digits, '.', '_' or '-'"
        )
    return name


def load_config() -> dict[str, Any]:
    path = config_path()
    return read_json(path) if path.exists() else {}


def set_default_profile(name: str) -> None:
    config = load_config()
    config["default_profile"] = validate_profile_name(name)
    write_private_json(config_path(), config)


def clear_default_profile(name: str) -> None:
    config = load_config()
    if config.get("default_profile") == name:
        del config["default_profile"]
        write_private_json(config_path(), config)


def resolve_profile_name(cli_value: str | None = None) -> str:
    name = (
        cli_value
        or os.environ.get("AISTUDIO_PROFILE")
        or load_config().get("default_profile")
        or DEFAULT_PROFILE
    )
    return validate_profile_name(name)


@dataclass(frozen=True)
class Profile:
    name: str
    root: Path

    @property
    def storage_state(self) -> Path:
        return self.root / "storage_state.json"

    @property
    def session(self) -> Path:
        return self.root / "session.json"

    @property
    def context(self) -> Path:
        return self.root / "context.json"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs.json"

    @property
    def models(self) -> Path:
        return self.root / "models.json"

    @property
    def browser_json(self) -> Path:
        return self.root / "browser.json"

    @property
    def chrome_dir(self) -> Path:
        return self.root / "chrome"

    def ensure(self) -> Profile:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self


def get_profile(name: str) -> Profile:
    return Profile(name=validate_profile_name(name), root=home_dir() / "profiles" / name)


def list_profiles() -> list[str]:
    base = home_dir() / "profiles"
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and _PROFILE_NAME.match(p.name))
