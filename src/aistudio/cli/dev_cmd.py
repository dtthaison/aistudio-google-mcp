"""``aistudio dev``: discovery tools for AI Studio's internal traffic (spec §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from aistudio._auth.session import Session
from aistudio._dev.capture import CHECKLIST, DEFAULT_HOST_PATTERN, CaptureRecorder, run_capture
from aistudio._dev.fixtures import FixtureWriter
from aistudio._dev.replay import replay_all
from aistudio._transport.http import HttpTransport
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState


@click.group()
def dev() -> None:
    """Discovery tools for reverse-engineering AI Studio traffic."""


def _render_capture(data: dict[str, Any]) -> None:
    stdout.print(f"{data['fixtures']} fixtures written to {data['out']}")
    for method, count in data["by_method"].items():
        stdout.print(f"  {method}: {count}")


@dev.command("capture")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path),
              default=Path("tests/fixtures"), show_default=True, help="Fixture directory.")
@click.option("--browser", type=click.Choice(["chrome", "chromium", "msedge"]), default="chrome",
              show_default=True)
@click.option("--host-pattern", default=DEFAULT_HOST_PATTERN, show_default=True,
              help="Regex of request hosts to record.")
@json_option
@click.pass_obj
@handle_errors
def capture(state: CliState, out: Path, browser: str, host_pattern: str,
            json_output: bool) -> None:
    """Record AI Studio traffic, redacted, while you use the UI."""
    profile = state.profile()
    if not json_output:
        for line in CHECKLIST:
            stderr.print(line)
    recorder = CaptureRecorder(
        FixtureWriter(out),
        host_pattern=host_pattern,
        on_write=None if json_output else lambda p, m: stderr.print(f"  recorded {m} -> {p}"),
    )
    try:
        asyncio.run(run_capture(profile, recorder, browser=browser))
    except KeyboardInterrupt:
        pass
    data = {
        "out": str(out),
        "fixtures": sum(recorder.counts.values()),
        "by_method": dict(sorted(recorder.counts.items())),
    }
    emit(data, json_output=json_output, render=_render_capture)


def _render_replay(data: dict[str, Any]) -> None:
    table = Table("fixture", "verdict", "status", "code", "message")
    for r in data["results"]:
        fixture = Path(r["fixture"])
        table.add_row(f"{fixture.parent.name}/{fixture.name}", r["verdict"],
                      str(r["http_status"] or ""), r["error_code"] or "", r["message"] or "")
    stdout.print(table)


@dev.command("replay")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--allow-mutating", is_flag=True,
              help="Also send RPCs that create, change, delete or generate "
                   "(may spend quota or money).")
@click.option("--delay", type=click.FloatRange(min=0), default=0.5, show_default=True,
              help="Seconds to wait between sent requests.")
@json_option
@click.pass_obj
@handle_errors
def replay(state: CliState, target: Path, allow_mutating: bool, delay: float,
           json_output: bool) -> None:
    """Re-send captured fixtures over plain HTTP and report which ones still work."""
    session = Session.load(state.profile())

    async def run() -> list[Any]:
        async with HttpTransport(session) as transport:
            return await replay_all(target, transport, allow_mutating=allow_mutating, delay=delay)

    results = asyncio.run(run())
    data = {"results": [r.to_dict() for r in results], "count": len(results)}
    emit(data, json_output=json_output, render=_render_replay)
