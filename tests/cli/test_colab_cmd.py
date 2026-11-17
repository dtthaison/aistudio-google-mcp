import json
import subprocess
import sys

import pytest
from click.testing import CliRunner

from aistudio._app import colab as colab_app
from aistudio.cli.main import cli


@pytest.fixture
def calls(monkeypatch, aistudio_home):
    seen = []

    def fake_call(command):
        seen.append(command)
        return 0

    monkeypatch.setattr("aistudio.cli.colab_cmd.subprocess.call", fake_call)
    monkeypatch.setenv(colab_app.PYTHON_ENV, "/opt/colab/bin/python")
    monkeypatch.delenv("AISTUDIO_PROFILE", raising=False)
    return seen


def run(*args):
    return CliRunner().invoke(cli, list(args))


def account_and_args(command):
    assert command[0] == "/opt/colab/bin/python"
    assert command[1:3] == ["-c", colab_app.BOOTSTRAP]
    return command[3], command[5:]


def test_the_profile_name_is_the_colab_account(calls):
    assert run("-p", "work", "colab", "usage").exit_code == 0
    assert account_and_args(calls[0]) == ("work", ["usage"])


def test_the_default_profile_and_account_override(calls):
    run("colab", "sessions")
    run("-p", "work", "colab", "--account", "personal", "sessions")
    assert [account_and_args(c)[0] for c in calls] == ["default", "personal"]


def test_colab_options_pass_through_untouched(calls):
    assert run("colab", "exec", "-s", "a", "--help-me", "print(1)").exit_code == 0
    assert account_and_args(calls[0])[1] == ["exec", "-s", "a", "--help-me", "print(1)"]


def test_no_args_shows_the_colab_help(calls):
    run("colab")
    assert account_and_args(calls[0])[1] == ["--help"]


@pytest.mark.parametrize("args", [["new", "--gpu", "T4"], ["run", "train.py"],
                                  ["--config=x", "new"]])
def test_a_new_vm_needs_allow_spend(calls, args):
    result = run("colab", *args)
    assert result.exit_code == 1
    assert "compute units" in result.output
    assert calls == []
    assert run("colab", "--allow-spend", *args).exit_code == 0
    assert account_and_args(calls[0])[1] == args


def test_help_for_a_paid_command_is_free(calls):
    assert run("colab", "new", "--help").exit_code == 0


def test_the_colab_exit_code_comes_through(monkeypatch, aistudio_home):
    monkeypatch.setenv(colab_app.PYTHON_ENV, "/opt/colab/bin/python")
    monkeypatch.setattr("aistudio.cli.colab_cmd.subprocess.call", lambda command: 3)
    assert run("colab", "status").exit_code == 3


def test_a_bad_account_name_is_refused(calls):
    result = run("colab", "--account", "../x", "usage")
    assert result.exit_code == 1
    assert calls == []


def test_missing_colab_cli_explains_how_to_install(monkeypatch, aistudio_home, tmp_path):
    monkeypatch.delenv(colab_app.PYTHON_ENV, raising=False)
    monkeypatch.setattr(colab_app.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(colab_app.shutil, "which", lambda name: None)
    monkeypatch.setattr(colab_app.Path, "home", lambda: tmp_path)
    result = CliRunner().invoke(cli, ["colab", "usage"])
    assert result.exit_code == 1
    assert "google-colab-cli" in result.output


def test_the_colab_command_s_python_is_found_from_its_shebang(monkeypatch, tmp_path):
    script = tmp_path / "colab"
    script.write_text("#!/venvs/colab/bin/python3\nprint()\n")
    monkeypatch.delenv(colab_app.PYTHON_ENV, raising=False)
    monkeypatch.setattr(colab_app.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(colab_app.shutil, "which", lambda name: str(script))
    assert colab_app.find_colab_python() == "/venvs/colab/bin/python3"


def test_bootstrap_points_the_colab_cli_at_one_account(tmp_path):
    # Run the real bootstrap against a stand-in colab_cli package.
    pkg = tmp_path / "pkgs" / "colab_cli"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "auth.py").write_text('KEYRING_USERNAME = "oauth_token"\nLEGACY_TOKEN_PATH = "x"\n')
    (pkg / "cli.py").write_text(
        "import json, sys\nimport colab_cli.auth as a\n"
        "def main():\n"
        "    print(json.dumps([a.KEYRING_USERNAME, a.LEGACY_TOKEN_PATH, sys.argv]))\n"
        "    return 0\n")
    config = tmp_path / "cfg"
    command = [sys.executable, "-c", colab_app.BOOTSTRAP, "work", str(config), "usage"]
    out = subprocess.run(command, capture_output=True, text=True, check=True,  # noqa: S603 (test)
                         env={"PYTHONPATH": str(tmp_path / "pkgs")}).stdout
    user, token, argv = json.loads(out)
    assert user == "oauth_token:work"
    assert token == str(config / "token-work.json.unused")
    assert argv == ["colab", "--config", str(config / "sessions-work.json"), "usage"]
    assert config.is_dir()


def test_the_colab_command_in_local_bin_is_found_off_path(monkeypatch, tmp_path):
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "colab").write_text("#!/venvs/colab/bin/python3\n")
    monkeypatch.delenv(colab_app.PYTHON_ENV, raising=False)
    monkeypatch.setattr(colab_app.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(colab_app.shutil, "which", lambda name: None)
    monkeypatch.setattr(colab_app.Path, "home", lambda: tmp_path)
    assert colab_app.find_colab_python() == "/venvs/colab/bin/python3"
