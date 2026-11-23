import json

from click.testing import CliRunner

from aistudio.cli.main import cli
from aistudio.paths import get_profile


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_profile_browser_shows_the_default(aistudio_home):
    result = run("profile", "browser", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"profile": "default", "browser": "chrome"}


def test_profile_browser_sets_shows_and_resets(aistudio_home):
    result = run("-p", "work", "profile", "browser", "shardx:acct1", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "profile": "work", "browser": "shardx:acct1", "saved": True}
    assert json.loads(get_profile("work").browser_json.read_text()) == {"browser": "shardx:acct1"}
    shown = run("-p", "work", "profile", "browser")
    assert "shardx:acct1" in shown.output
    # another profile is unaffected
    assert json.loads(run("profile", "browser", "--json").stdout)["browser"] == "chrome"
    reset = run("-p", "work", "profile", "browser", "--reset", "--json")
    assert json.loads(reset.stdout) == {"profile": "work", "browser": "chrome", "reset": True}
    assert not get_profile("work").browser_json.exists()


def test_profile_browser_rejects_a_bad_spec_and_both_arguments(aistudio_home):
    assert run("profile", "browser", "shardx:").exit_code != 0
    assert run("profile", "browser", "firefox").exit_code != 0
    assert run("profile", "browser", "chrome", "--reset").exit_code != 0
    assert not get_profile("default").browser_json.exists()


def test_login_rejects_a_bad_browser_spec(aistudio_home):
    result = run("login", "--browser", "shardx:")
    assert result.exit_code != 0


def test_changing_the_browser_forgets_the_old_logins_files_only(aistudio_home, logged_in_profile):
    profile = logged_in_profile("work")
    profile.chrome_dir.mkdir()
    result = run("-p", "work", "profile", "browser", "shardx:acct1", "--json")
    data = json.loads(result.stdout)
    assert sorted(data["cleared"]) == ["session.json", "storage_state.json"]
    assert "aistudio -p work login" in data["hint"]
    assert not profile.storage_state.exists() and not profile.session.exists()
    assert profile.chrome_dir.exists()  # Chrome's own folder is not touched


def test_setting_the_same_browser_again_keeps_the_login(aistudio_home, logged_in_profile):
    profile = logged_in_profile("work")
    run("-p", "work", "profile", "browser", "shardx:acct1")
    logged_in_profile("work")
    data = json.loads(run("-p", "work", "profile", "browser", "shardx:acct1", "--json").stdout)
    assert "cleared" not in data and profile.storage_state.exists()


def test_reset_forgets_the_login_of_the_attached_browser(aistudio_home, logged_in_profile):
    profile = logged_in_profile("work")
    run("-p", "work", "profile", "browser", "shardx:acct1")
    logged_in_profile("work")
    result = run("-p", "work", "profile", "browser", "--reset")
    assert "Forgot the saved login" in result.output and not profile.storage_state.exists()
