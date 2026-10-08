import contextlib
import json
from datetime import datetime

import pytest

from aistudio._app.flow import (
    STATE_FILE,
    flow_character,
    flow_download,
    flow_extend,
    flow_generate,
    flow_media,
    flow_projects,
)
from aistudio._flow.page import AgentTurn, CharacterOutcome, Defaults, FlowOutcome
from aistudio._flow.rows import FlowMedia, FlowProject, ProjectContents, Quote
from aistudio.exceptions import NotFoundError, OverBudgetError, ValidationError

PID = "72df29b1-e119-4741-ad44-ba18bc1ad34a"
VID = "3fc10f7b-49f4-4e72-89f8-620e60a8ae36"
IMG = "91f49a6c-9c8e-4646-96ca-868d0a9d5454"
NOW = datetime(2026, 10, 8, 9, 0, 0)


def media(media_id, *, video=True, status=3, seconds=8):
    base = f"https://flow-content.google/{{}}/{media_id}?Signature=s"
    return FlowMedia(media_id=media_id, project_id=PID, prompt="p", model_key="veo_3_1_t2v_lite",
                     status=status, created=1791420849, seconds=seconds if video else None,
                     size=3, video_url=base.format("video") if video and status == 3 else None,
                     image_url=base.format("image") if status == 3 else None)


CONTENTS = ProjectContents({VID: "Red apple rolling on table", IMG: "apple.jpg"},
                           [media(VID), media(IMG, video=False)])


class FakePage:
    def __init__(self, ui, *, contents=CONTENTS, credits=(1030, 1020), label="Veo 3.1 - Lite",
                 character_model="Nano Banana 2.1"):
        self.calls, self.contents_value, self.label = [], contents, label
        self.credit_values, self.character_model_label = list(credits), character_model
        self.paid_media = []

    async def open_project(self, pid):
        self.calls.append(("open", pid))

    async def create_project(self, title):
        return PID

    async def projects(self):
        return [FlowProject(PID, "aistudio", 1791371089)]

    async def contents(self, pid):
        self.calls.append(("contents", pid))
        return self.contents_value

    async def credits(self):
        return self.credit_values.pop(0) if len(self.credit_values) > 1 else self.credit_values[0]

    async def read_defaults(self, kind):
        return Defaults("Omni 1.1 Flash", "16:9", 1, asks=True)

    async def apply_defaults(self, kind, wanted):
        self.calls.append(("apply", wanted))

    async def new_session(self):
        self.calls.append(("session",))

    async def upload(self, path):
        self.calls.append(("upload", path.name))
        return "up1"

    async def attach(self, title):
        self.calls.append(("attach", title))

    async def submit_for_quote(self, text, *, timeout_s=90.0):
        self.calls.append(("submit", text))
        return AgentTurn("kick off this 1 video generation, costing 10 credits?",
                         Quote(1, "video", 10))

    async def reject(self):
        self.calls.append(("reject",))

    async def extend_label(self, title):
        self.calls.append(("extend_label", title))
        return self.label

    async def extend(self, title, prompt, *, timeout_s):
        self.calls.append(("extend", title, prompt))
        return FlowOutcome([media("m2")], None, False)

    async def character_model(self, pid):
        return self.character_model_label

    async def create_character(self, pid, description):
        self.calls.append(("character", description))
        return CharacterOutcome("c1", [media("i9", video=False)])


def harness(**page_kw):
    pages, opened = [], []

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        opened.append(window)
        yield object()

    def factory(ui):
        pages.append(FakePage(ui, **page_kw))
        return pages[-1]

    async def downloader(url):
        return b"\x00\x00\x00\x18ftypmp4" if "/video/" in url else b"\x89PNG..."

    return ({"browser": browser, "page_factory": factory, "downloader": downloader,
             "now": lambda: NOW}, pages, opened)


def store(profile):
    profile.ensure()
    (profile.root / STATE_FILE).write_text(json.dumps({"project_id": PID}))


