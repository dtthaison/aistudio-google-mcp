"""``aistudio flow``: Google Flow video and image generation with a credit budget (spec §3)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import click

from aistudio._app.flow import (
    flow_character,
    flow_character_body,
    flow_character_voice,
    flow_characters,
    flow_credits,
    flow_download,
    flow_edit,
    flow_extend,
    flow_frame,
    flow_generate,
    flow_image_batch,
    flow_media,
    flow_projects,
    flow_rename_character,
    flow_scene_download,
    flow_scenes,
    flow_set_personality,
    flow_stitch,
    flow_voices,
)
from aistudio._app.flow_music import (
    flow_music,
    flow_music_credits,
    flow_music_download,
    flow_music_get,
    flow_music_list,
)
from aistudio._app.flow_storyboard import flow_storyboard
from aistudio._app.flow_tools import (
    flow_tool_create,
    flow_tool_edit,
    flow_tool_export,
    flow_tool_get,
    flow_tool_use,
    flow_tools,
)
from aistudio._flow.models import ASPECTS, MODELS
from aistudio._flow.music import MAX_LENGTH_S, SONG_PRICE
from aistudio._flow.music import MODELS as MUSIC_MODELS
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.inputs import read_prompts, read_text
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState
from aistudio.exceptions import ValidationError, WaitTimeoutError


@click.group()
def flow() -> None:
    """Google Flow (flow.google.com): video and images, paid in Flow credits; and songs with
    Google Flow Music (flowmusic.app), paid in Flow Music credits."""


@flow.command("credits")
@click.option("--music", is_flag=True,
              help="Flow Music's credits instead (flowmusic.app has its own).")
@click.option("--visible", is_flag=True, help="Show the browser window instead of minimizing it.")
@json_option
@click.pass_obj
@handle_errors
def credits_cmd(state: CliState, music: bool, visible: bool, json_output: bool) -> None:
    """How many Flow credits are left (free): the account menu, or Flow Music's with --music."""
    state.require_browser()
    run = flow_music_credits if music else flow_credits
    result = asyncio.run(run(state.profile(), visible=visible))
    what = "Flow Music credits" if music else "Flow credits"
    emit(result, json_output=json_output,
         render=lambda d: stdout.print(f"{d['credits']} {what} left", markup=False))


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        stdout.print(line, markup=False)


def _left(data: dict[str, Any]) -> str:
    left = data.get("credits_left")
    return "credits left unknown" if left is None else f"{left} left"


def _spent_line(data: dict[str, Any]) -> str:
    spent = data.get("credits_spent")
    head = "credits spent unknown" if spent is None else f"Spent {spent} credits"
    return f"{head}; {_left(data)}"


def _render(data: dict[str, Any]) -> None:
    if data["status"] == "quoted":
        stdout.print(f"Flow quotes {data['credits']} credits for {data['count']} output(s): "
                     f"{data['plan']}", markup=False)
        stdout.print(f"Nothing was spent (Flow's request was declined). Re-run with "
                     f"--max-credits {data['credits']} to generate.", markup=False)
    elif data["status"] == "timeout":
        stdout.print(f"Still running in Flow ({data['credits_spent']} credits spent): "
                     f"{data['project_url']}", markup=False)
    else:
        for file in data["files"]:
            stdout.print(f"Wrote {file['path']} ({file['bytes']} bytes)", markup=False)
        stdout.print(_spent_line(data), markup=False)
    for warning in data.get("warnings", []):
        stderr.print(f"Warning: {warning}", markup=False)


def _batch(state: CliState, prompts_file: Path, start_at: int | None, prompt: str | None,
          prompt_file: Path | None, json_output: bool, timeout: int,
          options: dict[str, Any]) -> None:
    """``flow image --prompts-file``: everything is checked here before Chrome opens."""
    if prompt is not None or prompt_file is not None:
        raise ValidationError("give --prompts-file or a PROMPT, not both",
                              hint="a batch takes its prompts from the file only")
    if options["i_have_rights"]:
        raise ValidationError("--i-have-rights does not go with --prompts-file",
                              hint="pass the media id of an image already in Flow to "
                                   "--ingredient")
    out = options["out"]
    if out is None:
        raise ValidationError("--prompts-file needs -o FOLDER", hint="the images go there")
    prompts = read_prompts(prompts_file)
    window = "visible" if options["visible"] else "minimized"

    def notice() -> None:
        stderr.print(f"Asking Flow in a {window} Chrome window, one prompt at a time…",
                     markup=False)
    result = asyncio.run(flow_image_batch(
        state.profile(), prompts, out=out, overwrite=options["force"], start_at=start_at or 1,
        model=options["model"], aspect=options["aspect"], count=options["count"],
        max_credits=options["max_credits"], project=options["project"],
        timeout_s=float(timeout), visible=options["visible"],
        ingredients=list(options["ingredient"]), characters=list(options["characters"]),
        on_start=None if json_output else notice))
    emit(result, json_output=json_output, render=_render_batch)
    if result["status"] == "stopped":
        timed_out = result.get("error", {}).get("code") == WaitTimeoutError.code or any(
            r["status"] == "timeout" for r in result["results"])
        raise SystemExit(2 if timed_out else 1)


def _preview(prompt: str) -> str:
    flat = " ".join(prompt.split())
    return flat if len(flat) <= 50 else flat[:49] + "…"


