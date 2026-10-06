import pytest

from aistudio._rpc import registry
from aistudio._rpc.params.status import list_incidents
from aistudio._rpc.rows.status import decode_incidents
from aistudio._types.status import IncidentUpdate
from aistudio.exceptions import SchemaDriftError


def test_request_and_registry(captured):
    assert list_incidents() == captured("ListIncidentsHistory", 6).request
    assert registry.LIST_INCIDENTS.transport is registry.Transport.HTTP


def test_decodes_captured_incidents(captured):
    incidents = decode_incidents(captured("ListIncidentsHistory", 6).response)
    assert len(incidents) == 63
    first = incidents[0]
    assert (first.id, first.title) == (
        "GeminiAPI-voice-replication-20261001",
        "Issues creating replicated voices in Gemini API and AI Studio")
    assert first.products == ("Gemini API", "AI Studio")
    assert (first.state, first.active) == ("resolved", False)
    assert (first.started, first.updated) == ("2026-10-01T14:31:00Z", "2026-10-01T17:20:00Z")
    assert first.message.startswith("Issue has been resolved.")
    assert [u.state for u in first.updates] == ["investigating", "identified", "resolved"]
    live = [i for i in incidents if "Live API" in i.products]
    assert len(live) == 3


def test_unknown_codes_and_active_incidents():
    raw = [[[["x", "Down", 1, [[1, "t", ["1790865060"], "Looking"], [9, "t", ["1790868480"], "?"]],
              1, [7]]]]]
    (incident,) = decode_incidents(raw)
    assert incident.products == ("product:7",)
    assert (incident.state, incident.active) == ("state:9", True)
    assert incident.updates[-1] == IncidentUpdate("state:9", "2026-10-01T15:28:00Z", "?")


@pytest.mark.parametrize("response,count", [([], 0), (None, 0), ([[[]]], 0),
                                            ([[[["no-updates", "t"]]]], 1)])
def test_sparse_responses(response, count):
    assert len(decode_incidents(response)) == count


def test_incident_without_id_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_incidents([[[[None, "t"]]]])


async def test_listing_is_sorted_newest_first(make_client, captured):
    import httpx
    import respx

    from aistudio._app.status import list_incidents
    from aistudio._transport.http import method_url

    with respx.mock:
        respx.post(method_url("ListIncidentsHistory")).mock(return_value=httpx.Response(
            200, text=captured("ListIncidentsHistory", 6).raw["response"]["body"]))
        async with make_client() as client:
            data = await list_incidents(client, limit=63)
    times = [i["updated"] or "" for i in data["incidents"]]
    assert times == sorted(times, reverse=True)
