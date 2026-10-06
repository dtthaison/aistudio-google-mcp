"""``aistudio profile``: named profiles, one Google account each."""

from __future__ import annotations

from typing import Any

import click

from aistudio._app.profiles import profile_create, profile_delete, profile_list, profile_switch
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import confirm_or_fail, emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.paths import resolve_profile_name


@click.group()
def profile() -> None:
    """Manage named profiles."""


def _render_list(data: dict[str, Any]) -> None:
    if not data["profiles"]:
        stdout.print("No profiles yet. Run: aistudio login")
        return
    for p in data["profiles"]:
        marker = "*" if p["current"] else " "
        status = "logged in" if p["logged_in"] else "not logged in"
        stdout.print(f"{marker} {p['name']}  ({status})")


@profile.command("list")
@json_option
@click.pass_obj
@handle_errors
def list_cmd(state: CliState, json_output: bool) -> None:
    """List profiles; * marks the active one."""
    data = profile_list(resolve_profile_name(state.profile_name))
    emit(data, json_output=json_output, render=_render_list)


@profile.command("create")
@click.argument("name")
@json_option
@handle_errors
def create_cmd(name: str, json_output: bool) -> None:
    """Create an empty profile."""
    emit(profile_create(name), json_output=json_output,
         render=lambda d: stdout.print(f"Created profile {d['profile']}"))


@profile.command("switch")
@click.argument("name")
@json_option
@handle_errors
def switch_cmd(name: str, json_output: bool) -> None:
    """Make NAME the default profile."""
    emit(profile_switch(name), json_output=json_output,
         render=lambda d: stdout.print(f"Default profile is now {d['profile']}"))


@profile.command("delete")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@json_option
@handle_errors
def delete_cmd(name: str, yes: bool, json_output: bool) -> None:
    """Delete a profile and its credentials."""
    confirm_or_fail(f"Delete profile {name!r} and its credentials?", yes=yes,
                    json_output=json_output)
    emit(profile_delete(name), json_output=json_output,
         render=lambda d: stdout.print(f"Deleted profile {d['profile']}"))
