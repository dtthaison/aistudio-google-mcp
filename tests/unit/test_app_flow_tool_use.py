import contextlib
from types import SimpleNamespace

import pytest

from aistudio._app.flow_tools import flow_tool_use
from aistudio._flow.rows import FlowTool, ToolFile
from aistudio._flow.tool_use import Control
from aistudio.exceptions import BrowserError, ValidationError

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
MINE = FlowTool("aa11", "Chart Maker", "Image to chart", "v1", "mine", None, None, [])
WHISK = FlowTool("bb22", "Whisk", "Images as prompts", "v2", "template", "Google", 9254, [])
SAFE = [ToolFile("App.tsx", None, "await Flow.download({});\nFlow.media.select({});\n")]
SPENDS = [ToolFile("App.tsx", None, "await Flow.generate.image({prompt});\n")]
CONTROLS = [Control(1, "button", "PNG"), Control(2, "file", "Upload File", accept="image/*"),
            Control(3, "range", "Chart Width (Stitches)", value="60", min="20", max="200")]


class FakePage:
    def __init__(self, ui, tools=(MINE, WHISK), files=SAFE, credits=(100, 96)):
        self.listed, self.files, self.credit_reads = list(tools), files, list(credits)
        self.calls = []

    async def open_project(self, project_id):
        self.calls.append(("open", project_id))

    async def tools(self):
        return self.listed

    async def tool_files(self, tool_id, version_id):
        self.calls.append(("files", tool_id))
        return self.files

    async def credits(self):
        self.calls.append(("credits",))
        return self.credit_reads.pop(0)


class FakeSession:
    def __init__(self, ui):
        self.calls = []
        self.screens = []  # (controls, text) shown one after another while a step waits

    async def open(self, url, *, timeout_s=60.0):
        self.calls.append(("open", url))

    async def controls(self):
        if self.screens:
            return self.screens[0][0]
        return CONTROLS

    async def act(self, control, value):
        self.calls.append(("act", control.n, value))
        return value

    async def settle(self, *, timeout_s=30.0, quiet_s=1.2):
        self.calls.append(("settle", timeout_s))

    async def text(self):
        if self.screens:
            return self.screens.pop(0)[1]
        return "Chart Maker\n60 sts"

    async def save_downloads(self, folder, *, force):
        self.calls.append(("save", folder, force))
        return [{"path": str(folder / "bear.png"), "bytes": 9, "name": "bear.png"}]


def harness(**page):
    made = {}

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        made["window"] = window
        yield object()

    def page_factory(ui):
        made["page"] = FakePage(ui, **page)
        return made["page"]

    def session_factory(ui):
        made["session"] = FakeSession(ui)
        return made["session"]

    return {"browser": browser, "page_factory": page_factory,
            "session_factory": session_factory}, made


async def test_without_steps_it_lists_the_controls(logged_in_profile, tmp_path):
    kw, made = harness()
    got = await flow_tool_use(logged_in_profile(), "aa11", [], tmp_path, project=PID, **kw)
    assert made["window"] == "headless"
    assert made["session"].calls == [
        ("open", f"https://flow.google.com/project/{PID}/tool/aa11")]
    assert got["controls"][2] == {"n": 3, "kind": "range", "label": "Chart Width (Stitches)",
                                  "value": "60", "min": "20", "max": "200", "accept": None,
                                  "options": None, "disabled": False}
    assert got["steps"] == [] and got["files"] == []
    assert got["text"] == "Chart Maker\n60 sts"
    assert got["may_spend_credits"] is False and "credits_spent" not in got


async def test_steps_run_in_order_and_downloads_are_saved(logged_in_profile, tmp_path):
    kw, made = harness()
    got = await flow_tool_use(logged_in_profile(), "aa11", ["2=bear.png", "Chart Width=40", "PNG"],
                              tmp_path, project=PID, force=True, step_timeout_s=20.0, **kw)
    assert made["session"].calls[1:] == [
        ("act", 2, "bear.png"), ("settle", 20.0), ("act", 3, "40"), ("settle", 20.0),
        ("act", 1, None), ("settle", 20.0), ("save", tmp_path, True)]
    assert got["steps"] == [
        {"step": "2=bear.png", "control": 2, "label": "Upload File", "kind": "file",
         "value": "bear.png"},
        {"step": "Chart Width=40", "control": 3, "label": "Chart Width (Stitches)",
         "kind": "range", "value": "40"},
        {"step": "PNG", "control": 1, "label": "PNG", "kind": "button", "value": None}]
    assert got["files"] == [{"path": str(tmp_path / "bear.png"), "bytes": 9, "name": "bear.png"}]


async def test_a_bad_step_is_refused_before_chrome_opens(logged_in_profile, tmp_path):
    kw, made = harness()
    with pytest.raises(ValidationError, match="empty step"):
        await flow_tool_use(logged_in_profile(), "aa11", ["=3"], tmp_path, project=PID, **kw)
    assert made == {}


async def test_a_tool_that_can_spend_credits_needs_allow_spend(logged_in_profile, tmp_path):
    kw, made = harness(files=SPENDS)
    with pytest.raises(ValidationError, match="Flow.generate.image") as err:
        await flow_tool_use(logged_in_profile(), "aa11", ["PNG"], tmp_path, project=PID, **kw)
    assert "--allow-spend" in err.value.hint
    assert "session" not in made


async def test_allow_spend_reports_the_credits_used(logged_in_profile, tmp_path):
    kw, made = harness(files=SPENDS)
    got = await flow_tool_use(logged_in_profile(), "aa11", ["PNG"], tmp_path, project=PID,
                              allow_spend=True, **kw)
    assert got["may_spend_credits"] is True
    assert (got["credits_before"], got["credits_after"], got["credits_spent"]) == (100, 96, 4)


