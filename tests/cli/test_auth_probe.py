import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


@respx.mock
def test_check_test_probes_listmodels(logged_in_profile, captured):
    logged_in_profile()
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )
    result = CliRunner().invoke(cli, ["auth", "check", "--test", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["checks"]["http_probe"] is True
    assert data["probe"] == {"ok": True, "models": 42}


@respx.mock
def test_check_test_reports_rejected_cookies(logged_in_profile):
    logged_in_profile()
    route = respx.post(method_url("ListModels")).mock(return_value=httpx.Response(401))
    result = CliRunner().invoke(cli, ["auth", "check", "--test", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["checks"]["http_probe"] is False
    assert data["probe"]["code"] == "AUTH_ERROR"
    assert data["hint"] == 'run "aistudio login"'
    assert route.call_count == 1


def test_check_without_test_makes_no_request(logged_in_profile):
    logged_in_profile()
    data = json.loads(CliRunner().invoke(cli, ["auth", "check", "--json"]).stdout)
    assert "http_probe" not in data["checks"]


def test_check_test_skips_the_probe_when_not_logged_in(aistudio_home):
    result = CliRunner().invoke(cli, ["auth", "check", "--test", "--json"])
    data = json.loads(result.stdout)
    assert result.exit_code == 1
    assert data["checks"]["http_probe"] is False
    assert "probe" not in data


@respx.mock
def test_check_test_human_output_says_why_the_probe_failed(logged_in_profile):
    logged_in_profile()
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(401))
    result = CliRunner().invoke(cli, ["auth", "check", "--test"])
    assert result.exit_code == 1
    assert "AUTH_ERROR" in result.stdout
    assert "HTTP 401" in result.stdout
