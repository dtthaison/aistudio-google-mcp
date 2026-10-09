"""``aistudio generate speech|image|music`` and ``aistudio voices list`` (spec §6.4)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from aistudio._app.media import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_MUSIC_MODEL,
    DEFAULT_SPEECH_MODEL,
    DEFAULT_TRANSCRIBE_MODEL,
    generate_image,
    generate_music,
    generate_speech,
    list_voices,
    transcribe,
)
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.inputs import read_text
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState


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
    content = read_text(text, text_file, arg="TEXT", option="--text-file")
    result = asyncio.run(generate_speech(
        state.profile(), content, model=model, voice=voice, style=style, out=out,
        overwrite=force, visible=visible, on_start=_notice_for("speech", visible, json_output),
    ))
    emit(result, json_output=json_output, render=_render_speech)


def _notice_for(what: str, visible: bool, json_output: bool) -> Callable[[], None] | None:
    """The "Generating…" line, printed by the use case once validation has passed."""
    if json_output:
        return None
    window = "visible" if visible else "minimized"
    return lambda: stderr.print(f"Generating {what} in a {window} Chrome window…", markup=False)


def _render_media(data: dict[str, Any]) -> None:
    for file in data["files"]:
        stdout.print(f"Wrote {file['path']} ({file['bytes']} bytes)", markup=False)
    if data.get("text"):
        stdout.print(f"Text: {data['text']}", markup=False)
    for warning in data.get("warnings", []):
        stderr.print(f"Warning: {warning}", markup=False)


def _media_command(name: str, category: str, default_model: str, help_text: str,
                   out_help: str) -> click.Command:
    @generate.command(name, help=help_text)
    @click.argument("prompt", required=False)
    @click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                  help="Read the prompt from a file instead.")
    @click.option("-m", "--model", default=default_model, show_default=True,
                  help=f'A model id (see "aistudio models list --category {category}").')
    @click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), help=out_help)
    @click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
    @click.option("--visible", is_flag=True,
                  help="Show the browser window instead of minimizing it.")
    @json_option
    @click.pass_obj
    @handle_errors
    def command(state: CliState, prompt: str | None, prompt_file: Path | None, model: str,
                out: Path | None, force: bool, visible: bool, json_output: bool,
                **extra: Any) -> None:
        state.require_browser()
        content = read_text(prompt, prompt_file, arg="PROMPT", option="--prompt-file")
        run = generate_image if name == "image" else generate_music
        inputs = {"images": list(extra["images"])} if extra.get("images") else {}
        result = asyncio.run(run(state.profile(), content, model=model, out=out,
                                 overwrite=force, visible=visible,
                                 on_start=_notice_for(name, visible, json_output), **inputs))
        emit(result, json_output=json_output, render=_render_media)

    if name == "image":  # an input image to edit or combine (uploaded to your Drive by AI Studio)
        command = click.option(
            "--image", "images", multiple=True,
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            help="An image to edit or combine (repeatable). AI Studio uploads it to your "
                 "Google Drive.")(command)
    return command


image = _media_command(
    "image", "images", DEFAULT_IMAGE_MODEL,
    "Generate an image from PROMPT with a Gemini image model (uses quota).",
    "Output file (default: aistudio-image-<time>.<type>, e.g. .jpg).")
music = _media_command(
    "music", "music", DEFAULT_MUSIC_MODEL,
    "Generate music from PROMPT with Lyria (uses quota; lyria-3.5 makes full songs).",
    "Output file (default: aistudio-music-<time>.mp3).")


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


def _clock(seconds: float | None) -> str:
    seconds = seconds or 0.0
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


@click.command("transcribe")
@click.argument("audio", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-m", "--model", default=DEFAULT_TRANSCRIBE_MODEL, show_default=True,
              help='A transcription model (see "aistudio models list --category audio").')
@click.option("--timestamps/--no-timestamps", default=None,
              help="Word timestamps (AI Studio turns them on by default).")
@click.option("--diarize/--no-diarize", default=None,
              help="Speaker labels (AI Studio turns them on by default).")
@click.option("--smart/--no-smart", default=None,
              help="Smart transcription (default: AI Studio's own setting).")
@click.option("--vocabulary", default="",
              help="Words to expect, e.g. names or brands (turns timestamps and speaker labels "
                   "off: AI Studio cannot combine them).")
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path),
              help="Also write the text to this file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(30), default=600, show_default=True,
              help="Seconds to wait for the transcript (raise it for long recordings).")
@click.option("--visible", is_flag=True, help="Show the browser window instead of minimizing it.")
@json_option
@click.pass_obj
@handle_errors
def transcribe_cmd(state: CliState, audio: Path, model: str, timestamps: bool | None,
                   diarize: bool | None, smart: bool | None, vocabulary: str, out: Path | None,
                   force: bool, timeout: int, visible: bool, json_output: bool) -> None:
    """Turn the speech in AUDIO into text (uses quota; AI Studio uploads the audio to your
    Google Drive)."""
    state.require_browser()
    result = asyncio.run(transcribe(state.profile(), audio, model=model, timestamps=timestamps,
                                    diarize=diarize, smart=smart, vocabulary=vocabulary, out=out,
                                    overwrite=force, visible=visible, timeout_s=float(timeout),
                                    on_start=_notice_for("transcription", visible,
                                                         json_output)))

    def render(data: dict[str, Any]) -> None:
        for segment in data.get("segments") or [{"text": data["text"], "speaker": None,
                                                 "start": None, "end": None}]:
            when = (f"[{_clock(segment['start'])} - {_clock(segment['end'])}] "
                    if segment.get("start") is not None else "")
            who = f"{segment['speaker']}: " if segment.get("speaker") else ""
            stdout.print(f"{when}{who}{segment['text']}", markup=False)
        if data.get("file"):
            stderr.print(f"Wrote {data['file']['path']}", markup=False)
        for warning in data.get("warnings", []):
            stderr.print(f"Warning: {warning}", markup=False)

    emit(result, json_output=json_output, render=render)
