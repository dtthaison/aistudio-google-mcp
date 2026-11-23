import json
import logging
import sys
import types

import pytest
from click.testing import CliRunner

from aistudio._app import colab as colab_app
from aistudio.cli.main import cli


class FakeColab:
    """Stands in for the google-colab-cli package: records what each run saw."""

    def __init__(self, exit_code=0):
        self.runs = []
        self.exit_code = exit_code
        self.auth = types.ModuleType("colab_cli.auth")
        self.auth.KEYRING_USERNAME = "oauth_token"
        self.auth.LEGACY_TOKEN_PATH = "legacy.json"
        self.cli = types.ModuleType("colab_cli.cli")
        self.cli.app = self.app

    def app(self, args, prog_name):
        self.runs.append({"user": self.auth.KEYRING_USERNAME,
                          "legacy": self.auth.LEGACY_TOKEN_PATH,
                          "args": list(args), "prog": prog_name})
        raise SystemExit(self.exit_code)

    def account_and_args(self, i=0):
        run = self.runs[i]
        assert run["args"][0] == "--config"
        account = run["user"].removeprefix("oauth_token:")
        assert run["args"][1].endswith(f"sessions-{account}.json")
        return account, run["args"][2:]


@pytest.fixture
def fake(monkeypatch, aistudio_home, tmp_path):
    colab = FakeColab()
    package = types.ModuleType("colab_cli")
    package.auth, package.cli = colab.auth, colab.cli
    monkeypatch.setitem(sys.modules, "colab_cli", package)
    monkeypatch.setitem(sys.modules, "colab_cli.auth", colab.auth)
    monkeypatch.setitem(sys.modules, "colab_cli.cli", colab.cli)
    monkeypatch.setattr(colab_app, "config_dir", lambda: tmp_path / "colab-cli")
    monkeypatch.delenv("AISTUDIO_PROFILE", raising=False)
    return colab


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_the_profile_name_is_the_colab_account(fake, tmp_path):
    assert run("-p", "work", "colab", "usage").exit_code == 0
    assert fake.account_and_args() == ("work", ["usage"])
    assert fake.runs[0]["user"] == "oauth_token:work"
    assert fake.runs[0]["legacy"] == str(tmp_path / "colab-cli" / "token-work.json.unused")
    assert fake.runs[0]["prog"] == "colab"
    assert (tmp_path / "colab-cli").is_dir()


def test_the_library_globals_are_restored_after_a_run(fake):
    run("-p", "work", "colab", "usage")
    assert (fake.auth.KEYRING_USERNAME, fake.auth.LEGACY_TOKEN_PATH) == (
        "oauth_token", "legacy.json")


def test_the_default_profile_and_account_override(fake):
    run("colab", "sessions")
    run("-p", "work", "colab", "--account", "personal", "sessions")
    assert [fake.account_and_args(i)[0] for i in range(2)] == ["default", "personal"]


def test_colab_options_pass_through_untouched(fake):
    assert run("colab", "exec", "-s", "a", "--help-me", "print(1)").exit_code == 0
    assert fake.account_and_args()[1] == ["exec", "-s", "a", "--help-me", "print(1)"]


def test_no_args_shows_the_colab_help(fake):
    run("colab")
    assert fake.account_and_args()[1] == ["--help"]


@pytest.mark.parametrize("args", [["new", "--gpu", "T4"], ["run", "train.py"],
                                  ["--auth", "adc", "new"]])
def test_a_new_vm_needs_allow_spend(fake, args):
    result = run("colab", *args)
    assert result.exit_code == 1
    assert "compute units" in result.output
    assert fake.runs == []
    assert run("colab", "--allow-spend", *args).exit_code == 0
    assert fake.account_and_args()[1] == args


def test_help_for_a_paid_command_is_free(fake):
    assert run("colab", "new", "--help").exit_code == 0
    assert len(fake.runs) == 1


def test_the_colab_exit_code_comes_through(fake):
    fake.exit_code = 3
    assert run("colab", "status").exit_code == 3


def test_a_bad_account_name_is_refused(fake):
    result = run("colab", "--account", "../x", "usage")
    assert result.exit_code == 1
    assert fake.runs == []


def test_missing_colab_cli_says_so(monkeypatch, aistudio_home):
    monkeypatch.setitem(sys.modules, "colab_cli", None)  # makes the import fail
    result = run("colab", "usage")
    assert result.exit_code == 1
    assert "google-colab-cli" in result.output


def test_debug_logs_of_the_library_never_reach_aistudio_s_handlers(fake):
    # The real library sets the root logger to DEBUG and logs request headers (with the bearer
    # token); aistudio's own root handler must not print them.
    seen = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    root = logging.getLogger()
    mine = Catch()
    root.addHandler(mine)
    level, before = root.level, list(root.handlers)

    def app(args, prog_name):
        root.setLevel(logging.DEBUG)
        root.addHandler(logging.NullHandler())
        logging.getLogger("colab_cli.client").debug("authorization: Bearer secret")
        raise SystemExit(0)

    fake.cli.app = app
    try:
        assert run("colab", "usage").exit_code == 0
        assert seen == []
        assert root.level == level
        assert root.handlers == before
    finally:
        root.removeHandler(mine)


@pytest.fixture
def keychain(monkeypatch):
    """A fake keyring module: the accounts in the set have a stored token."""
    tokens = set()
    module = types.ModuleType("keyring")
    module.get_password = lambda service, user: (
        "token" if service == "google-colab-cli" and user.removeprefix("oauth_token:") in tokens
        else None)
    monkeypatch.setitem(sys.modules, "keyring", module)
    return tokens


def test_accounts_lists_profiles_and_session_only_accounts(fake, keychain, aistudio_home,
                                                           tmp_path):
    for name in ("work", "personal"):
        (aistudio_home / "profiles" / name).mkdir(parents=True)
    folder = tmp_path / "colab-cli"
    folder.mkdir()
    (folder / "sessions-lab.json").write_text('{"a": {}, "b": {}}')
    (folder / "sessions-default.json.lock").write_text("")
    (folder / "sessions.json.lock").write_text("")
    keychain.update({"work", "lab"})

    result = run("-p", "work", "colab", "accounts", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["current"] == "work"
    assert data["accounts"] == [
        {"name": "default", "current": False, "profile": False, "signed_in": False,
         "sessions": 0},
        {"name": "lab", "current": False, "profile": False, "signed_in": True, "sessions": 2},
        {"name": "personal", "current": False, "profile": True, "signed_in": False,
         "sessions": 0},
        {"name": "work", "current": True, "profile": True, "signed_in": True, "sessions": 0},
    ]
    assert fake.runs == []  # never handed to the Colab CLI

    text = run("-p", "work", "colab", "accounts").output
    assert "* work  (signed in, 0 sessions, profile)" in text
    assert "  lab  (signed in, 2 sessions, --account only)" in text


def test_accounts_with_no_keyring_says_unknown(fake, monkeypatch, aistudio_home):
    (aistudio_home / "profiles" / "work").mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "keyring", None)  # makes the import fail
    data = json.loads(run("colab", "accounts", "--json").output)
    assert data["accounts"][0]["signed_in"] is None


def test_accounts_refuses_other_arguments(fake):
    result = run("colab", "accounts", "rm")
    assert result.exit_code == 1
    assert fake.runs == []
