import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._transport.http import HttpTransport, method_url
from aistudio.exceptions import (
    AuthError,
    NetworkError,
    QuotaExceededError,
    ServerError,
)

URL = method_url("ListModels")


def session(value="s"):
    return Session(cookies=[{"name": "SAPISID", "value": value, "domain": ".google.com",
                             "expires": -1}])


def transport(**kwargs):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    t = HttpTransport(kwargs.pop("session", session()), sleep=fake_sleep, jitter=lambda: 0.0,
                      **kwargs)
    return t, delays


@respx.mock
async def test_idempotent_call_retries_retriable_errors_then_succeeds():
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(503), httpx.Response(503), httpx.Response(200, text="[1]"),
    ])
    t, delays = transport()
    async with t:
        assert await t.call("ListModels", [], idempotent=True) == [1]
    assert route.call_count == 3
    assert delays == [0.5, 1.0]


@respx.mock
async def test_idempotent_call_gives_up_after_three_sends():
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    t, _ = transport()
    async with t:
        with pytest.raises(ServerError):
            await t.call("ListModels", [], idempotent=True)
    assert route.call_count == 3


@respx.mock
async def test_non_idempotent_call_is_sent_once_on_server_error():
    route = respx.post(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200)])
    t, _ = transport()
    async with t:
        with pytest.raises(ServerError):
            await t.call("ListModels", [])
    assert route.call_count == 1


@respx.mock
async def test_quota_errors_are_never_retried():
    route = respx.post(URL).mock(
        return_value=httpx.Response(429, text='[,[8,"Quota exceeded for model"]]')
    )
    t, _ = transport()
    async with t:
        with pytest.raises(QuotaExceededError):
            await t.call("ListModels", [], idempotent=True)
    assert route.call_count == 1


@respx.mock
async def test_connect_failure_is_retried_even_when_not_idempotent():
    route = respx.post(URL).mock(side_effect=[httpx.ConnectError("down"),
                                              httpx.Response(200, text="[]")])
    t, _ = transport()
    async with t:
        assert await t.call("ListModels", []) == []  # idempotent=False by default
    assert route.call_count == 2


@respx.mock
async def test_read_error_is_not_retried_when_not_idempotent():
    route = respx.post(URL).mock(side_effect=httpx.ReadError("cut"))
    t, _ = transport()
    async with t:
        with pytest.raises(NetworkError):
            await t.call("ListModels", [])
    assert route.call_count == 1


@respx.mock
async def test_auth_error_reloads_session_once_and_resends():
    route = respx.post(URL).mock(side_effect=[httpx.Response(401), httpx.Response(200, text="[]")])
    t, _ = transport(reload_session=lambda: session("fresh"))
    async with t:
        assert await t.call("ListModels", []) == []
    assert route.call_count == 2
    assert "SAPISID=fresh" in route.calls[1].request.headers["cookie"]


@respx.mock
async def test_auth_error_with_unchanged_session_raises_after_one_request():
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    t, _ = transport(reload_session=lambda: session())
    async with t:
        with pytest.raises(AuthError) as info:
            await t.call("ListModels", [], idempotent=True)
    assert route.call_count == 1
    assert info.value.hint == 'run "aistudio login"'


@respx.mock
async def test_auth_reload_happens_only_once_per_call():
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    versions = iter(["a", "b", "c"])
    t, _ = transport(reload_session=lambda: session(next(versions)))
    async with t:
        with pytest.raises(AuthError):
            await t.call("ListModels", [])
    assert route.call_count == 2
