"""LoadCodeAssistantInteractionHistory: the per-turn code snapshots (docs/wire/findings.md)."""

import pytest

from aistudio._rpc import registry
from aistudio._rpc.params.apps import load_history
from aistudio._rpc.rows.apps import decode_history_next, decode_turn_snapshots
from aistudio._types.apps import TurnSnapshot
from aistudio.exceptions import SchemaDriftError

APP = "d6d4717c-30c3-4564-ac77-50cb262d3884"


def test_request_matches_the_capture(captured):
    assert load_history(APP, 100) == captured("LoadCodeAssistantInteractionHistory").request
    assert load_history(APP, 30, "60") == [APP, 30, None, None, "60", APP]
    assert registry.LOAD_HISTORY.transport is registry.Transport.HTTP
    assert registry.LOAD_HISTORY.idempotent


def test_a_snapshot_step_is_one_turn(captured):
    response = captured("LoadCodeAssistantInteractionHistory").response
    turns = decode_turn_snapshots(response)
    assert [t.prompt for t in turns] == [
        "A web page that shows a random motivational quote with a New quote button",
        "Use a dark theme",
    ]
    assert turns[1].saved == "2026-10-06T10:13:26Z"
    assert len(turns[0].files) == len(turns[1].files) == 21
    assert turns[0].files[0] == (
        ".env.example", "b6d12c728de0aac8b0bc6239e53aa8088c1431bcb4f9a3d61e5e8a2df1c7c400")
    assert isinstance(turns[0], TurnSnapshot)
    assert turns[1].to_dict() == {"prompt": "Use a dark theme", "saved": "2026-10-06T10:13:26Z",
                                  "files": 21}


def test_next_page_token(captured):
    assert decode_history_next(captured("LoadCodeAssistantInteractionHistory").response) == "100"
    assert decode_history_next([None, []]) is None
    assert decode_history_next([]) is None
    assert decode_history_next(None) is None


def test_empty_and_odd_histories():
    assert decode_turn_snapshots([]) == []
    assert decode_turn_snapshots([None, []]) == []
    assert decode_turn_snapshots([None, [[None] * 9]]) == []  # steps with no snapshot
    odd = [None, [[None] * 8 + [["model", "Snapshot from x", None, ["5", 0], [["a", "h"]]]]]]
    assert decode_turn_snapshots(odd)[0].files == (("a", "h"),)
    plain = [None, [[None] * 8 + [["model", "no prefix", None, None, []]]]]
    turn = decode_turn_snapshots(plain)[0]
    assert (turn.prompt, turn.saved, turn.files) == ("no prefix", None, ())


def test_a_malformed_file_entry_is_drift_not_a_silent_gap():
    bad = [None, [[None] * 8 + [["model", "Snapshot from x", None, ["5", 0], [["a"]]]]]]
    with pytest.raises(SchemaDriftError):
        decode_turn_snapshots(bad)
