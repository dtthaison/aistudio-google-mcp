"""``aistudio flow``: Google Flow video and image generation with a credit budget (spec §3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.flow import (
    flow_character,
    flow_character_body,
    flow_characters,
    flow_credits,
    flow_download,
    flow_edit,
    flow_extend,
    flow_frame,
    flow_generate,
    flow_media,
    flow_projects,
    flow_rename_character,
    flow_scene_download,
    flow_scenes,
    flow_set_personality,
    flow_stitch,
)
from aistudio._app.flow_tools import (
    flow_tool_create,
    flow_tool_edit,
    flow_tool_export,
    flow_tool_get,
    flow_tool_use,
    flow_tools,
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
    help_text = (f"Generate {kind}s with Google Flow. Without --max-credits a paid model only "
                 "shows Flow's quote and spends nothing"
                 + ("; the free Nano Banana 2.1 generates at once (0 credits)." if kind == "image"
                    else "."))
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
    emit(result, json_output=json_output, render=lambda d: [
        stdout.print(f"{c['id']}  {c['name'] or '(unnamed)'}"
                     f"{'' if c['media_id'] else '  (no image)'}",
                     markup=False) for c in d["characters"]])


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
@click.argument("prompt")
@click.option("--max-credits", type=click.IntRange(0),
              help="Needed only when aistudio's price table says the body costs credits.")
@click.option("--project", help="Flow project id or link (default: the 'aistudio' project).")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="Output image file.")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--visible", is_flag=True, help="Show the browser window.")
@json_option
@click.pass_obj
@handle_errors
def character_body(state: CliState, character_id: str, prompt: str, max_credits: int | None,
                   project: str | None, out: Path | None, force: bool, visible: bool,
                   json_output: bool) -> None:
    """A full-body image for a character, described by PROMPT ("Create body"; 0 credits on
    Nano Banana 2.1, Flow charges without a quote)."""
    state.require_browser()
    _finish(asyncio.run(flow_character_body(state.profile(), character_id, prompt,
                                            max_credits=max_credits, project=project, out=out,
                                            overwrite=force, visible=visible)), json_output)


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
    emit(result, json_output=json_output, render=lambda d: [
        stdout.print(f"{s['id']}  {s['title'] or '(untitled)'}  {len(s['clips'])} clip(s), "
                     f"{sum(c['seconds'] for c in s['clips'])} s", markup=False)
        for s in d["scenes"]])


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
    emit(result, json_output=json_output, render=lambda d: [
        stdout.print(f"{t['id']}  [{t['kind']}]  {t['name']}", markup=False)
        for t in d["tools"]])


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
