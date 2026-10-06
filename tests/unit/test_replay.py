import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._dev.replay import clean_url, is_read_only, replay_all, replay_fixture
from aistudio._transport.http import HttpTransport, method_url


def session():
    return Session(cookies=[{"name": "SAPISID", "value": "s", "domain": ".google.com",
                             "expires": -1}])


def test_is_read_only():
    for method in ("ListPrompts", "GetPrompt", "CountTokens", "ListModels"):
        assert is_read_only(method)
    for method in ("DeletePrompt", "GenerateContent", "UpdatePrompt", "CreatePrompt",
                   "Listen", "GetOrCreatePrompt", "ListAndDeletePrompts", "Getaway"):
        assert not is_read_only(method)


def test_clean_url_drops_redacted_key():
    assert clean_url("https://x.google.com/a?key=AIza_REDACTED&alt=json") == (
        "https://x.google.com/a?alt=json"
    )
    assert clean_url("https://x.google.com/a") == "https://x.google.com/a"


@pytest.mark.parametrize(
    "status,body,verdict,code",
    [
        (200, "[[]]", "http-ok", None),
        (404, '[,[5,"Prompt not found"]]', "http-reached", "NOT_FOUND"),
        (400, '[,[3,"Invalid argument"]]', "http-reached", "VALIDATION_ERROR"),
        (403, '[,[7,"The caller does not have permission"]]', "http-rejected", "AUTH_ERROR"),
        (500, '[,[13,"Internal error encountered."]]', "error", "SERVER_ERROR"),
        (400, '[,[3,"API key not valid. Please pass a valid API key."]]', "http-rejected",
         "AUTH_ERROR"),
        (302, "", "http-rejected", "REDIRECT"),
    ],
)
@respx.mock
async def test_replay_verdicts(tmp_path, write_rpc_fixture, status, body, verdict, code):
    path = write_rpc_fixture(tmp_path, "ListPrompts", body="[null,20]")
    route = respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(status, text=body)
    )
    async with HttpTransport(session()) as transport:
        result = await replay_fixture(path, transport)
    assert (result.verdict, result.error_code, result.http_status) == (verdict, code, status)
    assert route.calls.last.request.content == b"[null,20]"


@respx.mock(assert_all_called=False)
async def test_mutating_and_generating_rpcs_are_not_sent_by_default(tmp_path, write_rpc_fixture):
    routes = {m: respx.post(method_url(m)) for m in ("DeletePrompt", "GenerateContent")}
    async with HttpTransport(session()) as transport:
        results = [await replay_fixture(write_rpc_fixture(tmp_path, m), transport)
                   for m in routes]
    assert [r.verdict for r in results] == ["skipped", "skipped"]
    assert "--allow-mutating" in results[0].message
    assert not any(route.called for route in routes.values())


@respx.mock
async def test_mutating_rpc_sent_with_flag(tmp_path, write_rpc_fixture):
    route = respx.post(method_url("CreatePrompt")).mock(return_value=httpx.Response(200, text="[]"))
    async with HttpTransport(session()) as transport:
        result = await replay_fixture(write_rpc_fixture(tmp_path, "CreatePrompt"), transport,
                                      allow_mutating=True)
    assert result.verdict == "http-ok"
    assert route.called


async def test_non_rpc_fixture_skipped(tmp_path, write_rpc_fixture):
    path = write_rpc_fixture(tmp_path, "other_upload",
                             url="https://content-push.googleapis.com/upload/x")
    async with HttpTransport(session()) as transport:
        assert (await replay_fixture(path, transport)).verdict == "skipped"


@respx.mock
async def test_replay_strips_redacted_key_from_url(tmp_path, write_rpc_fixture):
    path = write_rpc_fixture(tmp_path, "ListModels",
                             url=method_url("ListModels") + "?key=AIza_REDACTED")
    route = respx.post(method_url("ListModels")).mock(return_value=httpx.Response(200, text="[]"))
    async with HttpTransport(session()) as transport:
        await replay_fixture(path, transport)
    assert "key=" not in str(route.calls.last.request.url)


@respx.mock
async def test_replay_all_walks_directory(tmp_path, write_rpc_fixture):
    write_rpc_fixture(tmp_path, "ListModels")
    write_rpc_fixture(tmp_path, "DeletePrompt")
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(200, text="[]"))
    async with HttpTransport(session()) as transport:
        results = await replay_all(tmp_path, transport, delay=0)
    assert [(r.method, r.verdict) for r in results] == [
        ("DeletePrompt", "skipped"), ("ListModels", "http-ok"),
    ]


@respx.mock(assert_all_called=False)
async def test_mislabelled_fixture_is_not_sent(tmp_path, write_rpc_fixture):
    route = respx.post(method_url("DeletePrompt"))
    path = write_rpc_fixture(tmp_path, "ListModels", url=method_url("DeletePrompt"))
    async with HttpTransport(session()) as transport:
        result = await replay_fixture(path, transport)
    assert result.verdict == "skipped"
    assert "does not match" in result.message
    assert not route.called


async def test_rpc_marker_must_be_in_the_path(tmp_path, write_rpc_fixture):
    path = write_rpc_fixture(tmp_path, "ListModels",
                             url="https://x.googleapis.com/upload?next=/$rpc/ListModels")
    async with HttpTransport(session()) as transport:
        assert (await replay_fixture(path, transport)).verdict == "skipped"
