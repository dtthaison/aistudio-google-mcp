"""``aistudio colab``: Google's Colab CLI, one Google account per aistudio profile."""

from __future__ import annotations

from typing import Any

import click

from aistudio._app.colab import colab_account, list_accounts, needs_spend_ack, run_colab
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, stdout
from aistudio.cli.state import CliState
from aistudio.exceptions import ValidationError
from aistudio.paths import resolve_profile_name


def _render_accounts(data: dict[str, Any]) -> None:
    if not data["accounts"]:
        stdout.print("No Colab accounts yet. Run: aistudio -p NAME colab whoami")
        return
    for a in data["accounts"]:
        marker = "*" if a["current"] else " "
        signed = {True: "signed in", False: "not signed in", None: "sign-in unknown"}[
            a["signed_in"]]
        kind = "profile" if a["profile"] else "--account only"
        stdout.print(f"{marker} {a['name']}  ({signed}, {a['sessions']} sessions, {kind})")


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
    list. `aistudio colab accounts [--json]` lists the accounts; `aistudio colab help` shows the
    Colab CLI's commands.
    """
    profile_name = resolve_profile_name(state.profile_name)
    if args and args[0] == "accounts":
        extra = [a for a in args[1:] if a != "--json"]
        if extra:
            raise ValidationError(f"unexpected argument {extra[0]!r}",
                                  hint="usage: aistudio colab accounts [--json]")
        emit(list_accounts(colab_account(profile_name, account)),
             json_output="--json" in args, render=_render_accounts)
        return
    name = colab_account(profile_name, account)
    if not args:
        args = ("--help",)
    if needs_spend_ack(args) and not allow_spend:
        raise ValidationError(
            "this starts a Colab VM, which uses compute units while it runs",
            hint="add --allow-spend before the Colab command, e.g. "
                 "`aistudio colab --allow-spend new`",
        )
    returncode = run_colab(name, args)
    if returncode:
        raise click.exceptions.Exit(returncode)
