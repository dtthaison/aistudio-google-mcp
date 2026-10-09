import hashlib

import httpx
import pytest
import respx

from aistudio._app.apps import export_app
from aistudio._rpc import wire
from aistudio._rpc.params.apps import get_app, get_snapshot
from aistudio._rpc.rows.apps import decode_snapshot_files, decode_snapshot_stamp
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ServerError, ValidationError

APP = "d6d4717c-30c3-4564-ac77-50cb262d3884"
STAMP = ("1791281606", 723699000)


def test_snapshot_requests_and_decoders(captured):
    assert get_app(APP) == captured("GetApplet").request
    assert get_snapshot(APP, STAMP) == captured("GetCodeAssistantSnapshot").request
    assert decode_snapshot_stamp(captured("GetApplet").response) == STAMP
    files = decode_snapshot_files(captured("GetCodeAssistantSnapshot").response)
    assert len(files) == 21 and files[0] == (
        ".env.example", "b6d12c728de0aac8b0bc6239e53aa8088c1431bcb4f9a3d61e5e8a2df1c7c400")
    assert decode_snapshot_stamp([[APP, None, None, None, None, None, None]]) is None
    assert decode_snapshot_files([]) == []


def sha(data):
    return hashlib.sha256(data).hexdigest()


FILES = {"index.html": b"<html></html>", "src/App.tsx": b"export default 1;\n"}


def serve(files, *, corrupt=None, apps=None, router=respx):
    """Mock the export calls on ``router``.

    Note: ``@respx.mock(...)`` with arguments creates its own router, so tests that need
    ``assert_all_called=False`` must pass that router here instead of using the global one.
    """
    applet = [None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]
    router.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([apps or [applet]])))
    router.post(method_url("GetApplet")).mock(return_value=httpx.Response(
        200, text=wire.encode([[APP, None, None, None, None, None, list(STAMP)], APP])))
    listing = [[path, sha(data)] for path, data in files.items()]
    router.post(method_url("GetCodeAssistantSnapshot")).mock(return_value=httpx.Response(
        200, text=wire.encode([["model", "s", None, list(STAMP), listing]])))
    gets = []
    for path, data in files.items():
        body = b"tampered" if path == corrupt else data
        gets.append(router.get(f"https://aistudio.google.com/_/upload/{APP}/file/{sha(data)}").mock(
            return_value=httpx.Response(200, content=body)))
    return gets


@respx.mock
async def test_export_writes_verified_files(make_client, tmp_path):
    serve(FILES)
    dest = tmp_path / "out"
    async with make_client() as client:
        result = await export_app(client, "d6d4", dest)
    assert (dest / "src/App.tsx").read_bytes() == FILES["src/App.tsx"]
    assert result["count"] == 2 and result["dir"] == str(dest) and result["title"] == "Quotes"
    assert result["bytes"] == sum(len(d) for d in FILES.values())


@respx.mock
async def test_a_corrupted_file_writes_nothing(make_client, tmp_path):
    serve(FILES, corrupt="src/App.tsx")
    dest = tmp_path / "out"
    async with make_client() as client:
        with pytest.raises(ServerError) as info:
            await export_app(client, APP, dest)
    assert "src/App.tsx" in info.value.message
    assert not dest.exists()


@pytest.mark.parametrize("bad", ["../../.ssh/config", "/etc/passwd", "a/../../b", "", "a\\b"])
async def test_unsafe_paths_are_rejected_before_downloading(make_client, tmp_path, bad):
    with respx.mock(assert_all_called=False) as router:
        gets = serve({**FILES, bad: b"evil"}, router=router)
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await export_app(client, APP, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not any(route.called for route in gets)


@respx.mock
async def test_non_empty_destination_needs_force(make_client, tmp_path):
    serve(FILES)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    async with make_client() as client:
        with pytest.raises(ValidationError) as info:
            await export_app(client, APP, dest)
        assert "--force" in info.value.hint
        await export_app(client, APP, dest, force=True)
    assert (dest / "keep.txt").read_text() == "mine" and (dest / "index.html").exists()


async def test_drive_apps_are_rejected(make_client, tmp_path):
    drive = [[[[["Old", "", "p", None, "default"]], None, None, "1" + "d" * 32]]]
    with respx.mock(assert_all_called=False) as router:
        serve(FILES, apps=drive, router=router)
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await export_app(client, "1ddd", tmp_path / "a")


@respx.mock
async def test_an_app_without_saved_code_is_not_found(make_client, tmp_path):
    applet = [[None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]]
    respx.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([applet])))
    respx.post(method_url("GetApplet")).mock(
        return_value=httpx.Response(200, text=wire.encode([[APP]])))
    async with make_client() as client:
        with pytest.raises(NotFoundError):
            await export_app(client, APP, tmp_path / "b")


@pytest.mark.parametrize("bad", ["a\x00b", "C:x", "x" * 300])
async def test_nul_colon_and_overlong_names_are_rejected(make_client, tmp_path, bad):
    with respx.mock(assert_all_called=False) as router:
        serve({**FILES, bad: b"evil"}, router=router)
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await export_app(client, APP, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("pair", [("README.md", "readme.md"), ("a", "a/b")])
async def test_clashing_paths_are_rejected(make_client, tmp_path, pair):
    with respx.mock(assert_all_called=False) as router:
        serve({pair[0]: b"1", pair[1]: b"2"}, router=router)
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await export_app(client, APP, tmp_path / "out")
    assert not (tmp_path / "out").exists()


async def test_a_symlinked_folder_in_dest_cannot_redirect_writes(make_client, tmp_path):
    outside, dest = tmp_path / "outside", tmp_path / "out"
    outside.mkdir()
    dest.mkdir()
    (dest / "src").symlink_to(outside)
    with respx.mock(assert_all_called=False) as router:
        serve(FILES, router=router)
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await export_app(client, APP, dest, force=True)
    assert list(outside.iterdir()) == []


async def test_an_existing_file_where_a_folder_is_needed_writes_nothing(make_client, tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "src").write_text("i am a file")
    with respx.mock(assert_all_called=False) as router:
        serve(FILES, router=router)
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await export_app(client, APP, dest, force=True)
    assert sorted(p.name for p in dest.iterdir()) == ["src"]


def test_malformed_snapshot_entries_are_schema_drift():
    from aistudio.exceptions import SchemaDriftError

    with pytest.raises(SchemaDriftError):
        decode_snapshot_files([["model", "s", None, None, [["a", "h"], ["b"]]]])


@respx.mock
async def test_export_reports_progress_per_verified_file(make_client, tmp_path):
    serve(FILES)
    seen = []
    async with make_client() as client:
        await export_app(client, "d6d4", tmp_path / "out",
                         progress=lambda done, total: seen.append((done, total)))
    # Deferred minor #24: the count comes first, so a slow export never looks stuck at once
    assert seen[0] == (0, 2) and sorted(seen[1:]) == [(1, 2), (2, 2)]
