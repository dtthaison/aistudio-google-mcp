import httpx
import pytest
import respx

from aistudio._app.models import get_model, list_models, star_model
from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError
from aistudio.paths import get_profile

T0 = 1_800_000_000.0


def prefs(starred):
    return wire.encode([2, None, None, None, None, None,
                        [[f"models/{m}", [str(s), 0]] for m, s in starred]])


def serve(captured, starred):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"]))
    respx.post(method_url("GetUserPreferences")).mock(
        return_value=httpx.Response(200, text=prefs(starred)))
    return respx.post(method_url("UpdateUserPreferences")).mock(
        side_effect=lambda request: httpx.Response(200, text=prefs([])))


@respx.mock
async def test_star_appends_with_a_timestamp(aistudio_home, make_client, captured):
    update = serve(captured, [("gemma-4-31b-it", 1700000000)])
    async with make_client() as client:
        result = await star_model(client, get_profile("default"), "LYRIA-3.5", now=lambda: T0)
    assert result == {"model": "lyria-3.5", "starred": True, "changed": True}
    sent = wire.decode(update.calls.last.request.content.decode())
    assert sent == [[None] * 6 + [[["models/gemma-4-31b-it", [1700000000, 0]],
                                   ["models/lyria-3.5", [1800000000, 0]]]],
                    [["starred_models"]]]


@respx.mock
async def test_noops_and_unknown_models_send_nothing(aistudio_home, make_client, captured):
    # No UpdateUserPreferences route: any write fails the test as "not mocked".
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"]))
    respx.post(method_url("GetUserPreferences")).mock(
        return_value=httpx.Response(200, text=prefs([("lyria-3.5", 1700000000)])))
    profile = get_profile("default")
    async with make_client() as client:
        again = await star_model(client, profile, "lyria-3.5")
        absent = await star_model(client, profile, "gemma-4-31b-it", starred=False)
        with pytest.raises(NotFoundError):
            await star_model(client, profile, "no-such-model")
    assert (again["changed"], absent["changed"]) == (False, False)


@respx.mock
async def test_unstar_last_model_sends_an_empty_list(aistudio_home, make_client, captured):
    update = serve(captured, [("lyria-3.5", 1700000000)])
    async with make_client() as client:
        await star_model(client, get_profile("default"), "lyria-3.5", starred=False)
    assert update.calls.last.request.content == b'[[],[["starred_models"]]]'


@respx.mock
async def test_list_marks_and_filters_starred(aistudio_home, make_client, captured):
    serve(captured, [("lyria-3.5", 1700000000)])
    profile = get_profile("default")
    async with make_client() as client:
        starred = await list_models(client, profile, starred_only=True, now=lambda: T0)
        one = await get_model(client, profile, "gemma-4-31b-it", now=lambda: T0)
    assert [m["id"] for m in starred["models"]] == ["lyria-3.5"]
    assert starred["models"][0]["starred"] is True and one["starred"] is False


@respx.mock
async def test_list_survives_a_preferences_failure(aistudio_home, make_client, captured):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"]))
    respx.post(method_url("GetUserPreferences")).mock(return_value=httpx.Response(404))
    async with make_client() as client:
        data = await list_models(client, get_profile("default"), now=lambda: T0)
    assert data["count"] == 42 and data["models"][0]["starred"] is None
