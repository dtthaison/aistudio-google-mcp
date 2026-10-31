import contextlib
import json

import pytest

from aistudio._app.flow_tools import (
    flow_tool_create,
    flow_tool_edit,
    flow_tool_export,
    flow_tool_get,
    flow_tools,
)
from aistudio._browser.ui import CapturedResponse
from aistudio._flow.page import FlowPage, ToolBuild
from aistudio._flow.rows import FlowTool, ToolFile
from aistudio.exceptions import BrowserError, NotFoundError, ServerError, ValidationError
from tests.conftest import FakeUi

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
MINE = FlowTool("aa11", "Chart Maker", "Image to chart", "v1", "mine", None, None, [])
WHISK = FlowTool("bb22", "Whisk", "Images as prompts", "v2", "template", "Google", 9254, [])
FORGE = FlowTool("community-cc33", "ThumbnailForge", "Thumbnails", "v3", "community", "A", 857,
                 ["Spotlight"])
EMPTY = FlowTool("dd44", "Unsaved", None, None, "mine", None, None, [])
FILES = [ToolFile("src/App.tsx", "text/typescript-jsx",
                  "import { Flow } from 'flow-sdk';\nFlow.media.select({});\n"
                  "await Flow.generate.image({prompt});\n"),
         ToolFile("index.html", "text/html", "<html></html>")]


class FakePage:
    def __init__(self, ui, tools=(MINE, WHISK, FORGE, EMPTY), files=None):
        self.ui, self.listed, self.files = ui, list(tools), FILES if files is None else files
        self.calls = []

    async def open_project(self, project_id):
        self.calls.append(("open", project_id))

    async def tools(self):
        return self.listed

    async def tool_files(self, tool_id, version_id):
        self.calls.append(("files", tool_id, version_id))
        return self.files


def harness(**page):
    made = []

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        yield object()

    def factory(ui):
        made.append(FakePage(ui, **page))
        return made[-1]

    return {"browser": browser, "page_factory": factory}, made


async def test_list_shows_each_tool_and_who_made_it(logged_in_profile):
    kw, _ = harness()
    got = await flow_tools(logged_in_profile(), project=PID, **kw)
    assert got["count"] == 4 and got["project"] == PID
    assert got["tools"][1] == {"id": "bb22", "name": "Whisk", "description": "Images as prompts",
                               "kind": "template", "author": "Google", "uses": 9254, "tags": [],
                               "version_id": "v2"}


async def test_list_filters_by_kind(logged_in_profile):
    kw, _ = harness()
    got = await flow_tools(logged_in_profile(), project=PID, kind="mine", **kw)
    assert [t["id"] for t in got["tools"]] == ["aa11", "dd44"]


async def test_list_searches_names_and_descriptions(logged_in_profile):
    kw, _ = harness()
    got = await flow_tools(logged_in_profile(), project=PID, search="THUMB", **kw)
    assert [t["id"] for t in got["tools"]] == ["community-cc33"]
    got = await flow_tools(logged_in_profile(), project=PID, search="as prompts", **kw)
    assert [t["id"] for t in got["tools"]] == ["bb22"]


async def test_get_finds_a_tool_by_prefix_and_lists_its_files(logged_in_profile):
    kw, made = harness()
    got = await flow_tool_get(logged_in_profile(), "aa", project=PID, **kw)
    assert got["id"] == "aa11" and got["name"] == "Chart Maker"
    assert got["files"] == [
        {"path": "src/App.tsx", "mime_type": "text/typescript-jsx", "bytes": len(FILES[0].text)},
        {"path": "index.html", "mime_type": "text/html", "bytes": 13}]
    assert ("files", "aa11", "v1") in made[0].calls


async def test_get_names_the_flow_calls_so_spending_is_visible(logged_in_profile):
    kw, _ = harness()
    got = await flow_tool_get(logged_in_profile(), "aa11", project=PID, **kw)
    assert got["flow_calls"] == ["Flow.generate.image", "Flow.media.select"]
    assert got["may_spend_credits"] is True


async def test_a_tool_without_generation_says_it_cannot_spend(logged_in_profile):
    kw, _ = harness(files=[ToolFile("App.tsx", None, "Flow.download({});")])
    got = await flow_tool_get(logged_in_profile(), "aa11", project=PID, **kw)
    assert got["flow_calls"] == ["Flow.download"] and got["may_spend_credits"] is False


async def test_get_of_a_tool_with_no_saved_code_says_so(logged_in_profile):
    kw, _ = harness()
    with pytest.raises(NotFoundError, match="no saved code"):
        await flow_tool_get(logged_in_profile(), "dd44", project=PID, **kw)


