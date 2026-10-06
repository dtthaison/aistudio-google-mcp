import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._transport.http import HttpTransport
from aistudio.exceptions import AuthError, ValidationError

URL = "https://aistudio.google.com/_/upload/app/file/abc"


def session(value="sid"):
    return Session(cookies=[{"name": "SID", "value": value, "domain": ".google.com",
                             "expires": -1}])


async def no_sleep(_):
    pass


@respx.mock
async def test_download_sends_cookies_and_retries_server_errors():
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"x")])
    async with HttpTransport(session(), sleep=no_sleep, jitter=lambda: 0.0) as transport:
        assert await transport.download(URL) == b"x"
    assert route.call_count == 2
    assert route.calls.last.request.headers["cookie"] == "SID=sid"
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_download_reloads_the_session_once_on_auth_errors():
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, content=b"y")])
    async with HttpTransport(session(), reload_session=lambda: session("fresh"),
                             sleep=no_sleep) as transport:
        assert await transport.download(URL) == b"y"
    assert route.calls.last.request.headers["cookie"] == "SID=fresh"
    unchanged = respx.get(URL + "2").mock(return_value=httpx.Response(403))
    async with HttpTransport(session(), reload_session=lambda: session(),
                             sleep=no_sleep) as transport:
        with pytest.raises(AuthError):
            await transport.download(URL + "2")
    assert unchanged.call_count == 1


async def test_download_refuses_other_hosts():
    async with HttpTransport(session()) as transport:
        with pytest.raises(ValidationError):
            await transport.download("https://evil.example.com/x")


@respx.mock
async def test_a_redirect_to_sign_in_is_an_auth_error():
    respx.get(URL).mock(return_value=httpx.Response(
        302, headers={"location": "https://accounts.google.com/ServiceLogin"}))
    async with HttpTransport(session(), sleep=no_sleep) as transport:
        with pytest.raises(AuthError) as info:
            await transport.download(URL)
    assert info.value.hint == 'run "aistudio login"'
