"""``aistudio models``: the AI Studio model picker as a catalog (spec §6.2)."""

from __future__ import annotations

import asyncio
from typing import Any

import click
from rich.table import Table

from aistudio._app.models import get_model, list_models
from aistudio._types.models import CATEGORY_ORDER
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


@click.group()
def models() -> None:
    """Browse the model catalog (the AI Studio model picker)."""


def _price(model: dict[str, Any]) -> str:
    if model["pricing"]:
        row = model["pricing"][0]
        price = "" if row["price_usd"] is None else f" ${row['price_usd']:g}"
        return f"{row['direction']}: {row['label']}{price}"
    return model["pricing_summary"] or ""


def _render_list(data: dict[str, Any]) -> None:
    table = Table("id", "categories", "released", "command", "price")
    for m in data["models"]:
        table.add_row(m["id"], ", ".join(m["categories"]), m["release_date"] or "",
                      m["command"] or "", _price(m))
    stdout.print(table)


@models.command("list")
@click.option("--category", type=click.Choice(CATEGORY_ORDER), help="A picker filter chip.")
@click.option("--search", help="Text to find in the id, name or description.")
@click.option("--refresh", is_flag=True, help="Ignore the 1-hour catalog cache.")
@json_option
@click.pass_obj
@handle_errors
def list_cmd(state: CliState, category: str | None, search: str | None, refresh: bool,
             json_output: bool) -> None:
    """List models, optionally filtered like the picker."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await list_models(client, profile, category=category, search=search,
                                     refresh=refresh)

    emit(asyncio.run(run()), json_output=json_output, render=_render_list)


@models.command("get")
@click.argument("model_id")
@click.option("--refresh", is_flag=True, help="Ignore the 1-hour catalog cache.")
@json_option
@click.pass_obj
@handle_errors
def get_cmd(state: CliState, model_id: str, refresh: bool, json_output: bool) -> None:
    """Show one model's card."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await get_model(client, profile, model_id, refresh=refresh)

    emit(asyncio.run(run()), json_output=json_output,
         render=lambda m: _render_list({"models": [m]}))
