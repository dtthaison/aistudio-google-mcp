import pytest

from aistudio._rpc import registry
from aistudio._rpc.params.apps import list_app_runs, list_apps, list_gallery, list_recent_apps
from aistudio._rpc.rows import as_timestamp
from aistudio._rpc.rows.apps import (
    decode_apps,
    decode_apps_checked,
    decode_gallery,
    decode_recent,
    decode_runs,
)
from aistudio._types.apps import App, AppRun, GalleryApp
from aistudio.exceptions import SchemaDriftError

FLUENT = "28a7fe28-01ab-4c19-952d-4cc266207ec4"


def test_requests_match_captures(captured):
    assert list_apps() == captured("ListApplets", 4).request
    assert list_recent_apps() == captured("ListRecentApplets", 7).request
    assert list_gallery() == captured("ListBundledApplets", 3).request
    assert list_app_runs(FLUENT) == captured("ListAppletTrajectories").request


def test_build_reads_are_plain_http():
    for spec in (registry.LIST_APPLETS, registry.LIST_RECENT_APPLETS,
                 registry.LIST_BUNDLED_APPLETS, registry.LIST_APPLET_TRAJECTORIES):
        assert spec.transport is registry.Transport.HTTP and spec.idempotent


def test_decodes_both_app_shapes(captured):
    apps = decode_apps(captured("ListApplets", 4).response)
    assert len(apps) == 4
    assert apps[0] == App(
        id=FLUENT, title="FluentFlow",
        description="Master English through interactive lessons, personalized practice, and "
                    "real-world conversation exercises designed for all skill levels.",
        prompt="leaning english", created="2026-10-06T05:05:29Z",
        updated="2026-10-06T05:05:36Z", storage="applet",
    )
    assert apps[1].description is None
    drive = apps[3]
    assert (drive.id, drive.title, drive.storage) == ("prompt_6", "FitNexus Admin", "drive")
    assert drive.prompt == "build admin page for fitness gym \n"
    assert (drive.created, drive.updated) == (None, "2026-01-09T17:02:44Z")
    assert drive.description.startswith("A modern, AI-powered administration dash")


def test_empty_account_and_missing_ids():
    assert decode_apps([]) == []
    assert decode_apps(None) == []
    with pytest.raises(SchemaDriftError):
        decode_apps(["not a list"])


def test_an_entry_without_an_id_is_skipped_and_counted(captured):
    good = captured("ListApplets", 4).response[0][0]
    apps, skipped = decode_apps_checked([[[None, [None, None, None, ["no id"]]], good]])
    assert [a.id for a in apps] == [decode_apps([[good]])[0].id]
    assert skipped == 1


def test_recent_kind_comes_from_the_filled_slot(captured):
    items = decode_recent(captured("ListRecentApplets", 7).response)
    assert [(i.kind, i.id) for i in items] == [
        ("app", FLUENT), ("gallery", "relocation_matcher"),
        ("gallery", "pet-grooming-route-optimizer"),
        ("app", "b46fa0cd-d34f-49c8-92e4-76f74ca6e74a"), ("prompt", "prompt_2"),
        ("app", "fb02c7b2-7b75-4ef4-a9cc-f320b5a6bb72"), ("gallery", "pixshop"),
        ("prompt", "prompt_3"), ("gallery", "genai_videos_with_veo"),
    ]
    assert (items[0].title, items[0].opened) == ("Untitled", "2026-10-06T05:05:31Z")
    assert decode_recent([[[["x"], "junk"]]])[0].kind == "unknown"


def test_decodes_the_gallery(captured):
    apps = decode_gallery(captured("ListBundledApplets", 3).response)
    assert len(apps) == 94
    assert apps[0] == GalleryApp(
        slug="store-locator", title="Find Near Me – Store Locator",
        description="Interactive Google Maps vector store locator with Places API (New), "
                    "distance filtering, live product inventory check, and branch details.",
        group="showcase", apis=("Google Maps API", "Places API"),
        tags=("showcase", "google-maps"),
        video_url="https://www.gstatic.com/aistudio-static/applets/store-locator/video.mp4",
        image_url="https://www.gstatic.com/aistudio-static/applets/store-locator/image.png",
    )
    templates = [a for a in apps if a.group == "template"]
    assert len(templates) == 14
    assert (templates[0].slug, templates[0].title, templates[0].apis) == ("blank", "Empty", ())
    assert decode_gallery([[["g", [[None, "no slug"], ["ok"]]]]]) == [
        GalleryApp(slug="ok", title=None, description=None, group="g")
    ]


def test_decodes_runs(captured):
    assert decode_runs(captured("ListAppletTrajectories").response) == [
        AppRun(id=FLUENT, prompt="leaning english", started="2026-10-06T05:05:30Z",
               updated="2026-10-06T05:05:30Z")
    ]
    assert decode_runs([]) == []


def test_as_timestamp():
    assert as_timestamp(["1791263129", 926446000]) == "2026-10-06T05:05:29Z"
    assert as_timestamp([1791263129]) == "2026-10-06T05:05:29Z"
    assert as_timestamp(["x"]) is None
    assert as_timestamp(None) is None


def test_to_dict_lists_tuples(captured):
    data = decode_gallery(captured("ListBundledApplets", 3).response)[0].to_dict()
    assert data["apis"] == ["Google Maps API", "Places API"]
    assert data["tags"] == ["showcase", "google-maps"]
