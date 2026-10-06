import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


@respx.mock
def test_replay_cli_json(logged_in_profile, write_rpc_fixture, tmp_path):
    logged_in_profile()
    write_rpc_fixture(tmp_path, "ListModels")
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(200, text="[]"))
    result = CliRunner().invoke(cli, ["dev", "replay", str(tmp_path), "--delay", "0", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["count"] == 1
    assert data["results"][0]["verdict"] == "http-ok"


def test_replay_cli_requires_login(aistudio_home, write_rpc_fixture, tmp_path):
    write_rpc_fixture(tmp_path, "ListModels")
    result = CliRunner().invoke(cli, ["dev", "replay", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "AUTH_ERROR"


@respx.mock
def test_replay_cli_table_shows_error_message(logged_in_profile, write_rpc_fixture, tmp_path):
    logged_in_profile()
    write_rpc_fixture(tmp_path, "ListModels")
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(403, text='[,[7,"denied"]]')
    )
    result = CliRunner().invoke(cli, ["dev", "replay", str(tmp_path), "--delay", "0"])
    assert result.exit_code == 0, result.output
    assert "denied" in result.stdout


@respx.mock
def test_replay_table_survives_markup_in_server_messages(logged_in_profile, write_rpc_fixture,
                                                         tmp_path):
    logged_in_profile()
    write_rpc_fixture(tmp_path, "ListModels")
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(400, text='[,[3,"bad [/] value"]]')
    )
    result = CliRunner().invoke(cli, ["dev", "replay", str(tmp_path), "--delay", "0"])
    assert result.exit_code == 0, result.output
    assert "bad [/] value" in result.stdout
