"""Chat-area commands. M2: ``aistudio count-tokens`` (spec §6.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.chat import DEFAULT_MODEL
from aistudio._app.chat import count_tokens as count_tokens_use_case
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient
from aistudio.exceptions import ValidationError


@click.command("count-tokens")
@click.argument("text", required=False)
@click.option("-m", "--model", default=DEFAULT_MODEL, show_default=True, help="Model id.")
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the text from a file instead.")
@json_option
@click.pass_obj
@handle_errors
def count_tokens(state: CliState, text: str | None, model: str, prompt_file: Path | None,
                 json_output: bool) -> None:
    """Count the tokens TEXT uses with a model."""
    state.require_http()
    if (text is None) == (prompt_file is None):
        raise ValidationError("give either TEXT or --prompt-file", hint="not both, not neither")
    content = text if prompt_file is None else prompt_file.read_text(encoding="utf-8")
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await count_tokens_use_case(client, content, model=model)

    emit(asyncio.run(run()), json_output=json_output,
         render=lambda d: stdout.print(f"{d['total_tokens']} tokens ({d['model']})"))
