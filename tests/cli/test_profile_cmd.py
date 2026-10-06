import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_list_empty(aistudio_home):
    assert json.loads(run("profile", "list", "--json").stdout) == {
        "profiles": [], "count": 0, "current": "default",
    }


def test_create_list_switch_delete(logged_in_profile):
    logged_in_profile("work")
    assert run("profile", "create", "home", "--json").exit_code == 0
    data = json.loads(run("profile", "list", "--json").stdout)
    assert data["profiles"] == [
        {"name": "home", "current": False, "logged_in": False},
        {"name": "work", "current": False, "logged_in": True},
    ]
    assert run("profile", "switch", "work", "--json").exit_code == 0
    data = json.loads(run("profile", "list", "--json").stdout)
    assert data["current"] == "work"
    assert [p["current"] for p in data["profiles"]] == [False, True]
    assert run("profile", "delete", "work", "-y", "--json").exit_code == 0
    data = json.loads(run("profile", "list", "--json").stdout)
    assert [p["name"] for p in data["profiles"]] == ["home"]
    assert data["current"] == "default"


def test_create_existing_fails(aistudio_home):
    run("profile", "create", "home", "--json")
    result = run("profile", "create", "home", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_switch_missing_is_not_found(aistudio_home):
    result = run("profile", "switch", "ghost", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "NOT_FOUND"


def test_delete_requires_yes(aistudio_home):
    run("profile", "create", "home", "--json")
    result = run("profile", "delete", "home", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
