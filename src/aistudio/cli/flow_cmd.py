"""``aistudio flow``: Google Flow video and image generation with a credit budget (spec §3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.flow import (
    flow_character,
    flow_credits,
    flow_download,
    flow_extend,
    flow_generate,
    flow_media,
    flow_projects,
)
from aistudio._flow.models import ASPECTS, MODELS
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.inputs import read_text
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState


@click.group()
def flow() -> None:
    """Google Flow (flow.google.com): video and images, paid in Flow credits."""


@flow.command("credits")
@click.option("--visible", is_flag=True, help="Show the browser window instead of minimizing it.")
@json_option
@click.pass_obj
@handle_errors
def credits_cmd(state: CliState, visible: bool, json_output: bool) -> None:
    """How many Flow credits are left (reads the account menu; free)."""
    state.require_browser()
    result = asyncio.run(flow_credits(state.profile(), visible=visible))
    emit(result, json_output=json_output,
         render=lambda d: stdout.print(f"{d['credits']} Flow credits left", markup=False))


def _left(data: dict[str, Any]) -> str:
    left = data.get("credits_left")
    return "credits left unknown" if left is None else f"{left} left"


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
        stdout.print(f"Spent {data['credits_spent']} credits; {_left(data)}", markup=False)
    for warning in data.get("warnings", []):
        stderr.print(f"Warning: {warning}", markup=False)


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
                  "credits; without it Flow's quote is shown and nothing is spent.")
    @click.option("--project", help="Flow project id or link (default: an 'aistudio' project).")
    @click.option("-o", "--out", type=click.Path(path_type=Path),
                  help="Output file, or a folder for several outputs.")
    @click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
    @click.option("--timeout", type=click.IntRange(30), default=600, show_default=True,
                  help="Seconds to wait for Flow to finish after approving.")
    @click.option("--ingredient", multiple=True,
                  help="Reference image: a file or a Flow media id (repeatable).")
    @click.option("--i-have-rights", is_flag=True,
                  help="Confirm you have the rights to the local files you pass; Flow asks this "
                       "before any upload.")
    @click.option("--visible", is_flag=True, help="Show the browser window.")
    @json_option
    @click.pass_obj
    @handle_errors
    def command(state: CliState, prompt: str | None, prompt_file: Path | None, json_output: bool,
                timeout: int, **options: Any) -> None:
        state.require_browser()
        content = read_text(prompt, prompt_file, arg="PROMPT", option="--prompt-file")
        if not json_output:
            window = "visible" if options["visible"] else "minimized"
            stderr.print(f"Asking Flow in a {window} Chrome window…", markup=False)
        out = options.pop("out")
        options["ingredients"] = list(options.pop("ingredient"))
        result = asyncio.run(flow_generate(
            state.profile(), kind, content, out=out, overwrite=options.pop("force"),
            timeout_s=float(timeout), **{**{"duration": None}, **options}))
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
    help_text = (f"Generate {kind}s with Google Flow. Without --max-credits it only shows "
                 "Flow's quote and spends nothing.")
    return flow.command(kind, help=help_text)(command)


video = _command("video")
image = _command("image")


def _render_files(data: dict[str, Any]) -> None:
    if data.get("status") == "timeout":
        stdout.print(f"Still running in Flow ({data['credits_spent']} credits spent): "
                     f"{data['project_url']}", markup=False)
    for file in data.get("files", []):
        stdout.print(f"Wrote {file['path']} ({file['bytes']} bytes)", markup=False)
    if "credits_spent" in data and data.get("status") == "done":
        stdout.print(f"Spent {data['credits_spent']} credits; {_left(data)}", markup=False)
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
    emit(result, json_output=json_output, render=lambda d: [
        stdout.print(f"{p['id']}  {p['title'] or '(untitled)'}", markup=False)
        for p in d["projects"]])


@flow.command("media")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--type", "kind", type=click.Choice(["video", "image"]))
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def media_cmd(state: CliState, project: str | None, kind: str | None, visible: bool,
              json_output: bool) -> None:
    """Media in a Flow project, with ids for --start-frame, --ingredient and extend (free)."""
    state.require_browser()
    result = asyncio.run(flow_media(state.profile(), project=project, kind=kind, visible=visible))
    emit(result, json_output=json_output, render=lambda d: [
        stdout.print(f"{m['media_id']}  {m['kind']:5}  {m['status']:7}  {m['title'] or ''}",
                     markup=False) for m in d["media"]])


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
@click.argument("description")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--max-credits", type=click.IntRange(0),
              help="Needed only when aistudio's price table says the character costs credits.")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output image file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_create(state: CliState, description: str, project: str | None,
                     max_credits: int | None, out: Path | None, force: bool, visible: bool,
                     json_output: bool) -> None:
    """Create a character from DESCRIPTION (Flow charges without a quote; see the price table)."""
    state.require_browser()
    _finish(asyncio.run(flow_character(state.profile(), description, max_credits=max_credits,
                                       project=project, out=out, overwrite=force,
                                       visible=visible)), json_output)


@flow.command("extend")
@click.argument("media_id")
@click.argument("prompt")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("--max-credits", type=click.IntRange(0),
              help="Spend at most this many credits (extend with Veo 3.1 Lite costs 10).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output file for the new clip.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--timeout", type=click.IntRange(30), default=600, show_default=True)
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
