import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve_count(captured):
    return respx.post(method_url("CountTokens")).mock(
        return_value=httpx.Response(200, text=captured("CountTokens", 3).raw["response"]["body"])
    )


@respx.mock
def test_count_tokens_normalizes_model_and_matches_capture(logged_in_profile, captured):
    logged_in_profile()
    route = serve_count(captured)
    result = CliRunner().invoke(cli, [
        "count-tokens", "generate a cute house", "-m", "models/Gemini-3.1-Flash-Lite-Image",
        "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "model": "gemini-3.1-flash-lite-image", "total_tokens": 5, "by_modality": {"TEXT": 5},
    }
    sent = wire.decode(route.calls.last.request.content.decode())
    assert sent == captured("CountTokens", 3).request


@respx.mock
def test_count_tokens_reads_a_prompt_file_and_defaults_the_model(logged_in_profile, captured,
                                                                tmp_path):
    logged_in_profile()
    route = serve_count(captured)
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("hello")
    result = CliRunner().invoke(cli, ["count-tokens", "--prompt-file", str(prompt_file), "--json"])
    assert result.exit_code == 0, result.output
    assert wire.decode(route.calls.last.request.content.decode())[0] == "models/gemini-flash-latest"


def test_count_tokens_rejects_empty_or_double_input(logged_in_profile, tmp_path):
    logged_in_profile()
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("x")
    for args in (["count-tokens", "   "], ["count-tokens"],
                 ["count-tokens", "a", "--prompt-file", str(prompt_file)]):
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 1, args
        assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_count_tokens_rejects_a_binary_prompt_file(logged_in_profile, tmp_path):
    logged_in_profile()
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\xff\xfe\x00\x81")
    res = CliRunner().invoke(cli, ["count-tokens", "--prompt-file", str(binary), "-m",
                                   "gemini-flash-latest", "--json"])
    assert res.exit_code == 1, res.output
    assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"
