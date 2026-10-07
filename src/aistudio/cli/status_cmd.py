"""``aistudio status``: AI Studio and Gemini API incidents."""

from __future__ import annotations

import asyncio
from typing import Any

import click

from aistudio._app.status import list_incidents
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


def _render(data: dict[str, Any]) -> None:
    if not data["active"]:
        stdout.print("No active incidents.", markup=False)
    else:
        stdout.print(f"{data['active']} active incident(s).", markup=False)
    for incident in data["incidents"]:
        products = ", ".join(incident["products"])
        stdout.print(f"\n{incident['updated'] or '-'}  {incident['state'].upper()}  "
                     f"{incident['title'] or incident['id']}  ({products})", markup=False)
        if incident["message"]:
            stdout.print(f"  {incident['message']}", markup=False)


@click.command()
@click.option("--active", "active_only", is_flag=True, help="Only incidents not yet resolved.")
@click.option("--limit", type=click.IntRange(1, 200), default=10, show_default=True)
@click.option("--fail-if-active", is_flag=True,
              help="Exit with code 3 (after printing) when any incident is active.")
@json_option
@click.pass_obj
@handle_errors
def status(state: CliState, active_only: bool, limit: int, fail_if_active: bool,
           json_output: bool) -> None:
    """AI Studio and Gemini API incidents, newest first.

    State and product names are inferred from AI Studio's numeric codes (JSON keeps the raw
    "state_code"). Unknown codes show as state:N or product:N, and unknown states count as
    active.
    """
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await list_incidents(client, active_only=active_only, limit=limit)

    data = asyncio.run(run())
    emit(data, json_output=json_output, render=_render)
    if fail_if_active and data["active"]:
        raise SystemExit(3)
