"""``aistudio colab``: Google's Colab CLI, one Google account per aistudio profile."""

from __future__ import annotations

import subprocess  # nosec B404: runs the user's own Colab CLI with an argument list

import click

from aistudio._app.colab import build_command, colab_account, find_colab_python, needs_spend_ack
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.state import CliState
from aistudio.exceptions import ValidationError
from aistudio.paths import resolve_profile_name


@click.command(
    "colab",
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False,
                      "help_option_names": []},
)
@click.option("--account", help="Colab account name (default: the aistudio profile's name).")
@click.option("--allow-spend", is_flag=True,
              help="Allow `new` and `run`, which start a Colab VM that uses compute units.")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.pass_obj
@handle_errors
def colab(state: CliState, account: str | None, allow_spend: bool, args: tuple[str, ...]) -> None:
    """Run the Colab CLI (google-colab-cli) as this profile's Google account.

    Everything after `colab` goes to the Colab CLI, e.g. `aistudio -p work colab usage` or
    `aistudio colab --allow-spend new --gpu T4`. Each account has its own sign-in and session
    list. `aistudio colab help` shows the Colab CLI's commands.
    """
    name = colab_account(resolve_profile_name(state.profile_name), account)
    if not args:
        args = ("--help",)
    if needs_spend_ack(args) and not allow_spend:
        raise ValidationError(
            "this starts a Colab VM, which uses compute units while it runs",
            hint="add --allow-spend before the Colab command, e.g. "
                 "`aistudio colab --allow-spend new`",
        )
    command = build_command(find_colab_python(), name, args)
    returncode = subprocess.call(command)  # noqa: S603 (argument list, the user's own Colab CLI)
    if returncode:
        raise click.exceptions.Exit(returncode)
