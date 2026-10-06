import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def test_star_and_unstar_commands(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []

    async def fake(client, profile, model_id, *, starred=True):
        calls.append((model_id, starred))
        return {"model": model_id, "starred": starred, "changed": True}

    monkeypatch.setattr("aistudio.cli.models_cmd.star_model", fake)
    star = CliRunner().invoke(cli, ["models", "star", "lyria-3.5", "--json"])
    unstar = CliRunner().invoke(cli, ["models", "unstar", "lyria-3.5"])
    assert star.exit_code == 0 and unstar.exit_code == 0, star.output + unstar.output
    assert json.loads(star.stdout)["starred"] is True
    assert "Unstarred lyria-3.5" in unstar.stdout
    assert calls == [("lyria-3.5", True), ("lyria-3.5", False)]


def test_list_warns_when_stars_are_unknown(logged_in_profile, monkeypatch):
    logged_in_profile()

    async def fake_list(client, profile, **kwargs):
        return {"models": [{"id": "m", "categories": [], "release_date": None, "command": None,
                            "pricing": [], "pricing_summary": None, "starred": None}],
                "count": 1}

    monkeypatch.setattr("aistudio.cli.models_cmd.list_models", fake_list)
    result = CliRunner().invoke(cli, ["models", "list"])
    assert result.exit_code == 0, result.output
    assert "could not read your starred models" in result.stderr
