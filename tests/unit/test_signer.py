import pytest

from aistudio._auth.session import Session
from aistudio._auth.signer import authorization_header, build_headers, sapisid_hash
from aistudio.exceptions import AuthError

TS = 1700000000
SAPISID_DIGEST = "8bb7c14d3d8aadd9a715140f82ef17b924c19bfd"
ONEP_DIGEST = "9dcc26d7523b112e44859b26f3b92dc3f84100df"
THREEP_DIGEST = "c3288cfe57bfdb9ae86d5ab7863d1c8774942ce4"


def test_sapisid_hash_known_answer():
    assert sapisid_hash("sapisid-secret", TS) == f"{TS}_{SAPISID_DIGEST}"


def test_authorization_header_includes_1p_and_3p_variants():
    cookies = {
        "SAPISID": "sapisid-secret",
        "__Secure-1PAPISID": "onep-secret",
        "__Secure-3PAPISID": "threep-secret",
    }
    assert authorization_header(cookies, TS) == (
        f"SAPISIDHASH {TS}_{SAPISID_DIGEST} "
        f"SAPISID1PHASH {TS}_{ONEP_DIGEST} "
        f"SAPISID3PHASH {TS}_{THREEP_DIGEST}"
    )


def test_authorization_header_sapisid_only():
    assert authorization_header({"SAPISID": "sapisid-secret"}, TS) == (
        f"SAPISIDHASH {TS}_{SAPISID_DIGEST}"
    )


def test_missing_sapisid_raises_auth_error():
    with pytest.raises(AuthError) as info:
        authorization_header({"__Secure-1PAPISID": "x"}, TS)
    assert info.value.hint == 'run "aistudio login"'


def make_session(api_key):
    cookies = [
        {"name": "SAPISID", "value": "sapisid-secret", "domain": ".google.com", "expires": -1},
        {"name": "SID", "value": "sid", "domain": ".google.com", "expires": -1},
    ]
    return Session(cookies=cookies, api_key=api_key, authuser=2)


def test_build_headers():
    headers = build_headers(make_session("AIza-test"), ts=TS)
    assert headers == {
        "Authorization": f"SAPISIDHASH {TS}_{SAPISID_DIGEST}",
        "Content-Type": "application/json+protobuf",
        "Cookie": "SAPISID=sapisid-secret; SID=sid",
        "Origin": "https://aistudio.google.com",
        "Referer": "https://aistudio.google.com/",
        "X-Goog-AuthUser": "2",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIza-test",
    }


def test_build_headers_without_api_key_omits_header():
    assert "X-Goog-Api-Key" not in build_headers(make_session(None), ts=TS)
