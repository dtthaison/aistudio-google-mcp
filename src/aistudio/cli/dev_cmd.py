"""``aistudio dev``: discovery tools for AI Studio's internal traffic (spec §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._dev.capture import CHECKLIST, DEFAULT_HOST_PATTERN, CaptureRecorder, run_capture
from aistudio._dev.fixtures import FixtureWriter
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
