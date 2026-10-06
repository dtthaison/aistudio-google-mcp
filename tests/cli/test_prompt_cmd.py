import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


@respx.mock
def test_prompt_list_filters_by_type(logged_in_profile, captured):
    logged_in_profile()
    route = respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(200, text=captured("ListPrompts", 4).raw["response"]["body"])
    )
    result = CliRunner().invoke(cli, ["prompt", "list", "--type", "video", "--limit", "50",
                                      "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["count"] == 7
    assert {p["type"] for p in data["prompts"]} == {"video"}
    assert route.calls.last.request.content == b"[50]"


@respx.mock
def test_prompt_list_human_output(logged_in_profile, captured):
    logged_in_profile()
    respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(200, text=captured("ListPrompts", 4).raw["response"]["body"])
    )
    result = CliRunner().invoke(cli, ["prompt", "list", "--type", "chat"])
    assert result.exit_code == 0, result.output
    assert "prompt_391" in result.stdout
