"""Opt-in checks against the real AI Studio service: `uv run pytest -m live`.

Read-only calls only, using the logged-in profile ($AISTUDIO_PROFILE or the default).
"""

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


async def test_live_catalog_decodes_with_known_categories(profile):
    async with AIStudioClient.from_profile(profile) as client:
        models = await client.models.list()
    assert models
    assert not [(m.id, c) for m in models for c in m.categories if c.startswith("code:")]


async def test_live_prompt_list_decodes(profile):
    async with AIStudioClient.from_profile(profile) as client:
        prompts = await client.prompts.list()
    assert all(p.id for p in prompts)


async def test_live_count_tokens(profile):
    async with AIStudioClient.from_profile(profile) as client:
        count = await client.chat.count_tokens("gemini-flash-latest", "hello world")
    assert count.total_tokens > 0
