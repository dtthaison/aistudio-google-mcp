"""Opt-in checks against the real speech page.

`uv run pytest -m live` lists voices headless (free).
`uv run pytest -m live_slow` also generates one short phrase in a minimized window (uses quota).
"""

import io
import os
import wave

import pytest

from aistudio._app.media import generate_speech, list_voices
from aistudio.paths import get_profile, resolve_profile_name


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.chrome_dir.exists():
        pytest.skip('no browser session; run "aistudio login"')
    return p


@pytest.mark.live
async def test_live_voices_include_the_default(profile):
    result = await list_voices(profile)
    assert "Fola" in result["voices"]
    assert result["count"] >= 20


@pytest.mark.live_slow
async def test_live_generate_speech_with_voice_and_style(profile, tmp_path):
    out = tmp_path / "live.wav"
    result = await generate_speech(profile, "Hello from the live test. <laugh>", voice="tova",
                                   style="warm and slow", out=out)
    assert result["voice"] == "Tova"
    with wave.open(io.BytesIO(out.read_bytes())) as w:
        assert w.getframerate() == 24000
        assert w.getnframes() / w.getframerate() > 0.5