async def test_get_of_an_unknown_tool_points_at_the_list(logged_in_profile):
    kw, _ = harness()
    with pytest.raises(NotFoundError) as caught:
        await flow_tool_get(logged_in_profile(), "zz", project=PID, **kw)
    assert "flow tool list" in caught.value.hint


async def test_export_writes_every_file(logged_in_profile, tmp_path):
    kw, _ = harness()
    dest = tmp_path / "tool"
    got = await flow_tool_export(logged_in_profile(), "aa11", dest, project=PID, **kw)
    assert (dest / "src" / "App.tsx").read_text(encoding="utf-8") == FILES[0].text
    assert (dest / "index.html").read_text(encoding="utf-8") == FILES[1].text
    assert got["written"] == 2 and got["path"] == str(dest)


async def test_export_keeps_non_ascii_text(logged_in_profile, tmp_path):
    kw, _ = harness(files=[ToolFile("App.tsx", None, "const label = 'mũi đơn';")])
    await flow_tool_export(logged_in_profile(), "aa11", tmp_path / "t", project=PID, **kw)
    assert "mũi đơn" in (tmp_path / "t" / "App.tsx").read_bytes().decode("utf-8")


async def test_export_refuses_a_folder_with_files_in_it(logged_in_profile, tmp_path):
    (tmp_path / "keep.txt").write_text("mine")
    kw, _ = harness()
    with pytest.raises(ValidationError, match="not empty"):
        await flow_tool_export(logged_in_profile(), "aa11", tmp_path, project=PID, **kw)
    assert not (tmp_path / "index.html").exists()


async def test_force_overwrites_and_keeps_other_files(logged_in_profile, tmp_path):
    (tmp_path / "keep.txt").write_text("mine")
    (tmp_path / "index.html").write_text("old")
    kw, _ = harness()
    await flow_tool_export(logged_in_profile(), "aa11", tmp_path, project=PID, force=True, **kw)
    assert (tmp_path / "index.html").read_text() == "<html></html>"
    assert (tmp_path / "keep.txt").read_text() == "mine"


async def test_export_writes_nothing_when_one_path_is_unsafe(logged_in_profile, tmp_path):
    kw, _ = harness(files=[ToolFile("ok.txt", None, "x"), ToolFile("../evil.txt", None, "x")])
    dest = tmp_path / "tool"
    with pytest.raises(ValidationError, match="unsafe"):
        await flow_tool_export(logged_in_profile(), "aa11", dest, project=PID, **kw)
    assert not dest.exists() and not (tmp_path / "evil.txt").exists()


def batch(rpc, payload):
    return "\n".join([")]}'", "", str(9), json.dumps([["wrb.fr", rpc, json.dumps(payload)]])])


async def test_page_reads_use_calls_and_never_load_a_tool_page():
    row = [None] * 27
    row[0], row[2], row[5], row[12] = "bb22", "Whisk", "v2", 2
    files = [None, None, None, [["App.tsx", "text/typescript-jsx", "x"]]]
    listed = CapturedResponse("tRARke", 200, batch("tRARke", [[row]]), "")
    code = CapturedResponse("ZLaYcd", 200, batch("ZLaYcd", files), "")
    ui = FakeUi(responses={"tRARke": [listed], "ZLaYcd": [code]})
    page = FlowPage(ui)
    (tool,) = await page.tools()
    (file,) = await page.tool_files(tool.id, tool.version_id)
    assert file.path == "App.tsx"
    assert ui.ops == [("batch_rpc", "tRARke", "[]"),
                      ("batch_rpc", "ZLaYcd", '["applets/bb22/versions/v2"]')]


async def test_a_refused_list_is_an_error_not_an_empty_list():
    with pytest.raises(BrowserError, match="HTTP 404"):
        await FlowPage(FakeUi()).tools()


TID = "5c0e7a12-3b4d-4e6f-8a9b-0c1d2e3f4a5b"
BUILT = FlowTool(TID, "Chart Maker", "Image to chart", "v9", "mine", None, None, [])
SAFE = [ToolFile("App.tsx", None, "Flow.media.select({}); Flow.download({});")]


class BuildPage(FakePage):
    def __init__(self, ui, build=None, saved=BUILT, **rest):
        super().__init__(ui, **rest)
        self.build, self.saved, self.sent = build or ToolBuild(TID, False), saved, []

    async def build_tool(self, url, text, *, timeout_s):
        self.sent.append((url, text, timeout_s))
        return self.build

    async def finish_tool(self):
        self.calls.append(("finish",))

    async def saved_tool(self, tool_id, *, not_version=None):
        self.calls.append(("saved", tool_id, not_version))
        return self.saved


