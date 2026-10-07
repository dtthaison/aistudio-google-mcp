"""Opt-in read-only checks against real AI Studio Build: `uv run pytest -m live`."""

import os

import pytest

from aistudio.client import AIStudioClient
from aistudio.paths import get_profile, resolve_profile_name

pytestmark = pytest.mark.live


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.storage_state.exists():
        pytest.skip('no logged-in profile; run "aistudio login"')
    return p


async def test_live_apps_recent_and_gallery_decode(profile):
    async with AIStudioClient.from_profile(profile) as client:
        apps = await client.apps.list()
        recent = await client.apps.recent()
        gallery = await client.gallery.list()
        runs = [await client.apps.runs(a.id) for a in [a for a in apps
                                                         if a.storage == "applet"][:1]]
    assert all(a.id for a in apps)
    assert all(i.kind in {"app", "gallery", "prompt", "unknown"} for i in recent)
    assert {a.group for a in gallery} >= {"showcase", "template"}
    assert all(isinstance(r, list) for r in runs)
