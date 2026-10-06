"""Opt-in: `uv run pytest -m live tests/live/test_live_quick_wins.py` (no generation, no quota).

Stars then unstars a model it finds unstarred (restoring the original state) and exports the
first Build app that has code into a temporary folder.
"""

import os

import pytest

from aistudio._app.apps import export_app
from aistudio._app.models import star_model
from aistudio.client import AIStudioClient
from aistudio.paths import get_profile, resolve_profile_name

pytestmark = pytest.mark.live


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.storage_state.exists():
        pytest.skip('no logged-in profile; run "aistudio login"')
    return p


async def test_live_status_star_roundtrip_and_export(profile, tmp_path):
    async with AIStudioClient.from_profile(profile) as client:
        assert await client.status.incidents()
        before = {m for m, _ in await client.models.starred()}
        target = next(m.id for m in await client.models.list() if m.id not in before)
        assert (await star_model(client, profile, target))["changed"]
        assert target in {m for m, _ in await client.models.starred()}
        assert (await star_model(client, profile, target, starred=False))["changed"]
        assert {m for m, _ in await client.models.starred()} == before
        apps = [a for a in await client.apps.list() if a.storage == "applet"]
        for app in apps:
            if await client.apps.snapshot_files(app.id):
                result = await export_app(client, app.id, tmp_path / "export")
                assert result["count"] > 0
                break
