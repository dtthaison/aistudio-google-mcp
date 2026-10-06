import json

from click.testing import CliRunner

from aistudio._auth.login import LoginResult
from aistudio._auth.storage import AccountInfo
from aistudio.cli.main import cli


def test_login_command_passes_options(aistudio_home, monkeypatch):
    calls = {}

    async def fake_run_login(profile, **kwargs):
        calls.update(kwargs)
        return LoginResult(profile=profile.name, account=AccountInfo(), api_key_captured=True)

    monkeypatch.setattr("aistudio.cli.session_cmd.run_login", fake_run_login)
    result = CliRunner().invoke(
        cli, ["login", "--browser", "chromium", "--fresh", "--timeout", "30", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "profile": "default", "account": {"email": None, "authuser": 0}, "api_key_captured": True,
    }
    assert calls == {"browser": "chromium", "fresh": True, "timeout": 30}