def _render_batch(data: dict[str, Any]) -> None:
    for r in data["results"]:
        head = f"#{r['index']} {_preview(r['prompt'])}: "
        if r["status"] == "done":
            stdout.print(head + f"done, {r['credits_spent']} credits", markup=False)
            for file in r["files"]:
                stdout.print(f"  Wrote {file['path']} ({file['bytes']} bytes)", markup=False)
        elif r["status"] == "quoted":
            stdout.print(head + f"Flow quotes {r['quote_credits']} credits", markup=False)
        elif r["status"] == "timeout":
            stdout.print(head + f"still running in Flow ({r['credits_spent']} credits spent); "
                                f"media {', '.join(r['media_ids']) or 'unknown'}", markup=False)
        elif r["status"] == "failed":
            stdout.print(head + f"failed: {r['error']['message']}", markup=False)
        else:
            stdout.print(head + "not run", markup=False)
        for warning in r.get("warnings", []):
            stderr.print(f"Warning (#{r['index']}): {warning}", markup=False)
    done = sum(r["status"] == "done" for r in data["results"])
    if data["status"] == "quoted":
        stdout.print(f"Flow quotes {data['quote_credits']} credits for "
                     f"{len(data['results'])} prompts. Nothing was spent. Re-run with "
                     f"--max-credits {data['quote_credits']} to generate them all.",
                     markup=False)
        return
    stdout.print(f"{done} of {len(data['results'])} prompts done; "
                 f"{data['credits_spent']} credits spent", markup=False)
    if data["status"] == "stopped":
        error = data.get("error")
        if error:
            stderr.print(f"Error: {error['message']}", markup=False)
            if error.get("hint"):
                stderr.print(f"Hint: {error['hint']}", markup=False)
        stdout.print(f"Stopped; nothing was retried. Check the Flow project first (the prompt "
                     f"it stopped at may have been made), then continue with the same command "
                     f"plus --start-at {data['resume_at']}.", markup=False)


def _command(kind: str) -> click.Command:
    ids = [m.id for m in MODELS if m.kind == kind]

    @click.argument("prompt", required=False)
    @click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                  help="Read the prompt from a file instead.")
    @click.option("-m", "--model", type=click.Choice(ids, case_sensitive=False),
                  help="Model (default: your Flow default).")
    @click.option("--aspect", type=click.Choice(list(ASPECTS[kind])))
    @click.option("--count", type=click.IntRange(1, 4), help="Outputs (default: your Flow "
                                                             "default).")
    @click.option("--max-credits", type=click.IntRange(0), help="Spend at most this many "
                  "credits; without it a paid model only shows Flow's quote (the free Nano "
                  "Banana 2.1 generates at once, 0 credits).")
    @click.option("--project", help="Flow project id or link (default: an 'aistudio' project).")
    @click.option("-o", "--out", type=click.Path(path_type=Path),
                  help="Output file, or a folder for several outputs.")
    @click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
    @click.option("--timeout", type=click.IntRange(30), show_default=True,
                  default=1200 if kind == "video" else 600,  # queued Fast videos took > 10 min
                  help="Seconds to wait for Flow to finish after approving.")
    @click.option("--ingredient", multiple=True,
                  help="Reference image: a file or a Flow media id (repeatable).")
    @click.option("--i-have-rights", is_flag=True,
                  help="Confirm you have the rights to the local files you pass; Flow asks this "
                       "before any upload.")
    @click.option("--character", "characters", multiple=True,
                  help="A Flow character to feature, by unique name or id (repeatable).")
    @click.option("--visible", is_flag=True, help="Show the browser window.")
    @json_option
    @click.pass_obj
    @handle_errors
    def command(state: CliState, prompt: str | None, prompt_file: Path | None, json_output: bool,
                timeout: int, **options: Any) -> None:
        state.require_browser()
        prompts_file = options.pop("prompts_file", None)
        start_at = options.pop("start_at", None)
        if prompts_file is not None:
            _batch(state, prompts_file, start_at, prompt, prompt_file, json_output, timeout,
                   options)
            return
        if start_at is not None:
            raise ValidationError("--start-at goes with --prompts-file")
        content = read_text(prompt, prompt_file, arg="PROMPT", option="--prompt-file")
        window = "visible" if options["visible"] else "minimized"

        def notice() -> None:
            stderr.print(f"Asking Flow in a {window} Chrome window…", markup=False)
        out = options.pop("out")
        options["ingredients"] = list(options.pop("ingredient"))
        options["characters"] = list(options["characters"])
        result = asyncio.run(flow_generate(
            state.profile(), kind, content, out=out, overwrite=options.pop("force"),
            timeout_s=float(timeout), on_start=None if json_output else notice,
            **{**{"duration": None}, **options}))
        emit(result, json_output=json_output, render=_render)
        if result["status"] == "timeout":
            raise SystemExit(2)

    if kind == "video":  # frames and length are video-only (spec §3)
        command = click.option("--end-frame",
                               help="End frame: an image file or a Flow media id.")(command)
        command = click.option("--start-frame",
                               help="Start frame: an image file or a Flow media id.")(command)
        command = click.option("--duration", type=int, help="Seconds (per the model: Veo "
                               "3.1 Lite/Fast 4/6/8, Quality 8, Omni 4/6/8/10).")(command)
    if kind == "image":  # a batch of prompts is for images only
        command = click.option(
            "--start-at", type=click.IntRange(1),
            help="With --prompts-file: begin at this prompt number (1 is the first), to resume "
                 "a stopped batch.")(command)
        command = click.option(
            "--prompts-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
            help="Generate for every prompt in this JSON Lines file (one JSON string, or "
                 '{"prompt": "..."}, per line; a prompt\'s own line breaks are written \\n), one '
                 "after another in one browser session. Needs -o FOLDER; files are "
                 "<number>-<words of the prompt>-<n>. The other options apply to every prompt; "
                 "--max-credits is the budget for the whole batch. The free Nano Banana 2.1 "
                 "makes every image at once. The first error stops the batch.")(command)
    help_text = (f"Generate {kind}s with Google Flow. Without --max-credits a paid model only "
                 "shows Flow's quote and spends nothing"
                 + ("; the free Nano Banana 2.1 generates at once (0 credits)." if kind == "image"
                    else "."))
    return flow.command(kind, help=help_text)(command)


video = _command("video")
image = _command("image")


class _MusicGroup(click.Group):
    """``flow music PROMPT`` keeps generating a song: anything that is not a subcommand name
    goes to ``generate``. A prompt that is exactly ``list``, ``get`` or ``download`` needs the
    ``generate`` word."""

    def resolve_command(self, ctx: click.Context, args: list[str]) -> Any:
        if args and args[0] in self.commands:
            return super().resolve_command(ctx, args)
        return "generate", self.commands["generate"], args