async def test_a_template_needs_allow_remix(logged_in_profile, tmp_path):
    kw, made = harness()
    with pytest.raises(ValidationError, match="Remix of Whisk"):
        await flow_tool_use(logged_in_profile(), "bb22", [], tmp_path, project=PID, **kw)
    assert "session" not in made
    assert ("files", "bb22") not in made["page"].calls


async def test_visible_shows_the_window(logged_in_profile, tmp_path):
    kw, made = harness()
    await flow_tool_use(logged_in_profile(), "aa11", [], tmp_path, project=PID, visible=True, **kw)
    assert made["window"] == "visible"


SAVES = [ToolFile("App.tsx", None, "await Flow.media.select({});\nawait Flow.save({base64});\n")]


def media(media_id, video_url=None):
    return SimpleNamespace(media_id=media_id, video_url=video_url, image_url=None, seconds=None,
                           model_key=None)


class SavingPage(FakePage):
    def __init__(self, ui, **kw):
        super().__init__(ui, files=SAVES, **kw)
        self.listings = [[media("old")], [media("old")], [media("old"), media("new")]]

    async def contents(self, project_id):
        self.calls.append(("contents", project_id))
        listed = self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]
        return SimpleNamespace(media=listed, titles={})

    async def media_details(self, media_id):
        return media(media_id, video_url=f"https://storage/{media_id}.mp4")


class DroppingSession(FakeSession):
    async def drop(self, path):
        self.calls.append(("drop", path))


def saving_harness():
    kw, made = harness()

    def page_factory(ui):
        made["page"] = SavingPage(ui)
        return made["page"]

    def session_factory(ui):
        made["session"] = DroppingSession(ui)
        return made["session"]

    kw.update(page_factory=page_factory, session_factory=session_factory)
    return kw, made


async def no_pause(seconds):
    return None


async def fake_download(url):
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16


async def test_a_dropped_file_reaches_the_tool_before_the_steps(logged_in_profile, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    kw, made = saving_harness()
    await flow_tool_use(logged_in_profile(), "aa11", ["PNG"], tmp_path, project=PID, drop=clip,
                        downloader=fake_download, pause=no_pause, **kw)
    assert made["session"].calls[:3] == [
        ("open", f"https://flow.google.com/project/{PID}/tool/aa11"), ("drop", clip),
        ("settle", 60.0)]



async def test_a_missing_drop_file_is_refused_before_chrome_opens(logged_in_profile, tmp_path):
    kw, made = saving_harness()
    with pytest.raises(ValidationError, match="not a file"):
        await flow_tool_use(logged_in_profile(), "aa11", [], tmp_path, project=PID,
                            drop=tmp_path / "missing.mp4", **kw)
    assert made == {}


async def test_what_the_tool_saves_to_flow_is_downloaded(logged_in_profile, tmp_path):
    kw, made = saving_harness()
    got = await flow_tool_use(logged_in_profile(), "aa11", ["PNG"], tmp_path, project=PID,
                              downloader=fake_download, pause=no_pause, **kw)
    assert [f["media_id"] for f in got["saved_to_flow"]] == ["new"]
    saved = got["saved_to_flow"][0]
    assert saved["kind"] == "video" and saved["path"].endswith(".mp4")
    assert (tmp_path / saved["path"].rsplit("/", 1)[-1]).exists()


async def test_no_steps_means_no_project_check(logged_in_profile, tmp_path):
    kw, made = saving_harness()
    got = await flow_tool_use(logged_in_profile(), "aa11", [], tmp_path, project=PID,
                              downloader=fake_download, **kw)
    assert got["saved_to_flow"] == []
    assert not any(c[0] == "contents" for c in made["page"].calls)


async def test_a_wait_step_polls_until_the_tool_is_ready(logged_in_profile, tmp_path):
    kw, made = harness()
    waits = []

    async def pause(seconds):
        waits.append(seconds)

    original = kw["session_factory"]

    def session_factory(ui):
        session = original(ui)
        busy = [Control(1, "button", "PNG", disabled=True)]
        session.screens = [(busy, "Rendering video..."), (busy, "Rendering video..."),
                           (CONTROLS, "Rendering video...")]
        return session

    got = await flow_tool_use(logged_in_profile(), "aa11", ["@PNG", "PNG"], tmp_path,
                              project=PID, step_timeout_s=600.0, pause=pause,
                              **{**kw, "session_factory": session_factory})
    assert len(waits) == 2
    assert ("act", 1, None) in made["session"].calls
    assert got["steps"][0] == {"step": "@PNG", "wait": "control", "waited_s": 2 * waits[0]}


async def test_a_wait_step_gives_up_after_the_step_timeout(logged_in_profile, tmp_path):
    kw, made = harness()

    async def pause(seconds):
        pass

    original = kw["session_factory"]

    def session_factory(ui):
        session = original(ui)
        session.screens = [([], "Rendering video...")] * 1000
        return session

    with pytest.raises(BrowserError, match="4 / 4") as err:
        await flow_tool_use(logged_in_profile(), "aa11", ["?4 / 4"], tmp_path, project=PID,
                            step_timeout_s=20.0, pause=pause,
                            **{**kw, "session_factory": session_factory})
    assert "--step-timeout" in err.value.hint
    assert "Rendering video" in err.value.hint


async def test_listing_a_tool_that_can_spend_explains_how_to_check_it(logged_in_profile,
                                                                     tmp_path):
    kw, made = harness(files=SPENDS)
    with pytest.raises(ValidationError, match="can spend") as err:
        await flow_tool_use(logged_in_profile(), "aa11", [], tmp_path, project=PID, **kw)
    assert "flow tool export" in err.value.hint and "--allow-spend" in err.value.hint
