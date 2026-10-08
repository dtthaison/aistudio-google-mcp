"""Opt-in checks against the real Google Flow (needs the saved session).

`uv run pytest -m live tests/live/test_live_flow.py` is free: it reads credits and gets a quote
that is then rejected. `-m live_slow` makes one Veo 3.1 Lite video (about 10 credits); run it
only with the user's OK.
"""

import os

import pytest

from aistudio._app.flow import (
    flow_character,
    flow_credits,
    flow_extend,
    flow_generate,
    flow_media,
    flow_projects,
)
from aistudio.exceptions import OverBudgetError
from aistudio.paths import get_profile, resolve_profile_name


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.chrome_dir.exists():
        pytest.skip('no browser session; run "aistudio login"')
    return p


@pytest.mark.live
async def test_live_credits(profile):
    assert (await flow_credits(profile))["credits"] >= 0


@pytest.mark.live
async def test_live_quote_then_reject_spends_nothing(profile, tmp_path):
    before = (await flow_credits(profile))["credits"]
    with pytest.raises(OverBudgetError):
        await flow_generate(profile, "video", "a red apple rolling on a table",
                            model="veo-3.1-lite", duration=8, max_credits=0,
                            out=tmp_path / "x.mp4")
    assert (await flow_credits(profile))["credits"] == before


@pytest.mark.live_slow
async def test_live_one_lite_video(profile, tmp_path):
    result = await flow_generate(profile, "video", "a red apple rolling on a table",
                                 model="veo-3.1-lite", duration=8, max_credits=10,
                                 out=tmp_path / "apple.mp4")
    assert result["status"] == "done" and (tmp_path / "apple.mp4").stat().st_size > 100_000


@pytest.mark.live
async def test_live_projects_and_media(profile):
    projects = await flow_projects(profile)
    assert any(p["title"] == "aistudio" for p in projects["projects"])
    media = await flow_media(profile)
    assert media["count"] >= 0 and all(m["media_id"] for m in media["media"])


@pytest.mark.live_slow
async def test_live_character_costs_nothing(profile, tmp_path):
    # 0 credits, but it creates a real character in the "aistudio" project.
    result = await flow_character(profile, "a small paper boat with a red sail",
                                  out=tmp_path / "boat.png")
    assert result["credits_spent"] == 0 and (tmp_path / "boat.png").stat().st_size > 1000


@pytest.mark.live_slow
async def test_live_extend_one_clip(profile, tmp_path):
    videos = [m for m in (await flow_media(profile, kind="video"))["media"]
              if m["status"] == "done"]
    if not videos:
        pytest.skip("no finished video to extend")
    result = await flow_extend(profile, videos[0]["media_id"], "the camera slowly pulls back",
                               max_credits=10, out=tmp_path / "next.mp4")
    assert result["status"] == "done" and (tmp_path / "next.mp4").stat().st_size > 100_000
