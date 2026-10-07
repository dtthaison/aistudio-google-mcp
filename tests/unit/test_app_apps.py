import httpx
import pytest
import respx

from aistudio._app.apps import gallery_get, gallery_list, get_app, list_apps, recent_apps
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ValidationError

FLUENT = "28a7fe28-01ab-4c19-952d-4cc266207ec4"


def serve(captured, method, n=1):
    return respx.post(method_url(method)).mock(
        return_value=httpx.Response(200, text=captured(method, n).raw["response"]["body"])
    )


@respx.mock
async def test_list_and_get_an_applet_with_its_runs(make_client, captured):
    route_list = serve(captured, "ListApplets", 4)
    route_runs = serve(captured, "ListAppletTrajectories")
    async with make_client() as client:
        listed = await list_apps(client, limit=50)
        app = await get_app(client, "28a7")
    assert listed["count"] == 4
    assert route_list.calls[0].request.content == b"[[50],[[]],0]"
    assert app["id"] == FLUENT and app["title"] == "FluentFlow"
    assert app["runs"] == [{"id": FLUENT, "prompt": "leaning english",
                            "started": "2026-10-06T05:05:30Z",
                            "updated": "2026-10-06T05:05:30Z"}]
    assert route_runs.calls.last.request.content == f'["{FLUENT}"]'.encode()


@respx.mock
async def test_drive_app_never_asks_for_runs(make_client, captured):
    serve(captured, "ListApplets", 4)  # no runs route: any runs request fails as "not mocked"
    async with make_client() as client:
        app = await get_app(client, "prompt_6")
    assert (app["storage"], app["runs"]) == ("drive", [])


@respx.mock
async def test_unknown_app_is_not_found(make_client, captured):
    serve(captured, "ListApplets", 4)
    async with make_client() as client:
        with pytest.raises(NotFoundError) as info:
            await get_app(client, "zzz")
    assert info.value.hint == 'see "aistudio app list"'


@respx.mock
async def test_recent(make_client, captured):
    serve(captured, "ListRecentApplets", 7)
    async with make_client() as client:
        data = await recent_apps(client)
    assert data["count"] == 9
    assert data["recent"][0]["kind"] == "app"


@respx.mock
async def test_gallery_filters_and_prefix_lookup(make_client, captured):
    serve(captured, "ListBundledApplets", 3)
    async with make_client() as client:
        templates = await gallery_list(client, group="template")
        maps = await gallery_list(client, tag="google-maps")
        lyria = await gallery_list(client, search="LYRIA")
        store = await gallery_get(client, "store")
        with pytest.raises(ValidationError) as info:
            await gallery_get(client, "genai_sdk")
    assert templates["count"] == 14
    assert maps["count"] > 0 and all("google-maps" in a["tags"] for a in maps["apps"])
    assert {"lyria-studio", "lyria-rhythm", "lyria_camera"} <= {a["slug"] for a in lyria["apps"]}
    assert store["slug"] == "store-locator"
    assert "genai_sdk_chats" in info.value.message


def test_pick_ignores_case_but_prefers_an_exact_match():
    from aistudio._app.apps import pick

    ids = ["abc1", "ABC", "Zeta-9"]
    assert pick(ids, str, "ABC", noun="app", hint="h") == "ABC"
    assert pick(ids, str, "ABC1", noun="app", hint="h") == "abc1"
    assert pick(ids, str, "zeta", noun="app", hint="h") == "Zeta-9"


def test_a_blank_id_is_rejected_not_matched_to_everything():
    from aistudio._app.apps import pick
    from aistudio.exceptions import ValidationError

    with pytest.raises(ValidationError):
        pick(["a", "b"], str, "  ", noun="app", hint="h")
