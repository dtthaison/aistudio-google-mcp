"""``aistudio app`` and ``aistudio gallery``: AI Studio Build, read-only (spec §6.7)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import click
from rich.table import Table

from aistudio._app.apps import gallery_get, gallery_list, get_app, list_apps, recent_apps
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


def _run(state: CliState, use_case: Callable[[AIStudioClient], Awaitable[dict[str, Any]]]
         ) -> dict[str, Any]:
    state.require_http()
    profile = state.profile()

    async def go() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await use_case(client)

    return asyncio.run(go())


def _table(columns: list[str], rows: list[list[str]], *, id_column: bool = True) -> None:
    table = Table()
    for i, name in enumerate(columns):
        if i == 0 and id_column:
            table.add_column(name, no_wrap=True)
        else:
            table.add_column(name, overflow="fold")
    for row in rows:
        table.add_row(*row)
    stdout.print(table)


@click.group()
def app() -> None:
    """Your AI Studio Build apps (read-only)."""


@app.command("list")
@click.option("--limit", type=click.IntRange(1, 1000), default=1000, show_default=True)
@json_option
@click.pass_obj
@handle_errors
def app_list(state: CliState, limit: int, json_output: bool) -> None:
    """List your Build apps."""
    data = _run(state, lambda c: list_apps(c, limit=limit))
    emit(data, json_output=json_output, render=lambda d: _table(
        ["id", "title", "updated", "storage"],
        [[a["id"], a["title"] or "", a["updated"] or "", a["storage"]] for a in d["apps"]]))


@app.command("get")
@click.argument("app_id")
@json_option
@click.pass_obj
@handle_errors
def app_get(state: CliState, app_id: str, json_output: bool) -> None:
    """Show one app (id or unique prefix) and its builder runs."""
    data = _run(state, lambda c: get_app(c, app_id))

    def render(d: dict[str, Any]) -> None:
        stdout.print(f"{d['title'] or '(untitled)'}  [{d['id']}]  ({d['storage']})")
        if d["description"]:
            stdout.print(d["description"])
        stdout.print(f"Prompt: {d['prompt'] or ''}", markup=False)
        stdout.print(f"Created: {d['created'] or '-'}  Updated: {d['updated'] or '-'}")
        for run in d["runs"]:
            stdout.print(f"  run {run['started'] or '-'}: {run['prompt'] or ''}", markup=False)

    emit(data, json_output=json_output, render=render)


@app.command("recent")
@json_option
@click.pass_obj
@handle_errors
def app_recent(state: CliState, json_output: bool) -> None:
    """Recently opened apps, gallery apps and prompts."""
    data = _run(state, recent_apps)
    emit(data, json_output=json_output, render=lambda d: _table(
        ["id", "kind", "opened", "title"],
        [[i["id"] or "", i["kind"], i["opened"] or "", i["title"] or ""] for i in d["recent"]]))


@click.group()
def gallery() -> None:
    """AI Studio Build gallery: showcase apps and templates."""


@gallery.command("list")
@click.option("--group", type=click.Choice(["showcase", "template"]))
@click.option("--tag", help="e.g. google-maps")
@click.option("--search", help="Text in the slug, title or description.")
@json_option
@click.pass_obj
@handle_errors
def gallery_list_cmd(state: CliState, group: str | None, tag: str | None,
                     search: str | None, json_output: bool) -> None:
    """List gallery apps."""
    data = _run(state, lambda c: gallery_list(c, group=group, tag=tag, search=search))
    emit(data, json_output=json_output, render=lambda d: _table(
        ["slug", "group", "title", "apis"],
        [[a["slug"], a["group"] or "", a["title"] or "", ", ".join(a["apis"])]
         for a in d["apps"]]))


@gallery.command("get")
@click.argument("slug")
@json_option
@click.pass_obj
@handle_errors
def gallery_get_cmd(state: CliState, slug: str, json_output: bool) -> None:
    """Show one gallery app (slug or unique prefix)."""
    data = _run(state, lambda c: gallery_get(c, slug))

    def render(d: dict[str, Any]) -> None:
        stdout.print(f"{d['title'] or d['slug']}  [{d['slug']}]  ({d['group'] or '-'})")
        if d["description"]:
            stdout.print(d["description"])
        if d["apis"]:
            stdout.print(f"APIs: {', '.join(d['apis'])}")
        for label, url in (("Video", d["video_url"]), ("Image", d["image_url"])):
            if url:
                stdout.print(f"{label}: {url}", markup=False)

    emit(data, json_output=json_output, render=render)
