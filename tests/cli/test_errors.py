import json

import click
from click.testing import CliRunner

from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import json_option
from aistudio.exceptions import NotFoundError, WaitTimeoutError


def make_command(exc):
    @click.command()
    @json_option
    @handle_errors
    def boom(json_output):
        raise exc

    return boom


def test_app_error_prints_json_envelope_and_exits_1():
    cmd = make_command(NotFoundError("no such prompt", hint="check the id"))
    result = CliRunner().invoke(cmd, ["--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": True, "code": "NOT_FOUND", "message": "no such prompt", "hint": "check the id",
    }


def test_app_error_without_json_goes_to_stderr():
    result = CliRunner().invoke(make_command(NotFoundError("no such prompt", hint="check")), [])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: no such prompt" in result.stderr
    assert "Hint: check" in result.stderr


def test_wait_timeout_exits_2():
    result = CliRunner().invoke(make_command(WaitTimeoutError("too slow")), ["--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["code"] == "TIMEOUT"


def test_unexpected_exception_still_returns_json_and_exits_2():
    result = CliRunner().invoke(make_command(RuntimeError("kaboom")), ["--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": True, "code": "UNEXPECTED_ERROR", "message": "RuntimeError: kaboom",
    }


def test_keyboard_interrupt_exits_130():
    result = CliRunner().invoke(make_command(KeyboardInterrupt()), ["--json"])
    assert result.exit_code == 130
    assert json.loads(result.stdout)["code"] == "INTERRUPTED"
