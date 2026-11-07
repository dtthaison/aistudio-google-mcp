import json

import httpx
import pytest
import respx
from click.testing import CliRunner

from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.cli.main import cli

LONG = "A very long line of prompt text that must never be cut " * 12


@pytest.fixture
def served(logged_in_profile, captured, prompt_wire):
    """ListPrompts (fake ids) plus a GetPrompt reply for prompt_391."""
    w = prompt_wire
    logged_in_profile()
    body = w.prompt(
        [w.chunk(None, inline="image/png"), w.chunk(LONG, at=1778571110),
         w.chunk("Thinking out loud", role="model", thought=True),
         w.chunk("The answer [with brackets]", role="model")],
        name="prompts/prompt_391", title="Plan [draft]", system="You are terse.",
        run=w.settings(temperature=0.7),
    )
    with respx.mock:
        respx.post(method_url("ListPrompts")).mock(return_value=httpx.Response(
            200, text=captured("ListPrompts", 4).raw["response"]["body"]))
        respx.post(method_url("GetPrompt")).mock(
            return_value=httpx.Response(200, text=wire.encode(body)))
        yield


def test_prompt_get_prints_everything_untruncated(served):
    result = CliRunner().invoke(cli, ["prompt", "get", "prompt_391"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Plan [draft]" in out and "prompt_391" in out
    assert "Model: gemini-test" in out and "Temperature: 0.7" in out
    assert "System instructions" in out and "You are terse." in out
    assert LONG in out  # one 600-character line, not wrapped or cut
    assert "image/png" in out
    assert "The answer [with brackets]" in out
    assert "Thinking out loud" not in out


def test_prompt_get_thoughts_flag_shows_thinking(served):
    result = CliRunner().invoke(cli, ["prompt", "get", "prompt_391", "--thoughts"])
    assert result.exit_code == 0, result.output
    assert "Thinking out loud" in result.stdout


def test_prompt_get_json(served):
    result = CliRunner().invoke(cli, ["prompt", "get", "prompt_39", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["id"] == "prompt_391" and data["system_instruction"] == "You are terse."
    assert data["settings"]["temperature"] == 0.7
    assert [t["role"] for t in data["turns"]] == ["user", "model"]
    assert data["turns"][0]["attachments"] == [{"source": "inline", "id": None,
                                                "mime": "image/png"}]
    assert data["turns"][1]["thoughts"] == "Thinking out loud"
    assert "QUJD" not in result.stdout


def test_prompt_get_saves_markdown_and_refuses_to_overwrite(served, tmp_path):
    target = tmp_path / "p.md"
    result = CliRunner().invoke(cli, ["prompt", "get", "prompt_391", "-o", str(target)])
    assert result.exit_code == 0, result.output
    assert str(target) in result.stdout
    text = target.read_text()
    assert text.startswith("# Plan [draft]\n") and LONG in text
    again = CliRunner().invoke(cli, ["prompt", "get", "prompt_391", "-o", str(target)])
    assert again.exit_code == 1 and "--force" in again.output
    forced = CliRunner().invoke(cli, ["prompt", "get", "prompt_391", "-o", str(target),
                                      "--force", "--json"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.stdout)["saved_to"] == str(target)


def test_prompt_get_ambiguous_prefix_is_an_error(served):
    result = CliRunner().invoke(cli, ["prompt", "get", "prompt_"])
    assert result.exit_code == 1 and "matches several prompts" in result.output


def test_prompt_get_help_mentions_the_id_prefix():
    result = CliRunner().invoke(cli, ["prompt", "get", "--help"])
    assert "unique prefix" in " ".join(result.output.split())
