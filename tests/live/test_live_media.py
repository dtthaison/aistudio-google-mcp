"""Opt-in real image and music generation. Both use quota and cost money (catalog prices on
2026-10-07: about $0.034 per image, $0.04 per Lyria clip). Run only with the user's OK:
`uv run pytest -m live_slow tests/live/test_live_media.py`.
"""

import os

import pytest

from aistudio._app.media import generate_image, generate_music
from aistudio.paths import get_profile, resolve_profile_name


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.chrome_dir.exists():
        pytest.skip('no browser session; run "aistudio login"')
    return p


@pytest.mark.live_slow
async def test_live_image(profile, tmp_path):
    result = await generate_image(profile, "A red apple on a white table, studio photo",
                                  out=tmp_path / "apple.jpg")
    file = result["files"][0]
    assert file["mime_type"].startswith("image/")
    assert (tmp_path / "apple.jpg").stat().st_size == file["bytes"] > 1000


@pytest.mark.live_slow
async def test_live_music_clip(profile, tmp_path):
    result = await generate_music(profile, "A calm solo piano melody, slow tempo",
                                  out=tmp_path / "piano.mp3")
    file = result["files"][0]
    assert file["mime_type"] == "audio/mpeg"
    assert (tmp_path / "piano.mp3").stat().st_size == file["bytes"] > 10000
    assert result["text"]