@flow.group("music", cls=_MusicGroup, context_settings={"ignore_unknown_options": True})
def music() -> None:
    """Songs on Google Flow Music (flowmusic.app).

    \b
    flow music PROMPT              make a song (same as: flow music generate PROMPT); 5 credits
    flow music list                your songs, with ids (free)
    flow music get ID              one song's prompt and lyrics (free)
    flow music download ID         save a song you already have (free)
    """


@music.command("generate")
@click.argument("prompt", required=False)
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the sound prompt from a file instead.")
@click.option("--lyrics", help="Song lyrics (sections like [Verse] work); without lyrics the "
                               "song is instrumental.")
@click.option("--lyrics-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the lyrics from a file.")
@click.option("--length", "length_s", type=click.IntRange(1, MAX_LENGTH_S),
              help="Song length in seconds (default: Flow Music's own, about 3 minutes).")
@click.option("--bpm", type=click.IntRange(1, 400), help="Tempo in beats per minute.")
@click.option("--seed", type=click.IntRange(0), help="Seed, to repeat a result.")
@click.option("--title", help="Song title (default: Untitled).")
@click.option("-m", "--model", type=click.Choice(list(MUSIC_MODELS), case_sensitive=False),
              help="Model (default: Lyria 3.5).")
@click.option("--wav", is_flag=True,
              help="Save the WAV file (about 34 MB for 3 minutes) instead of the .m4a.")
@click.option("--max-credits", type=click.IntRange(0),
              help=f"Spend at most this many Flow Music credits (a song costs {SONG_PRICE}); "
                   "without it only the price is shown.")
@click.option("-o", "--out", type=click.Path(path_type=Path),
              help="Output file (default: flow-music-<time>.m4a).")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(30), default=600, show_default=True,
              help="Seconds to wait for Flow Music to finish the song.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def music_cmd(state: CliState, prompt: str | None, prompt_file: Path | None,
              lyrics: str | None, lyrics_file: Path | None, json_output: bool, timeout: int,
              visible: bool, **options: Any) -> None:
    """Generate a song with Google Flow Music (flowmusic.app's Compose). PROMPT describes the
    sound. Flow Music shows no quote, so without --max-credits nothing is spent."""
    state.require_browser()
    content = read_text(prompt, prompt_file, arg="PROMPT", option="--prompt-file")
    if lyrics is not None and lyrics_file is not None:
        raise click.UsageError("pass --lyrics or --lyrics-file, not both")
    words = lyrics_file.read_text(encoding="utf-8") if lyrics_file is not None else lyrics
    window = "visible" if visible else "minimized"

    def notice() -> None:
        stderr.print(f"Asking Flow Music in a {window} Chrome window…", markup=False)
    result = asyncio.run(flow_music(
        state.profile(), content, lyrics=words, overwrite=options.pop("force"),
        timeout_s=float(timeout), visible=visible,
        on_start=None if json_output else notice, **options))
    emit(result, json_output=json_output, render=_render_music)
    if result["status"] == "timeout":
        raise SystemExit(2)


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


@music.command("list")
@click.option("--search", help="Only songs whose title, prompt or lyrics contain TEXT.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def music_list(state: CliState, search: str | None, visible: bool, json_output: bool) -> None:
    """Your Flow Music songs, newest first, with failed and unfinished ones (free)."""
    state.require_browser()
    result = asyncio.run(flow_music_list(state.profile(), search=search, visible=visible))

    def render(data: dict[str, Any]) -> None:
        if not data["songs"]:
            stdout.print("No songs.", markup=False)
        _print_lines(f"{s['clip_id']}  {s['status']:11}  {_clock(s['seconds']):>5}  {s['title']}"
                     for s in data["songs"])
    emit(result, json_output=json_output, render=render)


@music.command("get")
@click.argument("song_id")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def music_get(state: CliState, song_id: str, visible: bool, json_output: bool) -> None:
    """One Flow Music song with its prompt and lyrics (free)."""
    state.require_browser()
    result = asyncio.run(flow_music_get(state.profile(), song_id, visible=visible))

    def render(data: dict[str, Any]) -> None:
        stdout.print(f"{data['title']}  ({data['clip_id']})", markup=False)
        stdout.print(f"{data['status']}, {_clock(data['seconds'])}, created "
                     f"{data['created'] or '?'}, model {data['model'] or '?'}", markup=False)
        stdout.print(f"Prompt: {data['prompt'] or '-'}", markup=False)
        stdout.print(f"Lyrics: {data['lyrics'] or '-'}", markup=False)
        stdout.print(f"Session: {data['project_url']}", markup=False)
    emit(result, json_output=json_output, render=render)


@music.command("download")
@click.argument("song_id")
@click.option("--wav", is_flag=True,
              help="Save the WAV file (about 34 MB for 3 minutes) instead of the .m4a.")
@click.option("-o", "--out", type=click.Path(path_type=Path),
              help="Output file (default: flow-music-<song id>.m4a).")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def music_download(state: CliState, song_id: str, wav: bool, out: Path | None, force: bool,
                   visible: bool, json_output: bool) -> None:
    """Download a Flow Music song you already have, by id (free; never overwrites without
    --force)."""
    state.require_browser()
    result = asyncio.run(flow_music_download(state.profile(), song_id, wav=wav, out=out,
                                             overwrite=force, visible=visible))
    emit(result, json_output=json_output, render=_render_files)


def _render_music(data: dict[str, Any]) -> None:
    if data["status"] == "quoted":
        stdout.print(f"A Flow Music song costs {data['credits']} credits ({data['model']}). "
                     f"Nothing was spent. Re-run with --max-credits {data['credits']} to "
                     "generate.", markup=False)
        return
    if data["status"] == "done":
        length = "" if data.get("seconds") is None else f", {data['seconds']:.0f} s"
        stdout.print(f"Made \"{data['title']}\"{length}: {data['project_url']}", markup=False)
    _render_files(data)


def _render_files(data: dict[str, Any]) -> None:
    if data.get("status") == "timeout":
        stdout.print(f"Still running in Flow ({data['credits_spent']} credits spent): "
                     f"{data['project_url']}", markup=False)
    for file in data.get("files", []):
        stdout.print(f"Wrote {file['path']} ({file['bytes']} bytes)", markup=False)
    if "credits_spent" in data and data.get("status") == "done":
        stdout.print(_spent_line(data), markup=False)
    for warning in data.get("warnings", []):
        stderr.print(f"Warning: {warning}", markup=False)


def _finish(result: dict[str, Any], json_output: bool) -> None:
    emit(result, json_output=json_output, render=_render_files)
    if result.get("status") == "timeout":
        raise SystemExit(2)


@flow.command("projects")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def projects_cmd(state: CliState, visible: bool, json_output: bool) -> None:
    """Your Flow projects (free)."""
    state.require_browser()
    result = asyncio.run(flow_projects(state.profile(), visible=visible))
    emit(result, json_output=json_output, render=lambda d: _print_lines(
        f"{p['id']}  {p['title'] or '(untitled)'}" for p in d["projects"]))


@flow.command("media")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--type", "kind", type=click.Choice(["video", "image", "audio"]),
              help="Only this kind; audio is character voice samples.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def media_cmd(state: CliState, project: str | None, kind: str | None, visible: bool,
              json_output: bool) -> None:
    """Media in a Flow project, with ids for --start-frame, --ingredient and extend (free)."""
    state.require_browser()
    result = asyncio.run(flow_media(state.profile(), project=project, kind=kind, visible=visible))
    emit(result, json_output=json_output, render=lambda d: _print_lines(
        f"{m['media_id']}  {m['kind']:5}  {m['status']:7}  {m['title'] or ''}"
        for m in d["media"]))


