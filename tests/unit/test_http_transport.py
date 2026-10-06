import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._transport.http import HttpTransport, method_url
from aistudio.exceptions import AuthError, NetworkError, NotFoundError, ServerError, ValidationError


def make_session():
    return Session(
        cookies=[
            {"name": "SAPISID", "value": "s", "domain": ".google.com", "expires": -1},
            {"name": "SID", "value": "x", "domain": ".google.com", "expires": -1},
        ],
        api_key="AIza-test",
        authuser=1,
    )


def test_method_url():
    assert method_url("ListModels") == (
        "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
        "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
    )


@respx.mock
async def test_call_posts_jspb_and_decodes():
    route = respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text='[[["models/a",,"A"]]]')
    )
    async with HttpTransport(make_session()) as transport:
        result = await transport.call("ListModels", [None, 50])
    assert result == [[["models/a", None, "A"]]]
    request = route.calls.last.request
    assert request.content == b"[null,50]"
    assert request.headers["content-type"] == "application/json+protobuf"
    assert request.headers["x-goog-api-key"] == "AIza-test"
    assert request.headers["x-goog-authuser"] == "1"
    assert request.headers["authorization"].startswith("SAPISIDHASH ")
    assert "SAPISID=s" in request.headers["cookie"]


@pytest.mark.parametrize(
    "status,body,exc",
    [
        (404, '[,[5,"Prompt not found"]]', NotFoundError),
        (403, '[,[7,"The caller does not have permission"]]', AuthError),
        (500, '[,[13,"Internal error encountered."]]', ServerError),
        (401, "<html>login</html>", AuthError),
        (400, "", ValidationError),
    ],
)
@respx.mock
async def test_error_responses_map_to_exceptions(status, body, exc):
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(status, text=body))
    async with HttpTransport(make_session()) as transport:
        with pytest.raises(exc):
            await transport.call("ListModels", [])


@respx.mock
async def test_transport_failures_become_network_error():
    respx.post(method_url("ListModels")).mock(side_effect=httpx.ConnectError("boom"))
    async with HttpTransport(make_session()) as transport:
        with pytest.raises(NetworkError):
            await transport.call("ListModels", [])


@pytest.mark.parametrize(
    "url",
    ["https://evil.example.com/x", "https://google.com.evil.io/x", "http://aistudio.google.com/x"],
)
async def test_refuses_to_send_credentials_elsewhere(url):
    async with HttpTransport(make_session()) as transport:
        with pytest.raises(ValidationError):
            await transport.post_raw(url, "[]")
