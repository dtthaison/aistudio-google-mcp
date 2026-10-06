import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve_catalog(captured):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )


@respx.mock
def test_models_list_by_category_json(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    result = CliRunner().invoke(cli, ["models", "list", "--category", "agents", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert {m["id"] for m in data["models"]} == {
        "antigravity-preview-09-2026", "antigravity-preview-05-2026",
        "deep-research-preview-04-2026", "deep-research-max-preview-04-2026",
    }
    assert all(m["command"] == "agent run" for m in data["models"])


@respx.mock
def test_models_list_human_output(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    result = CliRunner().invoke(cli, ["models", "list", "--category", "music"])
    assert result.exit_code == 0, result.output
    assert "lyria-3.5" in result.stdout


@respx.mock
def test_models_get_and_not_found(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    ok = CliRunner().invoke(cli, ["models", "get", "models/LYRIA-3.5", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["display_name"] == "Lyria 3.5"
    missing = CliRunner().invoke(cli, ["models", "get", "nope", "--json"])
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["code"] == "NOT_FOUND"


def test_browser_backend_is_refused(logged_in_profile):
    logged_in_profile()
    result = CliRunner().invoke(cli, ["--backend", "browser", "models", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_models_list_requires_login(aistudio_home):
    result = CliRunner().invoke(cli, ["models", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "AUTH_ERROR"


@respx.mock
def test_human_table_shows_full_ids_at_80_columns(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    result = CliRunner().invoke(cli, ["models", "list", "--category", "agents"])
    assert result.exit_code == 0, result.output
    for model_id in ("antigravity-preview-09-2026", "deep-research-max-preview-04-2026"):
        assert model_id in result.stdout