@flow.command("download")
@click.argument("media_id")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def download_cmd(state: CliState, media_id: str, project: str | None, out: Path | None,
                 force: bool, visible: bool, json_output: bool) -> None:
    """Download one Flow media by id (free)."""
    state.require_browser()
    _finish(asyncio.run(flow_download(state.profile(), media_id, project=project, out=out,
                                      overwrite=force, visible=visible)), json_output)


@flow.group("character")
def character() -> None:
    """Flow characters: reusable people and creatures for consistent videos."""


@character.command("create")
@click.argument("name")
@click.argument("description", required=False)
@click.option("--photo", help="Make the character from this image: a file or a Flow media id "
                              "(free; nothing is generated).")
@click.option("--i-have-rights", is_flag=True,
              help="Confirm you have the rights to the local photo; Flow asks this before upload.")
@click.option("--personality", help="The character's personality notes (free).")
@click.option("-m", "--model", type=click.Choice(["nano-banana-2.1", "nano-banana-pro",
                                                  "nano-banana-2-lite"]),
              help="Image model for a character from a DESCRIPTION (all measured at 0 credits).")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--max-credits", type=click.IntRange(0),
              help="Needed only when aistudio's price table says the character costs credits.")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output image file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_create(state: CliState, name: str, description: str | None, photo: str | None,
                     i_have_rights: bool, personality: str | None, model: str | None,
                     project: str | None,
                     max_credits: int | None, out: Path | None, force: bool, visible: bool,
                     json_output: bool) -> None:
    """Create a character called NAME from DESCRIPTION, or from --photo.

    Names are unique per project: --character picks characters by name. From a description Flow
    charges without a quote (see the price table); from a photo nothing is generated."""
    state.require_browser()
    result = asyncio.run(flow_character(
        state.profile(), name, description, photo=photo, i_have_rights=i_have_rights,
        personality=personality, model=model, max_credits=max_credits, project=project, out=out,
        overwrite=force, visible=visible))

    def render(data: dict[str, Any]) -> None:
        cid = data["character_id"] or "(id not reported)"
        stdout.print(f"Created character {cid} ({data['name']})", markup=False)
        _render_files(data)

    emit(result, json_output=json_output, render=render)


@character.command("list")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_list(state: CliState, project: str | None, visible: bool,
                   json_output: bool) -> None:
    """Characters in a Flow project, with ids and names for --character (free)."""
    state.require_browser()
    result = asyncio.run(flow_characters(state.profile(), project=project, visible=visible))
    emit(result, json_output=json_output, render=lambda d: _print_lines(
        f"{c['id']}  {c['name'] or '(unnamed)'}"
        f"{'' if c['media_id'] else '  (no image)'}"
        f"{_voice_note(c.get('voice'))}" for c in d["characters"]))


def _voice_note(voice: dict[str, Any] | None) -> str:
    if not voice:
        return ""
    return f"  voice: {voice.get('name') or voice.get('ref')}"


def _voice_line(v: dict[str, Any]) -> str:
    if v["kind"] == "custom":
        return f"{v['name']}  (custom, {v['media_id']}; {v['base']}: {v['style'] or ''})"
    return f"{v['name']}  {v['gender']}, {v['style']}, {v['pitch']}"


@character.command("voices")
@click.option("--search", help="Only voices whose name or description contains TEXT.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_voices(state: CliState, search: str | None, project: str | None, visible: bool,
                     json_output: bool) -> None:
    """Voices for characters: Flow's presets and the project's custom voices (free)."""
    state.require_browser()
    result = asyncio.run(flow_voices(state.profile(), search=search, project=project,
                                     visible=visible))
    emit(result, json_output=json_output,
         render=lambda d: _print_lines(_voice_line(v) for v in d["voices"]))


@character.command("voice")
@click.argument("character_id")
@click.argument("voice", required=False)
@click.option("--style", help="Make a new custom voice: VOICE (a preset) spoken in this "
                              "performance style, then give it to the character (free).")
@click.option("--voice-name", help="Name of the new custom voice (default: the character's "
                                   "name plus ' voice').")
