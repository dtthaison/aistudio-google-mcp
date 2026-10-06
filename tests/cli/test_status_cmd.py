import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve(text):
    respx.post(method_url("ListIncidentsHistory")).mock(return_value=httpx.Response(200, text=text))


@respx.mock
def test_status_json_limits_and_counts(logged_in_profile, captured):
    logged_in_profile()
    serve(captured("ListIncidentsHistory", 6).raw["response"]["body"])
    data = json.loads(CliRunner().invoke(cli, ["status", "--limit", "3", "--json"]).stdout)
    assert data["count"] == 3 and data["active"] == 0 and data["total"] == 63
    assert data["incidents"][0]["id"] == "GeminiAPI-voice-replication-20261001"


@respx.mock
def test_status_human_says_all_clear_and_shows_titles(logged_in_profile, captured):
    logged_in_profile()
    serve(captured("ListIncidentsHistory", 6).raw["response"]["body"])
    result = CliRunner().invoke(cli, ["status", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "No active incidents" in result.stdout
    assert "Issues with AI Studio Billing" in result.stdout


@respx.mock
def test_status_active_only(logged_in_profile):
    logged_in_profile()
    active = ["x", "AI Studio [down]", 1, [[1, "t", ["1790865060"], "Looking into it"]], 1, [3]]
    serve(wire.encode([[[active]]]))
    result = CliRunner().invoke(cli, ["status", "--active"])
    assert result.exit_code == 0, result.output
    assert "AI Studio [down]" in result.stdout and "investigating" in result.stdout.lower()
