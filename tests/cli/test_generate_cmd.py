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


IMAGE = {"files": [{"path": "apple.jpg", "mime_type": "image/jpeg", "bytes": 1234}],
         "model": "gemini-3.1-flash-lite-image"}
MUSIC = {"files": [{"path": "song.mp3", "mime_type": "audio/mpeg", "bytes": 5678}],
         "model": "lyria-3-clip-preview", "text": "<instrumental>"}


def test_generate_image_passes_options(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_image", fake(IMAGE, calls))
    out = tmp_path / "apple.jpg"
    res = CliRunner().invoke(cli, ["generate", "image", "a red apple", "-m",
                                   "gemini-nano-banana-2.1", "-o", str(out), "--force",
                                   "--visible"])
    assert res.exit_code == 0, res.output
    assert "Wrote apple.jpg (1234 bytes)" in res.stdout
    assert "visible Chrome window" in res.stderr
    assert calls[0] == ("default", ("a red apple",),
                        {"model": "gemini-nano-banana-2.1", "out": out, "overwrite": True,
                         "visible": True})


def test_generate_music_reads_a_prompt_file(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_music", fake(MUSIC, calls))
    prompt = tmp_path / "p.txt"
    prompt.write_text("calm piano, `not run`")
    res = CliRunner().invoke(cli, ["generate", "music", "--prompt-file", str(prompt)])
    assert res.exit_code == 0, res.output
    assert calls[0][1] == ("calm piano, `not run`",)
    assert calls[0][2]["model"] == "lyria-3-clip-preview"
    assert "Wrote song.mp3 (5678 bytes)" in res.stdout
    assert "Text: <instrumental>" in res.stdout
    assert "minimized Chrome window" in res.stderr


def test_generate_music_json_output(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_music", fake(MUSIC, []))
    res = CliRunner().invoke(cli, ["generate", "music", "piano", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == MUSIC
    assert res.stderr == ""


def test_prompt_needs_exactly_one_source(logged_in_profile, tmp_path):
    logged_in_profile()
    prompt = tmp_path / "p.txt"
    prompt.write_text("x")
    for kind in ("image", "music"):
        for args in ([], ["a", "--prompt-file", str(prompt)]):
            res = CliRunner().invoke(cli, ["generate", kind, *args, "--json"])
            assert res.exit_code == 1, res.output
            assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_binary_or_huge_text_files_are_validation_errors(logged_in_profile, tmp_path):
    logged_in_profile()
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\xff\xfe\x00\x81")
    huge = tmp_path / "h.txt"
    huge.write_text("x" * (1024 * 1024 + 1))
    for args in (["speech", "--text-file", str(binary)], ["image", "--prompt-file", str(binary)],
                 ["music", "--prompt-file", str(huge)]):
        res = CliRunner().invoke(cli, ["generate", *args, "--json"])
        assert res.exit_code == 1, res.output
        assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"