@click.option("--clear", is_flag=True, help="Remove the character's voice.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_voice(state: CliState, character_id: str, voice: str | None, style: str | None,
                    voice_name: str | None, clear: bool, project: str | None, visible: bool,
                    json_output: bool) -> None:
    """Give a character a VOICE: a preset (e.g. Laomedeia) or a custom voice of the project, by
    name or media id (free). See "aistudio flow character voices"."""
    state.require_browser()
    result = asyncio.run(flow_character_voice(
        state.profile(), character_id, voice, style=style, voice_name=voice_name, clear=clear,
        project=project, visible=visible))

    def render(d: dict[str, Any]) -> None:
        made = d.get("created_voice")
        if made:
            stdout.print(f"Saved custom voice {made['name']} ({made['media_id']})", markup=False)
        name = d["voice"]["name"] if d["voice"] else None
        stdout.print(f"Voice of {d['character_id']}: {name or '(none)'}", markup=False)
        for warning in d.get("warnings", []):
            stderr.print(f"warning: {warning}", markup=False)

    emit(result, json_output=json_output, render=render)


@character.command("rename")
@click.argument("character_id")
@click.argument("name")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_rename(state: CliState, character_id: str, name: str, project: str | None,
                     visible: bool, json_output: bool) -> None:
    """Rename a character (free). Names are unique per project."""
    state.require_browser()
    result = asyncio.run(flow_rename_character(state.profile(), character_id, name,
                                               project=project, visible=visible))
    emit(result, json_output=json_output, render=lambda d: stdout.print(
        f"Renamed {d['character_id']} to {d['name']}", markup=False))


@character.command("body")
@click.argument("character_id")
@click.argument("prompt", required=False)
@click.option("--media", "media_id", metavar="MEDIA_ID",
              help="Add an image already in the project as the body instead (free).")
@click.option("--max-credits", type=click.IntRange(0),
              help="Needed only when aistudio's price table says the body costs credits.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output image file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_body(state: CliState, character_id: str, prompt: str | None, media_id: str | None,
                   max_credits: int | None, project: str | None, out: Path | None, force: bool,
                   visible: bool, json_output: bool) -> None:
    """Give a character a full body ("Create body"): an image generated from PROMPT (0 credits
    on Nano Banana 2.1, Flow charges without a quote), or with --media an image already in the
    project (free). Done only once Flow lists it as the character's body."""
    state.require_browser()
    result = asyncio.run(flow_character_body(state.profile(), character_id, prompt,
                                             media_id=media_id, max_credits=max_credits,
                                             project=project, out=out, overwrite=force,
                                             visible=visible))
    if "files" in result:
        _finish(result, json_output)
    else:
        emit(result, json_output=json_output, render=lambda d: stdout.print(
            f"Added {d['source_media_id']} as the body of {d['character_id']}", markup=False))


@character.command("personality")
@click.argument("character_id")
@click.argument("text")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_personality(state: CliState, character_id: str, text: str, project: str | None,
                          visible: bool, json_output: bool) -> None:
    """Set a character's personality notes; "" clears them (free)."""
    state.require_browser()
    result = asyncio.run(flow_set_personality(state.profile(), character_id, text,
                                              project=project, visible=visible))
    emit(result, json_output=json_output, render=lambda d: stdout.print(
        f"Personality of {d['character_id']}: {d['personality'] or '(cleared)'}", markup=False))


@flow.group("scene")
def scene() -> None:
    """Flow scenes: clips joined into one video (free)."""


@scene.command("list")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def scene_list(state: CliState, project: str | None, visible: bool, json_output: bool) -> None:
    """Scenes in a Flow project, with their clips in order (free)."""
    state.require_browser()
    result = asyncio.run(flow_scenes(state.profile(), project=project, visible=visible))
    emit(result, json_output=json_output, render=lambda d: _print_lines(
        f"{s['id']}  {s['title'] or '(untitled)'}  {len(s['clips'])} clip(s), "
        f"{sum(c['seconds'] for c in s['clips'])} s" for s in d["scenes"]))


@scene.command("stitch")
@click.argument("clips", nargs=-1, required=True)
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output MP4 file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(10), default=300, show_default=True,
              help="Seconds to wait for Flow to join the clips.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def scene_stitch(state: CliState, clips: tuple[str, ...], project: str | None, out: Path | None,
                 force: bool, timeout: int, visible: bool, json_output: bool) -> None:
    """Join finished video CLIPS into one MP4, in order (free).

    Each clip is a media id, optionally trimmed in whole seconds: <id>@START-END."""
    state.require_browser()
    _finish(asyncio.run(flow_stitch(state.profile(), list(clips), project=project, out=out,
                                    overwrite=force, timeout_s=float(timeout), visible=visible)),
            json_output)


@scene.command("download")
@click.argument("scene_id")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output MP4 file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(10), default=300, show_default=True,
              help="Seconds to wait for Flow to join the clips.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def scene_download(state: CliState, scene_id: str, project: str | None, out: Path | None,
                   force: bool, timeout: int, visible: bool, json_output: bool) -> None:
    """Download a scene as one MP4, such as the scene "flow extend" reports (free)."""
    state.require_browser()
    _finish(asyncio.run(flow_scene_download(state.profile(), scene_id, project=project, out=out,
                                            overwrite=force, timeout_s=float(timeout),
                                            visible=visible)), json_output)


@flow.command("edit")
@click.argument("media_id")
@click.argument("instruction")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--max-credits", type=click.IntRange(0),
              help="Spend at most this many credits (an Omni edit of an 8-second clip costs 20).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output file for the new clip.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(30), default=1200, show_default=True)
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def edit_cmd(state: CliState, media_id: str, instruction: str, project: str | None,
             max_credits: int | None, out: Path | None, force: bool, timeout: int,
             visible: bool, json_output: bool) -> None:
    """Edit a clip with INSTRUCTION, e.g. "make it black and white" (Omni 1.1 Flash; Flow
    charges without a quote, priced from aistudio's measured table)."""
    state.require_browser()
    _finish(asyncio.run(flow_edit(state.profile(), media_id, instruction, max_credits=max_credits,
                                  project=project, out=out, overwrite=force,
                                  timeout_s=float(timeout), visible=visible)), json_output)


