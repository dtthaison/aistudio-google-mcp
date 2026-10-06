"""Opt-in: `uv run pytest -m live_slow tests/live/test_live_build_create.py`.

Builds one tiny app and updates it once (uses Build quota, takes several minutes, opens a
minimized Chrome window). The app stays in your Build account.
"""

import os

import pytest

from aistudio._app.build import create_app, prompt_app
from aistudio.paths import get_profile, resolve_profile_name

pytestmark = pytest.mark.live_slow


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.chrome_dir.exists():
        pytest.skip('no browser session; run "aistudio login"')
    return p


async def test_live_create_then_prompt(profile):
    created = await create_app(profile, "A web page that shows today's date in large text.")
    assert created["status"] == "done" and "index.html" in created["files"]
    updated = await prompt_app(profile, created["app_id"], "Make the text dark blue.")
    assert updated["status"] == "done" and updated["app_id"] == created["app_id"]
