"""Map exceptions to the --json error envelope and exit codes (spec §6.8)."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

import click

from aistudio._app import spend as spend_log
from aistudio.cli.output import RESULT_KEY
from aistudio.cli.state import CliState
from aistudio.exceptions import AIStudioError, WaitTimeoutError

F = TypeVar("F", bound=Callable[..., Any])


def _report(envelope: dict[str, Any], json_output: bool) -> None:
    if json_output:
        click.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
        return
    click.echo(f"Error: {envelope['message']}", err=True)
    if envelope.get("hint"):
        click.echo(f"Hint: {envelope['hint']}", err=True)


def _record_spend(result: dict[str, Any] | None, error_code: str | None) -> None:
    """Add this run to the profile's spend log if it is a command that can spend. A log that
    cannot be written only warns: the command's result and exit code never change."""
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return
    path = " ".join(ctx.command_path.split()[1:])
    if path not in spend_log.SPENDING_COMMANDS:
        return
    try:
        state = ctx.find_object(CliState)
        if state is None:
            return
        profile = state.profile()
        entry = spend_log.build_entry(path, profile.name, ctx.params, result, error_code)
        if entry is not None:
            spend_log.append_entry(profile, entry)
    except Exception as exc:
        click.echo(f"Warning: could not write the spend log: {exc}", err=True)


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, AIStudioError):
        return exc.code
    return "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "UNEXPECTED_ERROR"


def _spend_logged(func: F) -> F:
    """Log the run once it ends: from the result the command printed, or from the error that
    ended it (a failed or interrupted paid run matters most)."""
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = click.get_current_context(silent=True)
        try:
            out = func(*args, **kwargs)
        except SystemExit:  # exit 2 after a timeout result was printed
            _record_spend(ctx.meta.get(RESULT_KEY) if ctx else None, None)
            raise
        except (click.exceptions.Exit, click.ClickException, click.Abort):
            raise
        except BaseException as exc:
            _record_spend(None, _error_code(exc))
            raise
        _record_spend(ctx.meta.get(RESULT_KEY) if ctx else None, None)
        return out

    return wrapper  # type: ignore[return-value]


def handle_errors(func: F) -> F:
    func = _spend_logged(func)

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