@flow.command("extend")
@click.argument("media_id")
@click.argument("prompt")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--max-credits", type=click.IntRange(0),
              help="Spend at most this many credits (extend with Veo 3.1 Lite costs 10).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output file for the new clip.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(30), default=1200, show_default=True)
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def extend_cmd(state: CliState, media_id: str, prompt: str, project: str | None,
               max_credits: int | None, out: Path | None, force: bool, timeout: int,
               visible: bool, json_output: bool) -> None:
    """Extend a Veo clip with PROMPT (Flow charges without a quote; priced from aistudio's
    measured table)."""
    state.require_browser()
    _finish(asyncio.run(flow_extend(state.profile(), media_id, prompt, max_credits=max_credits,
                                    project=project, out=out, overwrite=force,
                                    timeout_s=float(timeout), visible=visible)), json_output)


def _warn(data: dict[str, Any]) -> None:
    for warning in data.get("warnings", []):
        stderr.print(f"Warning: {warning}", markup=False)


@flow.group("tool")
def tool() -> None:
    """Flow Tools: small apps Flow's agent writes from a description.

    \b
    Build a tool when it needs Flow itself:
      - Flow's AI (Flow.generate: images, video, text; spends credits)
      - media in your Flow project (pick files, save results back)
      - a page other people can use in Flow without installing anything
    Keep exact logic, batch scripts, outside APIs and heavy compute in
    normal code: the agent's code has no tests, every fix uses one of
    the daily builds, and a tool cannot reach most outside servers.

    \b
    Steps:
      1. aistudio flow tool list --kind template    (one may exist already)
      2. aistudio flow tool create --prompt-file spec.txt
      3. aistudio flow tool use ID                   (lists the controls)
      4. aistudio flow tool use ID 1=value "Export" -o out
      5. aistudio flow tool edit ID --prompt-file fix.txt
    """


@tool.command("list")
@click.option("--kind", type=click.Choice(["mine", "template", "community"]),
              help="Only your tools, Flow's templates, or community tools.")
@click.option("--search", help="Only tools with this text in the name or description.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def tool_list(state: CliState, kind: str | None, search: str | None, project: str | None,
              visible: bool, json_output: bool) -> None:
    """Tools this account can see, with ids (free)."""
    state.require_browser()
    result = asyncio.run(flow_tools(state.profile(), kind=kind, search=search, project=project,
                                    visible=visible))
    emit(result, json_output=json_output, render=lambda d: _print_lines(
        f"{t['id']}  [{t['kind']}]  {t['name']}" for t in d["tools"]))


@tool.command("get")
@click.argument("tool_id")
@click.option("--allow-remix", is_flag=True,
              help="Accept that reading a template adds a remix of it to your tools.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def tool_get(state: CliState, tool_id: str, allow_remix: bool, project: str | None,
             visible: bool, json_output: bool) -> None:
    """One tool's files and the Flow calls in its code (free). TOOL_ID may be a unique prefix."""
    state.require_browser()
    result = asyncio.run(flow_tool_get(state.profile(), tool_id, allow_remix=allow_remix,
                                       project=project, visible=visible))

    def render(data: dict[str, Any]) -> None:
        stdout.print(f"{data['id']}  {data['name']}", markup=False)
        for file in data["files"]:
            stdout.print(f"  {file['path']} ({file['bytes']} bytes)", markup=False)
        stdout.print(f"Flow calls: {', '.join(data['flow_calls']) or 'none'}", markup=False)
        if data["may_spend_credits"]:
            stderr.print("Warning: this tool may spend Flow credits when it runs, with no quote.",
                         markup=False)
        _warn(data)

    emit(result, json_output=json_output, render=render)


@tool.command("export")
@click.argument("tool_id")
@click.argument("dest", type=click.Path(path_type=Path))
@click.option("--force", is_flag=True, help="Overwrite files already in the folder.")
@click.option("--allow-remix", is_flag=True,
              help="Accept that reading a template adds a remix of it to your tools.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def tool_export(state: CliState, tool_id: str, dest: Path, force: bool, allow_remix: bool,
                project: str | None, visible: bool, json_output: bool) -> None:
    """Save a tool's code into the folder DEST (free)."""
    state.require_browser()
    result = asyncio.run(flow_tool_export(state.profile(), tool_id, dest, force=force,
                                          allow_remix=allow_remix, project=project,
                                          visible=visible))

    def render(data: dict[str, Any]) -> None:
        stdout.print(f"Wrote {data['written']} files of {data['name']} to {data['path']}",
                     markup=False)
        _warn(data)

    emit(result, json_output=json_output, render=render)


def _finish_build(result: dict[str, Any], json_output: bool) -> None:
    def render(data: dict[str, Any]) -> None:
        if data["status"] == "timeout":
            stdout.print(f"Flow's agent is still working. Check it here before re-running: "
                         f"{data['url']}", markup=False)
            return
        stdout.print(f"Built {data['name']} in {data['seconds']} s: {data['id']}", markup=False)
        stdout.print(data["url"], markup=False)
        if data["may_spend_credits"]:
            stderr.print("Warning: this tool may spend Flow credits when it runs, with no quote.",
                         markup=False)

    emit(result, json_output=json_output, render=render)
    if result["status"] == "timeout":
        raise SystemExit(2)


