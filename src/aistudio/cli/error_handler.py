"""Map exceptions to the --json error envelope and exit codes (spec §6.8)."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

import click

from aistudio.exceptions import AIStudioError, WaitTimeoutError

F = TypeVar("F", bound=Callable[..., Any])


def _report(envelope: dict[str, Any], json_output: bool) -> None:
    if json_output:
        click.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
        return
    click.echo(f"Error: {envelope['message']}", err=True)
    if envelope.get("hint"):
        click.echo(f"Hint: {envelope['hint']}", err=True)


def handle_errors(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        json_output = bool(kwargs.get("json_output"))
        try:
            return func(*args, **kwargs)
        except (click.exceptions.Exit, click.ClickException, click.Abort):
            raise
        except KeyboardInterrupt:
            _report({"error": True, "code": "INTERRUPTED", "message": "interrupted"}, json_output)
            raise SystemExit(130) from None
        except AIStudioError as exc:
            _report(exc.to_dict(), json_output)
            raise SystemExit(2 if isinstance(exc, WaitTimeoutError) else 1) from None
        except Exception as exc:
            _report(
                {"error": True, "code": "UNEXPECTED_ERROR",
                 "message": f"{type(exc).__name__}: {exc}"},
                json_output,
            )
            raise SystemExit(2) from None

    return wrapper  # type: ignore[return-value]
