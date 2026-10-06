"""Global CLI options shared by every command via ``ctx.obj``."""

from __future__ import annotations

from dataclasses import dataclass

from aistudio.exceptions import ValidationError
from aistudio.paths import Profile, get_profile, resolve_profile_name


@dataclass
class CliState:
    profile_name: str | None = None
    backend: str = "auto"
    verbose: int = 0
    quiet: bool = False

    def profile(self) -> Profile:
        return get_profile(resolve_profile_name(self.profile_name))

    def require_http(self) -> None:
        """These commands run over plain HTTP; the browser backend is not available yet."""
        if self.backend == "browser":
            raise ValidationError(
                "--backend browser is not available for this command yet",
                hint="drop --backend or use --backend http",
            )

    def require_browser(self) -> None:
        """Generation is page-driven (spec §5.4); plain HTTP cannot do it."""
        if self.backend == "http":
            raise ValidationError(
                "this command needs the browser; --backend http cannot generate",
                hint="drop --backend or use --backend browser",
            )
