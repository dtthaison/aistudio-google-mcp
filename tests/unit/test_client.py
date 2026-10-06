import httpx
import pytest
import respx

from aistudio._rpc.registry import RpcSpec, Transport
from aistudio._transport.http import method_url
from aistudio.exceptions import ValidationError


@respx.mock
async def test_models_list_decodes_the_catalog(make_client, captured):
    route = respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )
    async with make_client() as client:
        models = await client.models.list()
    assert len(models) == 42
    assert route.calls.last.request.content == b"[]"


async def test_browser_only_rpcs_are_refused_until_the_browser_backend_exists(make_client):
    async with make_client() as client:
        with pytest.raises(ValidationError):
            await client.call(RpcSpec("GenerateContent", Transport.BROWSER, False), [])
