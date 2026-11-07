"""``diff_app``: what a builder turn changed, or how the app differs from a local folder."""

import hashlib

import httpx
import pytest
import respx

from aistudio._app.apps import diff_app
from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ServerError, ValidationError

APP = "d6d4717c-30c3-4564-ac77-50cb262d3884"
STAMP = ["9", 0]

V1 = {"index.html": b"<html></html>\n", "src/App.tsx": b"export default 1;\n"}
V2 = {"index.html": b"<html></html>\n", "src/App.tsx": b"export default 2;\n",
      "src/new.ts": b"export {};\n"}
V3 = {"src/App.tsx": b"export default 2;\n", "src/new.ts": b"export const a = 1;\n"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def step(files=None, prompt="p"):
    snapshot = ["model", f"Snapshot from {prompt}", None, ["1790000000", 0],
                [[path, sha(data)] for path, data in files.items()]] if files else None
    return [None] * 8 + [snapshot]


def history_pages(turns, *, per_page=2):
    """Newest page first, as the server sends them: each page's steps are oldest first."""
    steps = []
    for files, prompt in turns:
        steps += [step(), step(files, prompt)]  # a plain step, then the turn's snapshot step
    return [steps[max(0, end - per_page):end] for end in range(len(steps), 0, -per_page)]


def serve(turns, blobs, *, per_page=2, corrupt=None, router=respx):
    applet = [None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]
    router.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([[applet]])))
    pages = history_pages(turns, per_page=per_page)
    seen = []

    def history(request):
        offset = wire.decode(request.content.decode())[4]
        seen.append(offset)
        index = 0 if offset is None else int(offset)
        if index >= len(pages):
            return httpx.Response(200, text=wire.encode([None, []]))
        more = [str(index + 1)] if index + 1 < len(pages) else []
        return httpx.Response(200, text=wire.encode([None, pages[index], *more]))

    router.post(method_url("LoadCodeAssistantInteractionHistory")).mock(side_effect=history)
    latest = turns[-1][0]
    router.post(method_url("GetApplet")).mock(return_value=httpx.Response(
        200, text=wire.encode([[APP, None, None, None, None, None, STAMP], APP])))
    router.post(method_url("GetCodeAssistantSnapshot")).mock(return_value=httpx.Response(
        200, text=wire.encode([["model", "s", None, STAMP,
                                [[p, sha(d)] for p, d in latest.items()]]])))
    gets = {}
    for data in blobs:
        body = b"tampered" if data == corrupt else data
        gets[sha(data)] = router.get(
            f"https://aistudio.google.com/_/upload/{APP}/file/{sha(data)}").mock(
            return_value=httpx.Response(200, content=body))
    return seen, gets


def everything(*versions):
    return [d for v in versions for d in v.values()]


TURNS = [(V1, "make it"), (V2, "add a file"), (V3, "drop the html")]


@respx.mock
async def test_the_last_turn_is_compared_with_the_one_before(make_client):
    serve(TURNS, everything(V1, V2, V3))
    async with make_client() as client:
        result = await diff_app(client, "d6d4")
    assert (result["app_id"], result["title"], result["mode"]) == (APP, "Quotes", "turn")
    assert (result["turn"], result["turns"], result["prompt"]) == (3, 3, "drop the html")
    assert result["summary"] == {"added": 0, "removed": 1, "changed": 1,
                                 "lines_added": 1, "lines_removed": 2}
    paths = {f["path"]: f for f in result["files"]}
    assert set(paths) == {"index.html", "src/new.ts"}
    assert paths["index.html"]["status"] == "removed"
    assert "+export const a = 1;" in paths["src/new.ts"]["diff"]


@respx.mock
async def test_turn_n_and_the_first_turn_against_nothing(make_client):
    serve(TURNS, everything(V1, V2, V3))
    async with make_client() as client:
        second = await diff_app(client, APP, turn=2)
        first = await diff_app(client, APP, turn=1)
    assert second["prompt"] == "add a file"
    assert second["summary"] == {"added": 1, "removed": 0, "changed": 1,
                                 "lines_added": 2, "lines_removed": 1}
    assert first["summary"]["added"] == 2 and first["summary"]["changed"] == 0


@respx.mock
async def test_only_changed_files_are_downloaded(make_client):
    _, gets = serve(TURNS, everything(V1, V2, V3))
    async with make_client() as client:
        await diff_app(client, APP, turn=2)
    called = {digest for digest, route in gets.items() if route.called}
    assert called == {sha(V1["src/App.tsx"]), sha(V2["src/App.tsx"]), sha(V2["src/new.ts"])}


@respx.mock
async def test_history_is_read_page_by_page_until_the_last(make_client):
    seen, _ = serve(TURNS, everything(V1, V2, V3), per_page=2)
    async with make_client() as client:
        result = await diff_app(client, APP, turn=1)
    assert result["turns"] == 3
    assert seen == [None, "1", "2"]  # each page's token is the next page's offset