@tool.command("create")
@click.argument("description", required=False)
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the description from a file instead.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--timeout", type=click.IntRange(30), default=600, show_default=True,
              help="Seconds to wait for Flow's agent.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def tool_create(state: CliState, description: str | None, prompt_file: Path | None,
                project: str | None, timeout: int, visible: bool, json_output: bool) -> None:
    """Have Flow's agent build a tool from DESCRIPTION (no credits; counts toward Flow's daily
    limit for tools).

    So the tool can be driven with "flow tool use", ask in DESCRIPTION for real HTML
    controls (input, select, checkbox, button) with visible text labels, and for every
    export to go through Flow.download. Say "no Flow.generate" when it needs no AI, so
    using it never spends credits. For slow AI steps, ask for a ready signal the CLI can wait
    on with @LABEL or ?TEXT: a button that is disabled while a step runs, or a progress text
    such as "4 / 4 ready".
    """
    state.require_browser()
    text = read_text(description, prompt_file, arg="DESCRIPTION", option="--prompt-file")
    _finish_build(asyncio.run(flow_tool_create(state.profile(), text, project=project,
                                               timeout_s=float(timeout), visible=visible)),
                  json_output)


@tool.command("edit")
@click.argument("tool_id")
@click.argument("change", required=False)
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the change from a file instead.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--timeout", type=click.IntRange(30), default=600, show_default=True,
              help="Seconds to wait for Flow's agent.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def tool_edit(state: CliState, tool_id: str, change: str | None, prompt_file: Path | None,
              project: str | None, timeout: int, visible: bool, json_output: bool) -> None:
    """Have Flow's agent make CHANGE to one of your own tools."""
    state.require_browser()
    text = read_text(change, prompt_file, arg="CHANGE", option="--prompt-file")
    _finish_build(asyncio.run(flow_tool_edit(state.profile(), tool_id, text, project=project,
                                             timeout_s=float(timeout), visible=visible)),
                  json_output)



def _control_line(first: dict[str, Any], last: dict[str, Any], repeats: int) -> str:
    number = str(first["n"]) if repeats == 1 else f"{first['n']}-{last['n']}"
    line = f"{number}  {first['kind']}  {first['label'] or '(no label)'}"
    if first["value"] not in (None, ""):
        line += f" = {first['value']}"
    if first["min"] is not None and first["max"] is not None:
        line += f"  [{first['min']}-{first['max']}]"
    if first["options"]:
        line += f"  ({' | '.join(first['options'])})"
    if first["accept"]:
        line += f"  ({first['accept']})"
    if first["disabled"]:
        line += "  (disabled)"
    return line + (f"  (x{repeats})" if repeats > 1 else "")


def _render_use(data: dict[str, Any]) -> None:
    for step in data["steps"]:
        if step.get("wait"):
            stdout.print(f"{step['step']}: ready after {step['waited_s']:.0f} s", markup=False)
            continue
        done = f" = {step['value']}" if step["value"] is not None else ""
        stdout.print(f"{step['step']}: {step['kind']} {step['control']} "
                     f"({step['label']}){done}", markup=False)
    for file in data["files"]:
        stdout.print(f"Saved {file['path']} ({file['bytes']} bytes)", markup=False)
    for item in data.get("saved_to_flow", []):
        stdout.print(f"Saved {item['path']} ({item['bytes']} bytes; {item['kind']} "
                     f"{item['media_id']} in the Flow project)", markup=False)
    if data["steps"] and not data["files"] and not data.get("saved_to_flow"):
        stdout.print("The tool saved no files.", markup=False)
    stdout.print(f"\n{data['name']} controls:", markup=False)
    controls = data["controls"]
    i = 0
    while i < len(controls):  # fold runs of the same control, e.g. one button per row
        j = i
        while (j + 1 < len(controls) and controls[j + 1]["kind"] == controls[i]["kind"]
               and controls[j + 1]["label"] == controls[i]["label"]):
            j += 1
        stdout.print("  " + _control_line(controls[i], controls[j], j - i + 1), markup=False)
        i = j + 1
    if "credits_spent" in data:
        stdout.print(f"Credits spent: {data['credits_spent']}", markup=False)
    _warn(data)


@tool.command("use")
@click.argument("tool_id")
@click.argument("steps", nargs=-1)
@click.option("-o", "--out-dir", type=click.Path(file_okay=False, path_type=Path),
              default=Path("."), show_default=True, help="Folder for the files the tool saves.")
@click.option("--force", is_flag=True, help="Overwrite files with the same names.")
@click.option("--allow-spend", is_flag=True,
              help="Run a tool whose code calls Flow.generate (spends credits, with no quote).")
@click.option("--allow-remix", is_flag=True,
              help="Accept that opening a template adds a remix of it to your tools.")
@click.option("--step-timeout", type=click.IntRange(5), default=60, show_default=True,
              help="Most seconds to wait for the tool after each step, and for each @ or ? "
                   "wait step.")
