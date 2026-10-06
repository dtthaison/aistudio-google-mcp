from aistudio._auth.session import Session, format_cookie_header
from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio.paths import get_profile

HOST = "alkalimakersuite-pa.clients6.google.com"


def cookie(name, value="v", domain=".google.com", expires=-1):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": expires}


def test_cookies_for_matches_parent_domains_only():
    s = Session(cookies=[
        cookie("SID", "a"),
        cookie("AISTUDIO", "b", domain="aistudio.google.com"),
        cookie("YT", "c", domain=".youtube.com"),
    ])
    assert s.cookies_for(HOST) == {"SID": "a"}
    assert s.cookies_for("aistudio.google.com") == {"SID": "a", "AISTUDIO": "b"}


def test_cookies_for_skips_expired():
    s = Session(cookies=[cookie("OLD", expires=100.0), cookie("NEW", expires=10_000.0)])
    assert s.cookies_for(HOST, now=5_000.0) == {"NEW": "v"}


def test_more_specific_domain_wins():
    s = Session(cookies=[cookie("NID", "b", domain="clients6.google.com"), cookie("NID", "a")])
    assert s.cookies_for(HOST) == {"NID": "b"}


def test_cookie_header_format():
    assert format_cookie_header({"A": "1", "B": "2"}) == "A=1; B=2"
    assert Session(cookies=[cookie("A", "1")]).cookie_header(HOST) == "A=1"


def test_session_load(aistudio_home):
    profile = get_profile("default")
    save_state(profile, {"cookies": [cookie("SID")], "origins": []}, AccountInfo(authuser=3))
    save_session_info(profile, "AIza-test")
    s = Session.load(profile)
    assert s.api_key == "AIza-test"
    assert s.authuser == 3
    assert s.cookies_for(HOST) == {"SID": "v"}
