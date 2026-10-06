import json

from click.testing import CliRunner

from aistudio.cli.main import cli
from aistudio.paths import get_profile


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_version():
    result = run("--version")
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_check_ok(logged_in_profile):
    logged_in_profile()
    result = run("auth", "check", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["profile"] == "default"
    assert data["checks"] == {"cookies_present": True, "api_key_present": True}
    assert data["account"] == {"email": None, "authuser": 1}
    assert data["warnings"] == []


def test_check_warns_without_api_key(logged_in_profile):
    logged_in_profile(api_key=None)
    result = run("auth", "check", "--json")
    data = json.loads(result.stdout)
    assert result.exit_code == 0
    assert data["checks"]["api_key_present"] is False
    assert data["warnings"] == ['no web API key captured; re-run "aistudio login"']


def test_check_not_logged_in(aistudio_home):
    result = run("auth", "check", "--json")
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["hint"] == 'run "aistudio login"'


def test_check_corrupt_storage_reports_error_not_traceback(aistudio_home):
    profile = get_profile("default").ensure()
    profile.storage_state.write_text("{not json")
    result = run("auth", "check", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"


def test_check_human_output(logged_in_profile):
    logged_in_profile()
    result = run("auth", "check")
    assert result.exit_code == 0
    assert "cookies_present: yes" in result.stdout


def test_profile_option_selects_profile(logged_in_profile):
    logged_in_profile("work")
    data = json.loads(run("-p", "work", "auth", "check", "--json").stdout)
    assert data["profile"] == "work"


def test_invalid_profile_name_is_validation_error(aistudio_home):
    result = run("-p", "../escape", "auth", "check", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_quiet_and_verbose_conflict(aistudio_home):
    assert run("--quiet", "-v", "auth", "check").exit_code == 2


def test_logout_requires_yes_in_json_mode(logged_in_profile):
    profile = logged_in_profile()
    result = run("auth", "logout", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
    assert profile.storage_state.exists()


def test_logout_deletes_credentials(logged_in_profile):
    profile = logged_in_profile()
    profile.chrome_dir.mkdir()
    result = run("auth", "logout", "-y", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "profile": "default", "removed": ["storage_state.json", "session.json", "chrome/"],
    }
    assert not profile.storage_state.exists()
    assert not profile.chrome_dir.exists()
