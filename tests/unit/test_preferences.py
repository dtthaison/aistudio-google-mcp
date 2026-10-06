from aistudio._rpc import registry
from aistudio._rpc.params.preferences import get_preferences, set_starred
from aistudio._rpc.rows.preferences import decode_starred

STAR = ("antigravity-preview-09-2026", (1791272458, 217000000))


def test_requests_match_captures(captured):
    assert get_preferences() == captured("GetUserPreferences", 5).request
    assert set_starred([STAR]) == captured("UpdateUserPreferences", 6).request
    assert set_starred([]) == captured("UpdateUserPreferences", 7).request


def test_decode_starred(captured):
    assert decode_starred(captured("UpdateUserPreferences", 6).response) == [STAR]
    assert decode_starred(captured("GetUserPreferences", 5).response) == []
    assert decode_starred([None] * 6 + [[["models/x", ["bad"]], "junk", ["models/y"]]]) == [
        ("x", (0, 0)), ("y", (0, 0))]


def test_registry():
    assert registry.GET_PREFERENCES.idempotent and not registry.UPDATE_PREFERENCES.idempotent
