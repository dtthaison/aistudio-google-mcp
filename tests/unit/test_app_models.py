import json
from datetime import timezone

import httpx
import pytest
import respx

from aistudio._app.models import (
    CACHE_TTL,
    filter_models,
    get_model,
    list_models,
    load_catalog,
    normalize_model_id,
)
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ValidationError
from aistudio.paths import get_profile

T0 = 1_800_000_000.0


def serve_catalog(captured):
    respx.post(method_url("GetUserPreferences")).mock(return_value=httpx.Response(200, text="[2]"))
    return respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )


@respx.mock
async def test_catalog_is_cached_for_an_hour(aistudio_home, make_client, captured):
    route = serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        await load_catalog(client, profile, now=lambda: T0)
        await load_catalog(client, profile, now=lambda: T0 + CACHE_TTL - 1)
        assert route.call_count == 1
        await load_catalog(client, profile, now=lambda: T0 + CACHE_TTL + 1)
        assert route.call_count == 2
        await load_catalog(client, profile, refresh=True, now=lambda: T0 + CACHE_TTL + 2)
        assert route.call_count == 3
    cached = json.loads(profile.models.read_text())
    assert cached["fetched_at"] == T0 + CACHE_TTL + 2


@pytest.mark.parametrize(
    "content",
    [
        "{garbage",
        '["not", "a", "dict"]',
        '{"fetched_at": "x", "response": []}',
        json.dumps({"fetched_at": T0 + 10_000, "response": [[["models/a"]]]}),
        json.dumps({"fetched_at": T0, "response": "no longer decodes"}),
    ],
)
@respx.mock
async def test_corrupt_or_stale_cache_is_a_miss(aistudio_home, make_client, captured, content):
    route = serve_catalog(captured)
    profile = get_profile("default").ensure()
    profile.models.write_text(content)
    async with make_client() as client:
        models = await load_catalog(client, profile, now=lambda: T0)
    assert route.call_count == 1
    assert len(models) == 42


@respx.mock
async def test_list_models_filters_by_category_and_search(aistudio_home, make_client, captured):
    serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        music = await list_models(client, profile, category="music", now=lambda: T0)
        veo = await list_models(client, profile, search="VEO 3.1", now=lambda: T0)
    assert sorted(m["id"] for m in music["models"]) == [
        "lyria-3-clip-preview", "lyria-3-pro-preview", "lyria-3.5",
    ]
    assert music["count"] == 3
    assert {m["id"] for m in veo["models"]} == {
        "veo-3.1-generate-preview", "veo-3.1-fast-generate-preview",
        "veo-3.1-lite-generate-preview",
    }


@respx.mock
async def test_get_model_normalizes_the_id(aistudio_home, make_client, captured):
    serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        model = await get_model(client, profile, "models/LYRIA-3.5", now=lambda: T0,
                                tz=timezone.utc)
        with pytest.raises(NotFoundError) as info:
            await get_model(client, profile, "no-such-model", now=lambda: T0)
    assert model["id"] == "lyria-3.5"
    assert model["release_date"] == "2026-09-03"
    assert info.value.hint == 'see "aistudio models list"'


def test_normalize_model_id():
    assert normalize_model_id(" models/Gemini-3.1-Flash-Lite-Image ") == (
        "gemini-3.1-flash-lite-image"
    )
    assert normalize_model_id("lyria-3.5") == "lyria-3.5"


def test_filter_models_without_filters_is_identity(captured):
    from aistudio._rpc.rows.models import decode_models

    models = decode_models(captured("ListModels").response)
    assert filter_models(models) == models


@respx.mock
async def test_get_model_resolves_a_unique_prefix(aistudio_home, make_client, captured):
    serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        unique = await get_model(client, profile, "deep-research-max", now=lambda: T0)
        exact = await get_model(client, profile, "gemini-3.1-flash-lite", now=lambda: T0)
        with pytest.raises(ValidationError) as info:
            await get_model(client, profile, "antigravity", now=lambda: T0)
    assert unique["id"] == "deep-research-max-preview-04-2026"
    assert exact["id"] == "gemini-3.1-flash-lite"
    assert "antigravity-preview-05-2026" in info.value.message
    assert "antigravity-preview-09-2026" in info.value.message


def cache_without(profile, captured, model_id, fetched_at):
    """A fresh catalog cache that predates ``model_id`` (released within the cache hour)."""
    response = captured("ListModels").response
    response[0] = [entry for entry in response[0] if entry[0] != f"models/{model_id}"]
    profile.ensure()
    profile.models.write_text(json.dumps({"fetched_at": fetched_at, "response": response}))


@respx.mock
async def test_a_model_missing_from_a_fresh_cache_is_fetched_once_more(aistudio_home, make_client,
                                                                      captured):
    route = serve_catalog(captured)
    profile = get_profile("default")
    cache_without(profile, captured, "lyria-3.5", T0)
    async with make_client() as client:
        model = await get_model(client, profile, "lyria-3.5", now=lambda: T0 + 5)
        assert model["id"] == "lyria-3.5"
        assert route.call_count == 1
        await get_model(client, profile, "lyria-3.5", now=lambda: T0 + 6)
        assert route.call_count == 1  # the refreshed cache now has it


@respx.mock
async def test_a_cold_cache_miss_fetches_once(aistudio_home, make_client, captured):
    route = serve_catalog(captured)
    async with make_client() as client:
        with pytest.raises(NotFoundError):
            await get_model(client, get_profile("default"), "no-such-model", now=lambda: T0)
    assert route.call_count == 1  # the first load already came from the network


@respx.mock
async def test_a_cached_prefix_does_not_hide_a_new_exact_id(aistudio_home, make_client, captured):
    route = serve_catalog(captured)
    profile = get_profile("default")
    cache_without(profile, captured, "gemini-3.1-flash-lite", T0)
    async with make_client() as client:
        # older than RECHECK_S: a cached prefix match is rechecked (plan E: a recent one is used)
        model = await get_model(client, profile, "gemini-3.1-flash-lite", now=lambda: T0 + 400)
    assert model["id"] == "gemini-3.1-flash-lite" and route.call_count == 1


@respx.mock
async def test_a_bad_cache_fetches_once(aistudio_home, make_client, captured):
    # Plan C review M3: a fresh cache that does not decode is one network read, not two.
    route = serve_catalog(captured)
    profile = get_profile("default")
    profile.ensure()
    profile.models.write_text(json.dumps({"fetched_at": T0, "response": {"bogus": 1}}))
    async with make_client() as client:
        with pytest.raises(NotFoundError):
            await get_model(client, profile, "no-such-model", now=lambda: T0 + 5)
    assert route.call_count == 1


@respx.mock
async def test_a_prefix_lookup_uses_a_recent_cache(aistudio_home, make_client, captured):
    # Plan C review M4: an abbreviated id must not refetch the catalog on every call.
    route = serve_catalog(captured)
    profile = get_profile("default")
    cache_without(profile, captured, "no-such-model", T0)
    async with make_client() as client:
        model = await get_model(client, profile, "gemini-3.1-flash-lite-im", now=lambda: T0 + 60)
        assert model["id"] == "gemini-3.1-flash-lite-image" and route.call_count == 0
        await get_model(client, profile, "gemini-3.1-flash-lite-im", now=lambda: T0 + 400)
    assert route.call_count == 1
