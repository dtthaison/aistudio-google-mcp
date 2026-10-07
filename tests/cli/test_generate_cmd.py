import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def fake(result, calls):
    async def run(profile, *args, **kwargs):
        calls.append((profile.name, args, kwargs))
        return result

    return run


RESULT = {"files": [{"path": "a.wav", "mime_type": "audio/wav", "seconds": 1.5}],
          "model": "gemini-3.8-flash-tts", "voice": "Tova", "style": "warm"}


def test_generate_speech_passes_options(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_speech", fake(RESULT, calls))
    out = tmp_path / "a.wav"
    res = CliRunner().invoke(cli, ["generate", "speech", "Hi <laugh>", "--voice", "tova",
                                   "--style", "warm", "-o", str(out), "--force", "--visible",
                                   "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == RESULT
    name, args, kwargs = calls[0]
    assert (name, args) == ("default", ("Hi <laugh>",))
    assert kwargs == {"model": "gemini-3.8-flash-tts", "voice": "tova", "style": "warm",
                      "out": out, "overwrite": True, "visible": True}


def test_generate_speech_reads_a_text_file(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_speech", fake(RESULT, calls))
    script = tmp_path / "script.txt"
    script.write_text("Line one. <gasp> Line two.")
    res = CliRunner().invoke(cli, ["generate", "speech", "--text-file", str(script)])
    assert res.exit_code == 0, res.output
    assert calls[0][1] == ("Line one. <gasp> Line two.",)
    assert "Wrote a.wav" in res.stdout


def test_generate_speech_needs_exactly_one_text_source(logged_in_profile, tmp_path):
    logged_in_profile()
    script = tmp_path / "s.txt"
    script.write_text("x")
    for args in (["generate", "speech"], ["generate", "speech", "a", "--text-file", str(script)]):
        res = CliRunner().invoke(cli, [*args, "--json"])
        assert res.exit_code == 1
        assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_http_backend_cannot_generate(logged_in_profile):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["--backend", "http", "generate", "speech", "hi", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_voices_list(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.list_voices",
                        fake({"voices": ["Fola", "Tova"], "count": 2}, calls))
    res = CliRunner().invoke(cli, ["voices", "list", "--search", "o"])
    assert res.exit_code == 0, res.output
    assert "Fola, Tova" in res.stdout
    assert calls[0][2] == {"search": "o"}
