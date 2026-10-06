"""Output helpers: one JSON document on stdout for --json, rich text otherwise."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import click
from rich.console import Console
from rich.text import Text

from aistudio.exceptions import ValidationError

stdout = Console()
stderr = Console(stderr=True)

json_option = click.option(
    "--json", "json_output", is_flag=True, help="Print machine-readable JSON."
)


def cells(*values: str) -> list[Text]:
    """Table cells rendered literally: titles and server messages may contain [brackets]."""
    return [Text(value) for value in values]


def emit(data: Any, *, json_output: bool, render: Callable[[Any], None]) -> None:
    if json_output:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        render(data)


def confirm_or_fail(prompt: str, *, yes: bool, json_output: bool) -> None:
    """Require -y in non-interactive use; ask on a TTY otherwise."""
    if yes:
        return
    if json_output or not sys.stdin.isatty():
        raise ValidationError(f"{prompt} (confirmation required)", hint="pass -y/--yes")
    if not click.confirm(prompt, default=False):
        raise click.Abort()
