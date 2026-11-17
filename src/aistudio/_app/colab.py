"""``aistudio colab``: run Google's Colab CLI (google-colab-cli) as one named Google account.

The Colab CLI signs in with OAuth and keeps one token and one session list. To keep several
accounts apart, each account gets its own keychain entry (``google-colab-cli`` /
``oauth_token:<account>``) and its own session list
(``~/.config/colab-cli/sessions-<account>.json``), the same layout as the ``colab-acc``
wrapper, so a sign-in made with either tool works for both.

The Colab CLI runs in its own Python (it may live in another virtualenv), so this module only
builds the command line; the CLI layer runs it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path

from aistudio.exceptions import NotFoundError, ValidationError

PYTHON_ENV = "AISTUDIO_COLAB_PYTHON"
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Subcommands that start a Colab VM. A VM uses compute units while it runs (a GPU or TPU fast),
# so these need --allow-spend, the same rule as every other paid command here.
SPENDING_SUBCOMMANDS = frozenset({"new", "run"})

# Runs inside the Colab CLI's Python: point its token and session list at one account, then
# hand over to its own entry point. argv: -c ACCOUNT CONFIG_DIR [colab args...]
BOOTSTRAP = """\
import os, sys
account, config_dir = sys.argv[1], sys.argv[2]
del sys.argv[1:3]
import colab_cli.auth as auth
auth.KEYRING_USERNAME = "oauth_token:" + account
auth.LEGACY_TOKEN_PATH = os.path.join(config_dir, "token-" + account + ".json.unused")
os.makedirs(config_dir, exist_ok=True)
sys.argv[1:1] = ["--config", os.path.join(config_dir, "sessions-" + account + ".json")]
sys.argv[0] = "colab"
from colab_cli.cli import main
sys.exit(main())
"""


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


def subcommand(args: list[str] | tuple[str, ...]) -> str | None:
    """The Colab subcommand in ARGS: the first word that is not an option. Every global option
    of the Colab CLI that takes a value is written ``--opt=value`` or comes before it as a pair,
    so skip those pairs too."""
    takes_value = {"-c", "--client-oauth-config", "--config", "--auth"}
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in takes_value:
            skip = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def needs_spend_ack(args: list[str] | tuple[str, ...]) -> bool:
    if any(a in ("-h", "--help") for a in args):
        return False
    return subcommand(args) in SPENDING_SUBCOMMANDS


def _shebang_python(script: Path) -> str | None:
    try:
        with script.open("rb") as fh:
            first = fh.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].split()
    if not parts:
        return None
    if Path(parts[0]).name == "env":  # "#!/usr/bin/env python3": not a fixed interpreter
        return shutil.which(parts[-1]) if len(parts) > 1 else None
    return parts[0]


def find_colab_python() -> str:
    """The Python that has ``colab_cli`` installed: $AISTUDIO_COLAB_PYTHON, else this one, else
    the interpreter of the ``colab`` command on PATH or in ~/.local/bin (pipx and uv put it
    there, and that folder is not always on PATH)."""
    explicit = os.environ.get(PYTHON_ENV)
    if explicit:
        return explicit
    if importlib.util.find_spec("colab_cli") is not None:
        return sys.executable
    local = Path.home() / ".local" / "bin" / "colab"
    colab = shutil.which("colab") or (str(local) if local.exists() else None)
    if colab:
        python = _shebang_python(Path(colab).resolve())
        if python:
            return python
    raise NotFoundError(
        "the Colab CLI (google-colab-cli) is not installed",
        hint=("install it (https://github.com/googlecolab/google-colab-cli) so `colab` is on "
              f"PATH, or set ${PYTHON_ENV} to the Python that has it"),
    )


def build_command(python: str, account: str, args: list[str] | tuple[str, ...]) -> list[str]:
    return [python, "-c", BOOTSTRAP, account, str(config_dir()), *args]