@respx.mock
async def test_a_turn_out_of_range_is_refused(make_client):
    serve(TURNS, everything(V1, V2, V3))
    async with make_client() as client:
        for bad in (0, 4):
            with pytest.raises(ValidationError) as info:
                await diff_app(client, APP, turn=bad)
            assert "3 turns" in info.value.message


@respx.mock
async def test_an_app_with_no_turn_snapshots_points_at_against(make_client):
    router = respx
    router.post(method_url("ListApplets")).mock(return_value=httpx.Response(200, text=wire.encode(
        [[[None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]]])))
    router.post(method_url("LoadCodeAssistantInteractionHistory")).mock(
        return_value=httpx.Response(200, text=wire.encode([None, [step()]])))
    async with make_client() as client:
        with pytest.raises(NotFoundError) as info:
            await diff_app(client, APP)
    assert "--against" in info.value.hint


@respx.mock
async def test_a_corrupted_download_is_an_error_not_a_diff(make_client):
    serve(TURNS, everything(V1, V2, V3), corrupt=V2["src/App.tsx"])
    async with make_client() as client:
        with pytest.raises(ServerError):
            await diff_app(client, APP, turn=2)


@respx.mock
async def test_an_unsafe_path_in_a_snapshot_is_refused_before_downloading(make_client):
    evil = {"../../etc/passwd": b"x\n"}
    _, gets = serve([(evil, "bad")], everything(evil))
    async with make_client() as client:
        with pytest.raises(ValidationError):
            await diff_app(client, APP)
    assert not any(route.called for route in gets.values())


async def test_drive_apps_are_refused(make_client):
    drive = [[[[["Old", "", "p", None, "default"]], None, None, "1" + "d" * 32]]]
    with respx.mock(assert_all_called=False) as router:
        router.post(method_url("ListApplets")).mock(
            return_value=httpx.Response(200, text=wire.encode([drive])))
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await diff_app(client, "1ddd")


@respx.mock
async def test_against_a_folder_compares_the_current_code_with_it(make_client, tmp_path):
    serve(TURNS, everything(V1, V2, V3))
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_bytes(b"export default 1;\n")
    (tmp_path / "index.html").write_bytes(b"<html></html>\n")
    (tmp_path / "notes.txt").write_bytes(b"mine\n")
    async with make_client() as client:
        result = await diff_app(client, APP, against=tmp_path)
    assert (result["mode"], result["against"], result["turn"]) == ("folder", str(tmp_path), None)
    by_path = {f["path"]: f["status"] for f in result["files"]}
    assert by_path == {"index.html": "removed", "src/App.tsx": "changed",
                       "src/new.ts": "added", "notes.txt": "removed"}
    app_diff = next(f for f in result["files"] if f["path"] == "src/App.tsx")["diff"]
    assert "-export default 1;" in app_diff and "+export default 2;" in app_diff


@respx.mock
async def test_against_an_identical_export_is_empty_and_downloads_nothing(make_client, tmp_path):
    _, gets = serve(TURNS, everything(V3))
    for path, data in V3.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    async with make_client() as client:
        result = await diff_app(client, APP, against=tmp_path)
    assert result["files"] == []
    assert result["summary"] == {"added": 0, "removed": 0, "changed": 0,
                                 "lines_added": 0, "lines_removed": 0}
    assert not any(route.called for route in gets.values())


@respx.mock
async def test_against_skips_git_and_node_modules_and_links(make_client, tmp_path):
    serve(TURNS, everything(V3))
    for path, data in V3.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_bytes(b"x")
    (tmp_path / "node_modules/pkg").mkdir(parents=True)
    (tmp_path / "node_modules/pkg/i.js").write_bytes(b"x")
    (tmp_path / "link.txt").symlink_to(tmp_path / "src/App.tsx")
    async with make_client() as client:
        result = await diff_app(client, APP, against=tmp_path)
    assert result["files"] == []


async def test_against_must_be_an_existing_folder(make_client, tmp_path):
    (tmp_path / "f").write_text("x")
    with respx.mock(assert_all_called=False):
        async with make_client() as client:
            for bad in (tmp_path / "missing", tmp_path / "f"):
                with pytest.raises(ValidationError):
                    await diff_app(client, APP, against=bad)


async def test_turn_and_against_do_not_mix(make_client, tmp_path):
    with respx.mock(assert_all_called=False):
        async with make_client() as client:
            with pytest.raises(ValidationError):
                await diff_app(client, APP, turn=2, against=tmp_path)


@respx.mock
async def test_a_history_that_never_ends_is_an_error(make_client):
    respx.post(method_url("ListApplets")).mock(return_value=httpx.Response(200, text=wire.encode(
        [[[None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]]])))
    respx.post(method_url("LoadCodeAssistantInteractionHistory")).mock(
        return_value=httpx.Response(200, text=wire.encode([None, [step()], "100"])))
    async with make_client() as client:
        with pytest.raises(ServerError):
            await diff_app(client, APP)