def build_harness(**page):
    kw, _ = harness()
    made = []

    def factory(ui):
        made.append(BuildPage(ui, **page))
        return made[-1]

    return {**kw, "page_factory": factory}, made


async def test_create_reports_the_saved_tool(logged_in_profile):
    kw, made = build_harness(files=SAFE)
    got = await flow_tool_create(logged_in_profile(), "a chart maker", project=PID, **kw)
    assert made[0].sent == [(f"https://flow.google.com/project/{PID}/create-tool",
                             "a chart maker", 600.0)]
    assert (got["status"], got["id"], got["name"], got["version_id"]) == (
        "done", TID, "Chart Maker", "v9")
    assert got["url"] == f"https://flow.google.com/project/{PID}/tool/{TID}"
    assert got["may_spend_credits"] is False and got["flow_calls"] == [
        "Flow.download", "Flow.media.select"]


async def test_create_warns_when_the_agent_used_paid_calls(logged_in_profile):
    kw, _ = build_harness()
    got = await flow_tool_create(logged_in_profile(), "x", project=PID, **kw)
    assert got["may_spend_credits"] is True


async def test_a_timeout_leaves_the_build_alone_and_gives_the_link(logged_in_profile):
    kw, made = build_harness(build=ToolBuild(TID, True))
    got = await flow_tool_create(logged_in_profile(), "x", project=PID, **kw)
    assert got == {"status": "timeout", "id": TID, "project": PID,
                   "url": f"https://flow.google.com/project/{PID}/tool/{TID}"}
    assert ("finish",) not in made[0].calls


async def test_a_build_that_saved_nothing_is_an_error(logged_in_profile):
    kw, _ = build_harness(saved=None)
    with pytest.raises(ServerError, match="saved nothing"):
        await flow_tool_create(logged_in_profile(), "x", project=PID, **kw)


async def test_a_blank_description_sends_nothing(logged_in_profile):
    kw, made = build_harness()
    with pytest.raises(ValidationError, match="describe"):
        await flow_tool_create(logged_in_profile(), "  \n ", project=PID, **kw)
    assert made == []


async def test_edit_changes_your_own_tool_and_waits_for_a_new_version(logged_in_profile):
    kw, made = build_harness(files=SAFE)
    got = await flow_tool_edit(logged_in_profile(), "aa11", "add a title", project=PID, **kw)
    assert made[0].sent == [(f"https://flow.google.com/project/{PID}/tool/aa11?mode=EDIT",
                             "add a title", 600.0)]
    assert ("saved", TID, "v1") in made[0].calls and got["status"] == "done"


async def test_edit_refuses_a_tool_that_is_not_yours(logged_in_profile):
    """Opening a template's page makes a remix in the account (findings)."""
    kw, made = build_harness()
    with pytest.raises(ValidationError, match="not your tool"):
        await flow_tool_edit(logged_in_profile(), "bb22", "x", project=PID, **kw)
    assert made[0].sent == []


async def test_an_edit_that_saved_nothing_new_is_an_error(logged_in_profile):
    kw, _ = build_harness(saved=None)
    with pytest.raises(ServerError, match="saved nothing"):
        await flow_tool_edit(logged_in_profile(), "aa11", "x", project=PID, **kw)


async def test_get_refuses_a_template_because_reading_one_makes_a_remix(logged_in_profile):
    """Live: reading Video Resizer's code added "Remix of Video Resizer" to the account."""
    kw, made = harness()
    with pytest.raises(ValidationError, match="Remix of Whisk") as caught:
        await flow_tool_get(logged_in_profile(), "bb22", project=PID, **kw)
    assert "--allow-remix" in caught.value.hint
    assert not [c for c in made[0].calls if c[0] == "files"]


async def test_export_refuses_a_template_and_writes_nothing(logged_in_profile, tmp_path):
    kw, made = harness()
    dest = tmp_path / "tool"
    with pytest.raises(ValidationError, match="Remix of Whisk"):
        await flow_tool_export(logged_in_profile(), "bb22", dest, project=PID, **kw)
    assert not [c for c in made[0].calls if c[0] == "files"] and not dest.exists()


async def test_a_template_can_be_read_when_the_remix_is_accepted(logged_in_profile):
    kw, made = harness()
    got = await flow_tool_get(logged_in_profile(), "bb22", project=PID, allow_remix=True, **kw)
    assert ("files", "bb22", "v2") in made[0].calls and got["kind"] == "template"
    assert "Remix of Whisk" in got["warnings"][0]


async def test_a_community_tool_needs_no_permission(logged_in_profile):
    kw, _ = harness()
    got = await flow_tool_get(logged_in_profile(), "community-cc33", project=PID, **kw)
    assert got["kind"] == "community" and "warnings" not in got
