"""Session commands: ``login``, ``auth check`` and ``auth logout``."""

from __future__ import annotations

import asyncio
from typing import Any

import click

from aistudio._app.auth import auth_status, http_probe
from aistudio._auth.login import run_login
from aistudio._auth.storage import delete_credentials
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import confirm_or_fail, emit, json_option, stderr, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


@click.group()
def auth() -> None:
    """Authentication management."""


def _render_check(data: dict[str, Any]) -> None:
    status = "[green]ok[/green]" if data["status"] == "ok" else "[red]error[/red]"
    stdout.print(f"Profile: {data['profile']}")
    stdout.print(f"Status:  {status}")
    for name, value in data["checks"].items():
        stdout.print(f"  {name}: {'yes' if value else 'no'}")
    probe = data.get("probe")
    if probe and not probe["ok"]:
        stdout.print(f"  probe failed: {probe['code']}: {probe['message']}", markup=False)
    for warning in data["warnings"]:
        stdout.print(f"[yellow]warning:[/yellow] {warning}")
    if data.get("hint"):
        stdout.print(f"Hint: {data['hint']}")


@auth.command("check")
@click.option("--test", "probe", is_flag=True,
              help="Also make one real ListModels call over plain HTTP.")
@json_option
@click.pass_obj
@handle_errors
def check(state: CliState, probe: bool, json_output: bool) -> None:
    """Check the stored credentials of the active profile."""
    profile = state.profile()
    data = auth_status(profile)
    if probe:
        data["checks"]["http_probe"] = False
        if data["status"] == "ok":

            async def run() -> dict[str, Any]:
                async with AIStudioClient.from_profile(profile) as client:
                    return await http_probe(client)

            data["probe"] = asyncio.run(run())
            data["checks"]["http_probe"] = data["probe"]["ok"]
            if not data["probe"]["ok"]:
                data["status"] = "error"
                if data["probe"]["code"] == "AUTH_ERROR":
                    data["hint"] = 'run "aistudio login"'
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


@click.command()
@click.option("--browser", type=click.Choice(["chrome", "chromium", "msedge"]), default="chrome",
              show_default=True, help="Browser to sign in with.")
@click.option("--fresh", is_flag=True, help="Delete the cached browser profile first.")
@click.option("--timeout", type=click.IntRange(min=1), default=300, show_default=True,
              help="Seconds to wait for sign-in.")
@json_option
@click.pass_obj
@handle_errors
def login(state: CliState, browser: str, fresh: bool, timeout: int, json_output: bool) -> None:
    """Sign in to AI Studio in a browser window and save the session."""
    profile = state.profile()
    if not json_output:
        stderr.print(f"Opening {browser}. Sign in to AI Studio in the window that appears.")
    result = asyncio.run(run_login(profile, browser=browser, fresh=fresh, timeout=timeout))
    data = {
        "profile": result.profile,
        "account": {"email": result.account.email, "authuser": result.account.authuser},
        "api_key_captured": result.api_key_captured,
    }
    emit(data, json_output=json_output,
         render=lambda d: stdout.print(
             f"Logged in (profile {d['profile']}, API key captured: {d['api_key_captured']})"))