async def test_projects_and_media_lists(logged_in_profile):
    profile = logged_in_profile()
    store(profile)
    kw, _, _ = harness()
    kw.pop("downloader"), kw.pop("now")
    projects = await flow_projects(profile, **kw)
    assert projects["projects"][0]["title"] == "aistudio" and projects["count"] == 1
    videos = await flow_media(profile, kind="video", **kw)
    assert [(m["media_id"], m["title"], m["kind"]) for m in videos["media"]] == [
        (VID, "Red apple rolling on table", "video")]


async def test_download_needs_a_finished_media(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    running = ProjectContents({VID: "Red apple"}, [media(VID, status=2)])
    kw, _, _ = harness(contents=running)
    with pytest.raises(ValidationError) as info:
        await flow_download(profile, VID, out=tmp_path / "a.mp4", **kw)
    assert "no download yet" in info.value.message
    kw, _, _ = harness()
    result = await flow_download(profile, VID, out=tmp_path / "a.mp4", **kw)
    assert (tmp_path / "a.mp4").exists() and result["files"][0]["media_id"] == VID


async def test_local_files_need_i_have_rights(logged_in_profile, tmp_path):
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"\xff\xd8x")
    kw, _, opened = harness()
    with pytest.raises(ValidationError) as info:
        await flow_generate(logged_in_profile(), "video", "an apple", start_frame=str(photo),
                            out=tmp_path / "a.mp4", **kw)
    assert "--i-have-rights" in info.value.hint
    assert opened == []


async def test_unknown_attachment_ids_fail_before_submitting(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness()
    with pytest.raises(NotFoundError):
        await flow_generate(profile, "video", "an apple",
                            ingredients=["00000000-0000-0000-0000-000000000000"],
                            out=tmp_path / "a.mp4", **kw)
    assert not any(c[0] == "submit" for c in pages[0].calls)


async def test_attachments_are_uploaded_attached_and_named_in_order(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"\xff\xd8x")
    kw, pages, _ = harness()
    await flow_generate(profile, "video", "an apple", start_frame=str(photo), ingredients=[IMG],
                        i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    calls = pages[0].calls
    assert ("upload", "me.jpg") in calls
    assert [c for c in calls if c[0] == "attach"] == [("attach", "me.jpg"), ("attach", "apple.jpg")]
    submit = next(c[1] for c in calls if c[0] == "submit")
    assert "as: start frame, ingredient." in submit


async def test_extend_checks_the_price_before_clicking(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness()
    with pytest.raises(OverBudgetError):
        await flow_extend(profile, VID, "rolls off", out=tmp_path / "x.mp4", **kw)
    assert not any(c[0] == "extend" for c in pages[0].calls)
    kw, pages, _ = harness(label="Veo 3.1 - Fast")
    with pytest.raises(ValidationError):
        await flow_extend(profile, VID, "rolls off", max_credits=100, out=tmp_path / "x.mp4", **kw)
    assert not any(c[0] == "extend" for c in pages[0].calls)


async def test_a_cost_different_from_the_table_is_a_warning(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, _, _ = harness(credits=(1030, 1018))
    result = await flow_extend(profile, VID, "rolls off", max_credits=10,
                               out=tmp_path / "x.mp4", **kw)
    assert result["status"] == "done" and result["credits_spent"] == 12
    assert result["warnings"] == ["Flow charged 12 credits; aistudio's table says 10"]
    assert (tmp_path / "x.mp4").exists() and result["files"][0]["media_id"] == "m2"


async def test_character_is_free_and_downloads_its_image(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness(credits=(1020, 1020))
    result = await flow_character(profile, "a robot", out=tmp_path / "robot.png", **kw)
    assert result["character_id"] == "c1" and result["credits_spent"] == 0
    assert (tmp_path / "robot.png").exists()
    kw, pages, _ = harness(character_model="Nano Banana Pro")
    with pytest.raises(ValidationError):
        await flow_character(profile, "a robot", out=tmp_path / "r2.png", **kw)
    assert not any(c[0] == "character" for c in pages[0].calls)
