"""``aistudio prompt``: the AI Studio prompt library (spec §6.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from aistudio._app.prompts import (
    attachment_label,
    describe,
    get_prompt,
    list_prompts,
    save_markdown,
)
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import cells, emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


@click.group()
def prompt() -> None:
    """Your saved prompts and chats."""


def _render_list(data: dict[str, Any]) -> None:
    # Ids must never be cut (piped output is laid out at 80 columns): they are copied back
    table = Table()
    table.add_column("id", no_wrap=True,
                     min_width=max((len(p["id"]) for p in data["prompts"]), default=2))
    for name in ("type", "modified", "title"):
        table.add_column(name)
    for p in data["prompts"]:
        table.add_row(*cells(p["id"], p["type"], p["modified"] or "", p["title"] or ""))
    stdout.print(table)


@prompt.command("list")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True,
              help="How many prompts to list; --type looks through up to 1000.")
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


def _render_prompt(data: dict[str, Any], *, thoughts: bool) -> None:
    # Plain echo: prompt text is the user's own and must reach the terminal exactly (no markup,
    # emoji codes, wrapping or cutting)
    click.echo(f"{data['title'] or '(untitled)'}  [{data['id']}]")
    for label, value in describe(data):
        click.echo(f"{label}: {value}")
    if data["description"]:
        click.echo(f"Description: {data['description']}")
    if data["system_instruction"]:
        click.echo(f"\nSystem instructions:\n{data['system_instruction']}")
    for turn in data["turns"]:
        when = f"  {turn['at']}" if turn["at"] else ""
        click.echo(f"\n--- {turn['role']}{when} ---")
        for att in turn["attachments"]:
            click.echo(f"[attachment: {attachment_label(att)}]")
        if thoughts and turn["thoughts"]:
            click.echo(f"[thinking]\n{turn['thoughts']}\n[/thinking]")
        if turn["text"]:
            click.echo(turn["text"])


@prompt.command("get")
@click.argument("prompt_id")
@click.option("--thoughts", is_flag=True, help="Also show the model's saved thinking summaries.")
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path),
              help="Save the prompt as a markdown file instead of printing it.")
@click.option("--force", is_flag=True, help="Overwrite the -o file if it exists.")
@json_option
@click.pass_obj
@handle_errors
def get_cmd(state: CliState, prompt_id: str, thoughts: bool, out: Path | None, force: bool,
            json_output: bool) -> None:
    """Show one saved prompt or chat (id or unique prefix) in full.

    Prints the title, model, system instructions, generation settings and every turn untruncated.
    Attachments are listed by type only; their bytes are never fetched.
    """
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await get_prompt(client, prompt_id)

    data = asyncio.run(run())
    if out is not None:
        saved = save_markdown(data, out, force=force, thoughts=thoughts)
        data = {**data, "saved_to": str(saved)}
        emit(data, json_output=json_output,
             render=lambda d: click.echo(f"Saved {d['title'] or d['id']} to {d['saved_to']}"))
    else:
        emit(data, json_output=json_output, render=lambda d: _render_prompt(d, thoughts=thoughts))
