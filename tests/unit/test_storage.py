import pytest

from aistudio._auth.storage import (
    AccountInfo,
    account_info,
    delete_credentials,
    load_api_key,
    load_state,
    missing_cookies,
    save_session_info,
    save_state,
)
from aistudio.exceptions import AuthError
from aistudio.paths import get_profile


def cookie(name, domain=".google.com", value="v"):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": -1}


STATE = {
    "cookies": [
        cookie("SID"),
        cookie("SAPISID"),
        cookie("PREF", domain=".youtube.com"),
        cookie("X", domain="notgoogle.com"),
        cookie("Y", domain="google.com.evil.io"),
    ],
    "origins": [
        {"origin": "https://aistudio.google.com", "localStorage": []},
        {"origin": "https://www.youtube.com", "localStorage": []},
    ],
}


def test_save_filters_foreign_domains_and_records_account(aistudio_home):
    profile = get_profile("default")
    save_state(profile, STATE, AccountInfo(email="me@example.com", authuser=2))
    state = load_state(profile)
    assert [c["name"] for c in state["cookies"]] == ["SID", "SAPISID"]
    assert [o["origin"] for o in state["origins"]] == ["https://aistudio.google.com"]
    assert account_info(state) == AccountInfo(email="me@example.com", authuser=2)


def test_saved_state_is_owner_only(aistudio_home):
    profile = get_profile("default")
    save_state(profile, STATE, AccountInfo())
    assert profile.storage_state.stat().st_mode & 0o777 == 0o600


def test_load_missing_state_raises_auth_error_with_hint(aistudio_home):
    with pytest.raises(AuthError) as info:
        load_state(get_profile("default"))
    assert info.value.hint == 'run "aistudio login"'


@pytest.mark.parametrize("content", ["{not json", "[]", '{"cookies": "nope"}', ""])
def test_load_corrupt_state_raises_auth_error(aistudio_home, content):
    profile = get_profile("default").ensure()
    profile.storage_state.write_text(content)
    with pytest.raises(AuthError):
        load_state(profile)


def test_missing_cookies():
    assert missing_cookies(STATE) == []
    assert missing_cookies({"cookies": [cookie("SID")]}) == ["SAPISID"]
    assert missing_cookies({"cookies": [cookie("SAPISID", domain=".youtube.com")]}) == [
        "SID", "SAPISID",
    ]


def test_session_info_roundtrip(aistudio_home):
    profile = get_profile("default")
    assert load_api_key(profile) is None
    save_session_info(profile, "AIza-test")
    assert load_api_key(profile) == "AIza-test"
    assert profile.session.stat().st_mode & 0o777 == 0o600


def test_corrupt_session_info_reads_as_no_key(aistudio_home):
    profile = get_profile("default").ensure()
    profile.session.write_text("garbage")
    assert load_api_key(profile) is None


def test_delete_credentials(aistudio_home):
    profile = get_profile("default")
    save_state(profile, STATE, AccountInfo())
    save_session_info(profile, "k")
    profile.chrome_dir.mkdir()
    assert delete_credentials(profile) == ["storage_state.json", "session.json", "chrome/"]
    assert not profile.storage_state.exists()
    assert not profile.session.exists()
    assert not profile.chrome_dir.exists()
    assert delete_credentials(profile) == []
