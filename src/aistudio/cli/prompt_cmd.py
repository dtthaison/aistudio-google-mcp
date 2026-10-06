"""``aistudio prompt``: the AI Studio prompt library (spec §6.3)."""

from __future__ import annotations

import asyncio
from typing import Any

import click
from rich.table import Table

from aistudio._app.prompts import list_prompts
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import cells, emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


@click.group()
def prompt() -> None:
    """Your saved prompts and chats."""


def _render_list(data: dict[str, Any]) -> None:
    table = Table("id", "type", "modified", "title")
    for p in data["prompts"]:
        table.add_row(*cells(p["id"], p["type"], p["modified"] or "", p["title"] or ""))
    stdout.print(table)


@prompt.command("list")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True,
              help="Page size requested from AI Studio.")
@click.option("--type", "prompt_type", type=click.Choice(["chat", "video"]),
              help="Only chat prompts or only Veo (video) prompts.")
@json_option
@click.pass_obj
@handle_errors
def list_cmd(state: CliState, limit: int, prompt_type: str | None, json_output: bool) -> None:
    """List saved prompts, newest first."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await list_prompts(client, limit=limit, prompt_type=prompt_type)

    emit(asyncio.run(run()), json_output=json_output, render=_render_list)
