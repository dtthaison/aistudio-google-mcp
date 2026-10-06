"""Global CLI options shared by every command via ``ctx.obj``."""

from __future__ import annotations

from dataclasses import dataclass

from aistudio.paths import Profile, get_profile, resolve_profile_name


@dataclass
class CliState:
    profile_name: str | None = None
    backend: str = "auto"
    verbose: int = 0
    quiet: bool = False

    def profile(self) -> Profile:
        return get_profile(resolve_profile_name(self.profile_name))
