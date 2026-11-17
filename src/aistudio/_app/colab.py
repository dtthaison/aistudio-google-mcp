"""``aistudio colab``: run Google's Colab CLI (the google-colab-cli library) as one named account.

The Colab CLI signs in with OAuth and keeps one token and one session list. To keep several
accounts apart, each account gets its own keychain entry (``google-colab-cli`` /
``oauth_token:<account>``) and its own session list
(``~/.config/colab-cli/sessions-<account>.json``), the same layout as the ``colab-acc``
wrapper, so a sign-in made with either tool works for both.

google-colab-cli needs Python 3.12 or newer; on older Pythons it is not installed and the
command says so.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aistudio.exceptions import NotFoundError, ValidationError

_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Subcommands that start a Colab VM. A VM uses compute units while it runs (a GPU or TPU fast),
# so these need --allow-spend, the same rule as every other paid command here.
SPENDING_SUBCOMMANDS = frozenset({"new", "run"})

# Global options of the Colab CLI that take a value as the next word.
_VALUE_OPTIONS = frozenset({"-c", "--client-oauth-config", "--config", "--auth"})


def colab_account(profile_name: str, override: str | None = None) -> str:
    """The Colab account name: --account if given, else the aistudio profile's name."""
    account = override or profile_name
    if not _ACCOUNT.match(account):
        raise ValidationError(
            f"invalid Colab account name {account!r}",
            hint="use letters, digits, '.', '_' or '-' (at most 64 characters)",
        )
    return account


def config_dir() -> Path:
    return Path.home() / ".config" / "colab-cli"


def subcommand(args: Sequence[str]) -> str | None:
    """The Colab subcommand in ARGS: the first word that is not an option or an option's value."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return None


def needs_spend_ack(args: Sequence[str]) -> bool:
    if any(a in ("-h", "--help") for a in args):
        return False
    return subcommand(args) in SPENDING_SUBCOMMANDS


def _load_colab() -> tuple[Any, Any]:
    try:
        import colab_cli.auth as auth
        import colab_cli.cli as colab_cli
    except ImportError as exc:
        hint = ("google-colab-cli needs Python 3.12 or newer; reinstall aistudio with one"
                if sys.version_info < (3, 12) else "reinstall aistudio to get google-colab-cli")
        raise NotFoundError("the Colab CLI (google-colab-cli) is not installed",
                            hint=hint) from exc
    return auth, colab_cli


def run_colab(account: str, args: Sequence[str]) -> int:
    """Run the Colab CLI with ARGS as ACCOUNT and return its exit code."""
    auth, colab_cli = _load_colab()
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    saved = auth.KEYRING_USERNAME, auth.LEGACY_TOKEN_PATH
    auth.KEYRING_USERNAME = f"oauth_token:{account}"
    # The library would also read (and then delete) its single legacy token file; point that
    # at a per-account name nothing writes, so one account never picks up another's token.
    auth.LEGACY_TOKEN_PATH = str(folder / f"token-{account}.json.unused")
    argv = ["--config", str(folder / f"sessions-{account}.json"), *args]
    # The library sets the root logger to DEBUG and adds its own handlers (a log file that
    # redacts secrets; stderr only with --logtostderr). aistudio's own root handlers would then
    # print every DEBUG record, request headers with the bearer token among them, so take them
    # off for the run and put everything back afterwards.
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    for handler in saved_handlers:
        root.removeHandler(handler)
    try:
        colab_cli.app(args=argv, prog_name="colab")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    finally:
        auth.KEYRING_USERNAME, auth.LEGACY_TOKEN_PATH = saved
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
    return 0
