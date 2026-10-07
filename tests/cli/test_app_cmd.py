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


@respx.mock
def test_gallery_get_human_output_keeps_bracketed_slug(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListBundledApplets", 3)
    result = CliRunner().invoke(cli, ["gallery", "get", "store"])
    assert result.exit_code == 0, result.output
    assert "[store-locator]" in result.stdout


@respx.mock
def test_app_list_survives_markup_in_titles(logged_in_profile):
    from aistudio._rpc import wire

    logged_in_profile()
    apps = [[None, [f"id-{i}", ["1791263136", 0], ["1791263129", 0], [title, "", "p"]]]
            for i, title in enumerate(["Fix [/] bug", "My [store-locator] app"])]
    respx.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([apps]))
    )
    result = CliRunner().invoke(cli, ["app", "list"])
    assert result.exit_code == 0, result.output
    assert "Fix [/] bug" in result.stdout
    assert "My [store-locator] app" in result.stdout


@respx.mock
def test_app_list_shows_skipped_entry_warnings(logged_in_profile, captured):
    logged_in_profile()
    response = captured("ListApplets", 4).response
    response[0] = [[None, [None, None, None, ["no id"]]], *response[0]]
    respx.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=json.dumps(response)))
    res = CliRunner().invoke(cli, ["app", "list"])
    assert res.exit_code == 0, res.output
    assert "Warning: skipped 1 app entry" in res.stderr
