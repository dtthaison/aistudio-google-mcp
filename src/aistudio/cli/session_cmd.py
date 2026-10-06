"""Session commands: ``auth check`` and ``auth logout`` (login is added in Task 8)."""

from __future__ import annotations

from typing import Any

import click

from aistudio._app.auth import auth_status
from aistudio._auth.storage import delete_credentials
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import confirm_or_fail, emit, json_option, stdout
from aistudio.cli.state import CliState


@click.group()
def auth() -> None:
    """Authentication management."""


def _render_check(data: dict[str, Any]) -> None:
    status = "[green]ok[/green]" if data["status"] == "ok" else "[red]error[/red]"
    stdout.print(f"Profile: {data['profile']}")
    stdout.print(f"Status:  {status}")
    for name, value in data["checks"].items():
        stdout.print(f"  {name}: {'yes' if value else 'no'}")
    for warning in data["warnings"]:
        stdout.print(f"[yellow]warning:[/yellow] {warning}")
    if data.get("hint"):
        stdout.print(f"Hint: {data['hint']}")


@auth.command("check")
@json_option
@click.pass_obj
@handle_errors
def check(state: CliState, json_output: bool) -> None:
    """Check the stored credentials of the active profile (no network)."""
    data = auth_status(state.profile())
    emit(data, json_output=json_output, render=_render_check)
    if data["status"] != "ok":
        raise SystemExit(1)


@auth.command("logout")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@json_option
@click.pass_obj
@handle_errors
def logout(state: CliState, yes: bool, json_output: bool) -> None:
    """Delete the stored credentials and browser session of the active profile."""
    profile = state.profile()
    confirm_or_fail(
        f"Delete stored credentials for profile {profile.name!r}?", yes=yes, json_output=json_output
    )
    data = {"profile": profile.name, "removed": delete_credentials(profile)}
    emit(data, json_output=json_output,
         render=lambda d: stdout.print(f"Removed: {', '.join(d['removed']) or 'nothing'}"))