@click.option("--drop", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Drop this file onto the tool first (for tools that take a file only by "
                   "drag and drop).")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def tool_use(state: CliState, tool_id: str, steps: tuple[str, ...], out_dir: Path, force: bool,
             allow_spend: bool, allow_remix: bool, step_timeout: int, drop: Path | None,
             project: str | None, visible: bool, json_output: bool) -> None:
    """Use a tool: run STEPS on its controls in order and save the files it downloads.

    Without STEPS, list the tool's controls with their numbers. Each step is N or LABEL to
    press a control, or N=VALUE or "LABEL=VALUE" to set one (a file path for an upload, a number
    for a slider, true/false for a checkbox). A LABEL may be part of the label if only one
    control has it. Numbers can change after a step adds controls (an upload often does): use
    labels, or list them again if unsure.

    A step returns once the tool's screen stops changing, which can be before slow AI work
    ends. To wait for it, add @LABEL (wait until that control is there and enabled) or
    "?TEXT" (wait until the tool's screen shows TEXT), each for up to --step-timeout seconds,
    for example: 3=photo.png "Write the brief" "@Generate images" "Generate images" "?4 / 4".
    Files the tool saves into the Flow project (Flow.save) are downloaded into --out-dir too.
    """
    state.require_browser()
    result = asyncio.run(flow_tool_use(state.profile(), tool_id, list(steps), out_dir,
                                       allow_remix=allow_remix, allow_spend=allow_spend,
                                       force=force, project=project, visible=visible,
                                       step_timeout_s=float(step_timeout), drop=drop))
    emit(result, json_output=json_output, render=_render_use)


@flow.group("frame")
def frame() -> None:
    """Frames of Flow clips, saved as images for --start-frame, --end-frame or --ingredient."""


@frame.command("save")
@click.argument("media_id")
@click.option("--at", type=click.FloatRange(0), help="Seconds into the clip (nearest frame, "
              "1/24 s); default: the clip's last frame.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("-o", "--out", type=click.Path(path_type=Path),
              help="Also download the saved image to this file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def frame_save(state: CliState, media_id: str, at: float | None, project: str | None,
               out: Path | None, force: bool, visible: bool, json_output: bool) -> None:
    """Save one frame of the clip MEDIA_ID as a new image in its Flow project (Flow's "Save
    frame"; free). Its media id works as --start-frame, --end-frame or --ingredient."""
    state.require_browser()
    result = asyncio.run(flow_frame(state.profile(), media_id, at=at, project=project, out=out,
                                    overwrite=force, visible=visible))

    def render(data: dict[str, Any]) -> None:
        stdout.print(f"Saved the frame at {data['timecode']} of {data['source']} as image "
                     f"{data['media_id']} (use it with --start-frame {data['media_id']})",
                     markup=False)
        _render_files({k: v for k, v in data.items() if k != "credits_spent"})

    emit(result, json_output=json_output, render=render)


def _adopt(_ctx: click.Context, _param: click.Parameter, values: tuple[str, ...]
           ) -> dict[str, str]:
    adopted: dict[str, str] = {}
    for value in values:
        shot, sep, media = value.partition("=")
        if not sep or not shot.strip() or not media.strip():
            raise click.BadParameter(f"{value!r} is not SHOT=MEDIA-ID", param_hint="--adopt")
        adopted[shot.strip()] = media.strip()
    return adopted


@flow.command("storyboard")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--keyframes", is_flag=True,
              help="Also make the free assets now (new characters, location plates, keyframes "
                   "on Nano Banana 2.1: 0 credits, but they are generated in your project) so "
                   "you can review them before paying. Without --keyframes or --max-credits "
                   "nothing is generated.")
@click.option("--max-credits", type=click.IntRange(0),
              help="Make the scene, spending at most this many credits in total; refused "
                   "before anything is made when the quotes add up to more. Without it "
                   "only the plan and the quotes are shown.")
@click.option("-o", "--out", type=click.Path(path_type=Path),
              help="Where to save the finished scene (an .mp4); needed with --max-credits.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--state", "state_path", type=click.Path(dir_okay=False, path_type=Path),
              help="State file (default: FILE with .state.json); it records what is made so a "
                   "re-run never pays twice.")
@click.option("--assets", "assets_dir", type=click.Path(file_okay=False, path_type=Path),
              help="Folder for the keyframes, plates, frames and clips (default: FILE.assets).")
@click.option("--adopt", multiple=True, callback=_adopt, metavar="SHOT=MEDIA-ID",
              help="Tell aistudio a shot's clip exists after an error that may have charged "
                   "(repeatable). Look it up with 'flow media --type video'.")
@click.option("--timeout", type=click.IntRange(30), default=1200, show_default=True,
              help="Seconds to wait for each clip after approving it.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def storyboard_cmd(state: CliState, file: Path, keyframes: bool, max_credits: int | None,
                   out: Path | None, force: bool, state_path: Path | None,
                   assets_dir: Path | None, adopt: dict[str, str], timeout: int, visible: bool,
                   json_output: bool) -> None:
    """Make a whole Flow scene from one YAML FILE: characters, location plates, keyframes and
    an ordered list of shots, joined into one video.

    The whole file is checked first. Without --max-credits it spends nothing: it shows each
    shot's plan and one total quote. With --max-credits N it makes the clips in order, stops at
    the first error, and keeps a state file so a re-run continues instead of paying again.
    See SKILL.md or the README for the file format."""
    state.require_browser()

    def progress(message: str) -> None:
        stderr.print(message, markup=False)
    result = asyncio.run(flow_storyboard(
        state.profile(), file, max_credits=max_credits, keyframes=keyframes, out=out,
        overwrite=force, state_path=state_path, assets_dir=assets_dir, adopt=adopt,
        timeout_s=float(timeout), visible=visible,
        on_progress=None if json_output else progress))
    emit(result, json_output=json_output, render=_render_storyboard)
    if result["status"] == "timeout":
        raise SystemExit(2)
    if result["status"] == "failed":
        raise SystemExit(1)


def _render_storyboard(data: dict[str, Any]) -> None:
    for shot in data["shots"]:
        quote = ("" if shot["quote"] is None
                 else f"  {shot['quote']} credits ({shot['quote_basis']})")
        length = "" if shot["duration"] is None else f" {shot['duration']} s"
        stdout.print(f"{shot['id']}  {shot['join']:6}  {shot['status']:8}  "
                     f"{shot['model']}{length} {shot['aspect']}  "
                     f"{' + '.join(shot['steps'])}{quote}", markup=False)
    for file in data.get("files", []):
        stdout.print(f"Wrote {file['path']}", markup=False)
    if data["status"] == "quoted":
        total = data["quote"]["credits"]
        stdout.print(f"{data['quote']['pending_shots']} shot(s) left, quoted at {total} "
                     f"credits in total. Nothing was spent. Re-run with --max-credits "
                     f"{total} -o scene.mp4 to make them; add --keyframes first to review "
                     f"the free keyframes.", markup=False)
    else:
        stdout.print(f"Spent {data['credits_spent']} credits in this run "
                     f"({data['credits_spent_total']} in all); {_left(data)}", markup=False)
    error = data.get("error")
    if error:
        stderr.print(f"Error: {error['message']}", markup=False)
        if error.get("hint"):
            stderr.print(f"Hint: {error['hint']}", markup=False)
    for warning in data.get("warnings", []):
        stderr.print(f"Warning: {warning}", markup=False)
