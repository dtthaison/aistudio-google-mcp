import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve(captured, method, n=1):
    respx.post(method_url(method)).mock(
        return_value=httpx.Response(200, text=captured(method, n).raw["response"]["body"])
    )


@respx.mock
def test_app_list_and_get(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListApplets", 4)
    serve(captured, "ListAppletTrajectories")
    listed = CliRunner().invoke(cli, ["app", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["count"] == 4
    shown = CliRunner().invoke(cli, ["app", "get", "28a7"])
    assert shown.exit_code == 0, shown.output
    assert "FluentFlow" in shown.stdout and "leaning english" in shown.stdout


@respx.mock
def test_app_recent_human(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListRecentApplets", 7)
    result = CliRunner().invoke(cli, ["app", "recent"])
    assert result.exit_code == 0, result.output
    assert "pixshop" in result.stdout


@respx.mock
def test_gallery_list_and_get(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListBundledApplets", 3)
    listed = CliRunner().invoke(cli, ["gallery", "list", "--group", "template", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["count"] == 14
    missing = CliRunner().invoke(cli, ["gallery", "get", "no-such-app", "--json"])
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["code"] == "NOT_FOUND"


def test_build_reads_refuse_the_browser_backend(logged_in_profile):
    logged_in_profile()
    result = CliRunner().invoke(cli, ["--backend", "browser", "app", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
