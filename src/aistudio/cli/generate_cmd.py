"""``aistudio generate speech`` and ``aistudio voices list`` (spec §6.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.media import DEFAULT_SPEECH_MODEL, generate_speech, list_voices
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState
from aistudio.exceptions import ValidationError


@click.group()
def generate() -> None:
    """Generate media with AI Studio."""


def _render_speech(data: dict[str, Any]) -> None:
    file = data["files"][0]
    voice = f", voice {data['voice']}" if data["voice"] else ""
    stdout.print(f"Wrote {file['path']} ({file['seconds']} s{voice})", markup=False)


@generate.command("speech")
@click.argument("text", required=False)
@click.option("--text-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the text from a file instead.")
@click.option("-m", "--model", default=DEFAULT_SPEECH_MODEL, show_default=True,
              help="A text-to-speech model id.")
@click.option("--voice", help='Voice name, e.g. Fola or Tova (see "aistudio voices list").')
@click.option("--style", help='How to speak, e.g. "warm, slow and smiling".')
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path),
              help="Output .wav (default: aistudio-speech-<time>.wav).")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window instead of minimizing it.")
@json_option
@click.pass_obj
@handle_errors
def speech(state: CliState, text: str | None, text_file: Path | None, model: str,
           voice: str | None, style: str | None, out: Path | None, force: bool,
           visible: bool, json_output: bool) -> None:
    """Turn TEXT into speech. Put expression tags such as <laugh> inside the text."""
    state.require_browser()
    if (text is None) == (text_file is None):
        raise ValidationError("give either TEXT or --text-file", hint="not both, not neither")
    content = text if text_file is None else text_file.read_text(encoding="utf-8")
    if not json_output:
        stderr.print("Generating speech in a minimized Chrome window…", markup=False)
    result = asyncio.run(generate_speech(
        state.profile(), content, model=model, voice=voice, style=style, out=out,
        overwrite=force, visible=visible,
    ))
    emit(result, json_output=json_output, render=_render_speech)


@click.group()
def voices() -> None:
    """Voices available for text-to-speech."""


@voices.command("list")
@click.option("--search", help="Only voices whose name contains this text.")
@json_option
@click.pass_obj
@handle_errors
def voices_list(state: CliState, search: str | None, json_output: bool) -> None:
    """List the speech page's voices (reads the page; generates nothing)."""
    state.require_browser()
    result = asyncio.run(list_voices(state.profile(), search=search))
    emit(result, json_output=json_output,
         render=lambda d: stdout.print(", ".join(d["voices"]) or "No voices found",
                                       markup=False))
