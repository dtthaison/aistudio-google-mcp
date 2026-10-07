"""``aistudio flow``: Google Flow video and image generation with a credit budget (spec §3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.flow import flow_credits, flow_generate
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


def _render(data: dict[str, Any]) -> None:
    if data["status"] == "quoted":
        stdout.print(f"Flow quotes {data['credits']} credits for {data['count']} output(s): "
                     f"{data['plan']}", markup=False)
        stdout.print(f"Nothing was spent. Re-run with --max-credits {data['credits']} to "
                     "generate.", markup=False)
    elif data["status"] == "timeout":
        stdout.print(f"Still running in Flow ({data['credits_spent']} credits spent): "
                     f"{data['project_url']}", markup=False)
    else:
        for file in data["files"]:
            stdout.print(f"Wrote {file['path']} ({file['bytes']} bytes)", markup=False)
        stdout.print(f"Spent {data['credits_spent']} credits; {data['credits_left']} left",
                     markup=False)
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
        result = asyncio.run(flow_generate(
            state.profile(), kind, content, out=out, overwrite=options.pop("force"),
            timeout_s=float(timeout), **{**{"duration": None}, **options}))
        emit(result, json_output=json_output, render=_render)
        if result["status"] == "timeout":
            raise SystemExit(2)

    if kind == "video":
        command = click.option("--duration", type=int, help="Seconds (per the model: Veo "
                               "3.1 Lite/Fast 4/6/8, Quality 8, Omni 4/6/8/10).")(command)
    help_text = (f"Generate {kind}s with Google Flow. Without --max-credits it only shows "
                 "Flow's quote and spends nothing.")
    return flow.command(kind, help=help_text)(command)


video = _command("video")
image = _command("image")
