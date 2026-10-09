import contextlib
import dataclasses
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
    return FlowMedia(media_id=media_id, project_id=PID, prompt="p",
                     model_key="veo_3_1_t2v_lite" if video else None,  # live: images have none
                     status=status, created=1791420849, seconds=seconds if video else None,
                     size=3, video_url=base.format("video") if video and status == 3 else None,
                     image_url=base.format("image") if status == 3 else None)


def listed(m):
    """As the project list sends it (live): no links."""
    return dataclasses.replace(m, video_url=None, image_url=None)


CONTENTS = ProjectContents({VID: "Red apple rolling on table", IMG: "apple.jpg"},
                           [listed(media(VID)), listed(media(IMG, video=False))],
                           {VID: "w1", IMG: "w9"})


class FakePage:
    def __init__(self, ui, *, contents=CONTENTS, credits=(1030, 1020), label="Veo 3.1 - Lite",
                 character_model="Nano Banana 2.1", details=None, turn_media=None,
                 tiles_error=None):
        self.calls, self.contents_value, self.label = [], contents, label
        self.details, self.turn_media, self.tiles_error = details, turn_media, tiles_error
        self.uploaded, self.attached = {}, []  # what Flow would send: the attached media ids
        self.credit_values, self.character_model_label = list(credits), character_model
        self.paid_media, self.started = [], False

    async def open_project(self, pid):
        self.calls.append(("open", pid))

    async def create_project(self, title):
        return PID

    async def media_details(self, media_id):
        self.calls.append(("details", media_id))
        if self.details is not None:
            return self.details
        return media(media_id, video=media_id != IMG)

    async def projects(self):
        return [FlowProject(PID, "aistudio", 1791371089)]

    async def contents(self, pid):
        self.calls.append(("contents", pid))
        return self.contents_value

    async def credits(self):
        value = self.credit_values.pop(0) if len(self.credit_values) > 1 else self.credit_values[0]
        if isinstance(value, Exception):
            raise value
        return value

    async def read_defaults(self, kind):
        model = "Nano Banana 2.1" if kind == "image" else "Omni 1.1 Flash"
        return Defaults(model, "16:9", 1, asks=True)

    async def apply_defaults(self, kind, wanted):
        self.calls.append(("apply", wanted))

    async def new_session(self):
        self.calls.append(("session",))

    async def show_tiles(self, pid, titles):
        self.calls.append(("show_tiles", tuple(titles)))
        if self.tiles_error:
            raise self.tiles_error

    async def approve_and_follow(self, *, count, credits, timeout_s, kind, known=frozenset()):
        self.calls.append(("approve", frozenset(known)))
        return FlowOutcome([media("v9")], 1020, False)

    async def upload(self, path, known_ids=frozenset()):
        self.calls.append(("upload", path.name, frozenset(known_ids)))
        self.uploaded[path.name] = f"up{len([c for c in self.calls if c[0] == 'upload'])}"
        return self.uploaded[path.name]

    async def attach(self, title):
        self.calls.append(("attach", title))
        hidden = {c.media_id for c in self.contents_value.characters}
        by_title = {t: m for m, t in self.contents_value.titles.items() if m not in hidden}
        self.attached.append(self.uploaded.get(title) or by_title[title])

    async def submit_for_quote(self, text, *, timeout_s=90.0):
        self.calls.append(("submit", text))
        media = self.attached if self.turn_media is None else self.turn_media
        return AgentTurn("kick off this 1 video generation, costing 10 credits?",
                         Quote(1, "video", 10), media=tuple(media))

    async def reject(self, *, expect=True):
        self.calls.append(("reject",))
        return 1 if expect else 0

    async def extend_label(self, pid, workflow_id):
        self.calls.append(("extend_label", workflow_id))
        return self.label

    async def extend(self, prompt, *, timeout_s):
        self.calls.append(("extend", prompt))
        self.started = True
        self.paid_media = ["m2"]
        return FlowOutcome([media("m2")], 1021, False)

    async def open_character(self, pid):
        return self.character_model_label

    async def start_character(self, description):
        self.calls.append(("character", description))
        return CharacterOutcome("c1", [media("i9", video=False)])

    async def rename_character(self, pid, cid, name):
        self.calls.append(("rename", cid, name))


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
    assert videos["media"][0]["prompt"] == "p"


async def test_download_needs_a_finished_media(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    running = ProjectContents({VID: "Red apple"}, [listed(media(VID, status=2))], {VID: "w1"})
    kw, _, _ = harness(contents=running, details=media(VID, status=2))
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
    assert [c[1] for c in calls if c[0] == "upload"] == ["me.jpg"]
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
    assert result["warnings"][0] == "credits fell by 12 during this run; aistudio's table says 10"
    assert (tmp_path / "x.mp4").exists() and result["files"][0]["media_id"] == "m2"


async def test_character_is_free_and_downloads_its_image(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness(credits=(1020, 1020))
    result = await flow_character(profile, "Robot", "a robot", out=tmp_path / "robot.png", **kw)
    assert result["character_id"] == "c1" and result["credits_spent"] == 0
    assert (tmp_path / "robot.png").exists()
    kw, pages, _ = harness(character_model="Nano Banana 3")  # a model with no measured price
    with pytest.raises(ValidationError):
        await flow_character(profile, "Robot", "a robot", out=tmp_path / "r2.png", **kw)
    assert not any(c[0] == "character" for c in pages[0].calls)


async def test_a_failed_credit_read_after_extend_is_a_warning_not_an_error(logged_in_profile,
                                                                           tmp_path):
    # Review C1: after the spend, nothing may turn into "re-run".
    from aistudio.exceptions import BrowserError

    profile = logged_in_profile()
    store(profile)
    kw, _, _ = harness(credits=(1030, BrowserError("network blip")))
    result = await flow_extend(profile, VID, "rolls off", max_credits=10,
                               out=tmp_path / "x.mp4", **kw)
    assert result["status"] == "done" and (tmp_path / "x.mp4").exists()
    assert result["credits_left"] == 1021  # from Flow's own reply
    assert any("not verified" in w for w in result["warnings"])


async def test_extend_opens_the_clip_by_its_workflow_id(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness()
    await flow_extend(profile, VID, "rolls off", max_credits=10, out=tmp_path / "x.mp4", **kw)
    assert ("extend_label", "w1") in pages[0].calls


async def test_duplicate_titles_are_refused_before_anything_is_sent(logged_in_profile, tmp_path):
    # Review I2: attach works by title, so a title must name exactly one media.
    profile = logged_in_profile()
    store(profile)
    twins = ProjectContents({IMG: "apple.jpg", "a2b3c4d5-0000-0000-0000-000000000000": "apple.jpg"},
                            [media(IMG, video=False)], {IMG: "w9"})
    kw, pages, _ = harness(contents=twins)
    with pytest.raises(ValidationError) as info:
        await flow_generate(profile, "video", "an apple", ingredients=[IMG],
                            out=tmp_path / "a.mp4", **kw)
    assert "same title" in info.value.message
    assert not any(c[0] in ("attach", "submit") for c in pages[0].calls)


async def test_a_file_already_in_the_project_is_used_by_id_not_uploaded_again(logged_in_profile,
                                                                             tmp_path):
    profile = logged_in_profile()
    store(profile)
    photo = tmp_path / "apple.jpg"  # already uploaded (title "apple.jpg", id IMG)
    photo.write_bytes(b"\xff\xd8x")
    kw, pages, _ = harness()
    with pytest.raises(ValidationError) as info:
        await flow_generate(profile, "video", "an apple", start_frame=str(photo),
                            i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    assert IMG in info.value.hint
    assert not any(c[0] == "upload" for c in pages[0].calls)


async def test_uploads_are_reported_so_the_paid_run_can_reuse_them(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"\xff\xd8x")
    kw, _, _ = harness()
    result = await flow_generate(profile, "video", "an apple", start_frame=str(photo),
                                 i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    assert result["status"] == "quoted"
    assert result["uploads"] == [{"file": "me.jpg", "media_id": "up1"}]


async def test_extend_reports_the_scene_and_the_cli_model_id(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    kw, _, _ = harness()
    result = await flow_extend(profile, VID, "rolls off", max_credits=10, out=tmp_path / "x.mp4",
                               **kw)
    assert result["scene_id"] is None and result["model"] == "veo-3.1-lite"  # none listed


async def test_a_second_upload_never_takes_the_first_uploads_id(logged_in_profile, tmp_path):
    # Final review I1: a.jpg's as29s record can arrive while b.jpg uploads.
    profile = logged_in_profile()
    store(profile)
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    first.write_bytes(b"\xff\xd8x")
    second.write_bytes(b"\xff\xd8y")
    kw, pages, _ = harness()
    result = await flow_generate(profile, "video", "an apple", start_frame=str(first),
                                 end_frame=str(second), i_have_rights=True,
                                 out=tmp_path / "a.mp4", **kw)
    uploads = [c for c in pages[0].calls if c[0] == "upload"]
    assert "up1" not in uploads[0][2] and "up1" in uploads[1][2]
    assert [u["media_id"] for u in result["uploads"]] == ["up1", "up2"]


COPY = "1306ca3e-0000-4000-8000-000000000000"


async def test_a_characters_photo_copy_does_not_block_its_photo(logged_in_profile, tmp_path):
    # Plan E: a character's image is a hidden copy titled like its photo (live: one tile).
    from aistudio._flow.rows import FlowCharacter

    profile = logged_in_profile()
    store(profile)
    with_copy = ProjectContents({**CONTENTS.titles, COPY: "apple.jpg"},
                                [*CONTENTS.media, listed(media(COPY, video=False))],
                                CONTENTS.workflows, [FlowCharacter("c1", "Apple", COPY, 1)])
    kw, pages, _ = harness(contents=with_copy)
    result = await flow_generate(profile, "video", "an apple", start_frame=IMG,
                                 out=tmp_path / "a.mp4", **kw)
    assert result["status"] == "quoted" and ("attach", "apple.jpg") in pages[0].calls


async def test_attachments_are_checked_by_id_in_the_quote(logged_in_profile, tmp_path):
    # Plan E: a stale chip or a wrong tile would otherwise be approved with the quote.
    from aistudio.exceptions import PlanMismatchError

    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness(turn_media=[VID])
    with pytest.raises(PlanMismatchError) as info:
        await flow_generate(profile, "video", "an apple", start_frame=IMG, max_credits=10,
                            out=tmp_path / "a.mp4", **kw)
    assert ("reject",) in pages[0].calls and "attachments" in info.value.hint


async def test_a_characters_image_id_is_not_an_ingredient(logged_in_profile, tmp_path):
    from aistudio._flow.rows import FlowCharacter

    profile = logged_in_profile()
    store(profile)
    with_copy = ProjectContents({**CONTENTS.titles, COPY: "apple.jpg"},
                                [*CONTENTS.media, listed(media(COPY, video=False))],
                                CONTENTS.workflows, [FlowCharacter("c1", "Apple", COPY, 1)])
    kw, pages, _ = harness(contents=with_copy)
    with pytest.raises(ValidationError) as info:
        await flow_generate(profile, "video", "an apple", ingredients=[COPY],
                            out=tmp_path / "a.mp4", **kw)
    assert "--character" in info.value.hint
    assert not any(c[0] == "submit" for c in pages[0].calls)



def test_avif_heic_and_unknown_image_bytes():
    # Plan C review M7: an ftyp image is not a video; unknown bytes are not called JPEG.
    from aistudio._app.flow import _type_of

    assert _type_of("image", b"\x00\x00\x00\x1cftypavif\x00") == ("image/avif", ".avif")
    assert _type_of("image", b"\x00\x00\x00\x18ftypheic\x00") == ("image/heic", ".heic")
    assert _type_of("image", b"GIF89a....") == ("application/octet-stream", ".bin")
    assert _type_of("video", b"\x00\x00\x00\x18ftypisom") == ("video/mp4", ".mp4")



async def test_attachments_are_checked_in_order(logged_in_profile, tmp_path):
    # Plan E review: swapped frames would make a paid video the wrong way round.
    from aistudio.exceptions import PlanMismatchError

    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness(turn_media=[VID, IMG])
    with pytest.raises(PlanMismatchError):
        await flow_generate(profile, "video", "an apple", start_frame=IMG, end_frame=VID,
                            max_credits=10, out=tmp_path / "a.mp4", **kw)
    assert ("reject",) in pages[0].calls


async def test_an_extend_that_never_started_is_not_reported_as_spent(logged_in_profile,
                                                                   tmp_path):
    # Plan E review: checks before Start ("nothing was spent") must keep their own message.
    from aistudio.exceptions import BrowserError

    profile = logged_in_profile()
    store(profile)
    kw, pages, _ = harness()
    original = kw["page_factory"]

    def factory(ui):
        page = original(ui)

        async def refuse(prompt, *, timeout_s):
            raise BrowserError("Flow's Extend panel did not open; nothing was spent",
                               hint="re-run")
        page.extend = refuse
        return page

    kw["page_factory"] = factory
    with pytest.raises(BrowserError) as info:
        await flow_extend(profile, VID, "rolls off", max_credits=10, out=tmp_path / "e.mp4", **kw)
    assert info.value.hint == "re-run"



def test_avif_with_a_mif1_major_brand():
    # Plan E review M5: an AVIF may name mif1 first and avif among its compatible brands.
    from aistudio._app.flow import _type_of

    avif = b"\x00\x00\x00\x20ftypmif1\x00\x00\x00\x00mif1avifmiaf"
    assert _type_of("image", avif) == ("image/avif", ".avif")


async def test_unknown_image_bytes_without_an_output_name_warn(tmp_path, monkeypatch):
    from aistudio._app.flow import _save_all

    monkeypatch.chdir(tmp_path)

    async def gif(url):
        return b"GIF89a...."

    files, warnings = await _save_all("image", [media(IMG, video=False)], out=None,
                                      overwrite=False, downloader=gif, stamp=NOW,
                                      project_url="u")
    assert files[0]["path"].endswith(".bin") and any("not recognised" in w for w in warnings)


async def test_uploads_happen_before_the_session_and_the_page_is_reloaded(logged_in_profile,
                                                                         tmp_path):
    # Live: after "Upload media" the + panel stays over the grid, so the tile's menu cannot be
    # reached; upload everything first, reload, then start the session and attach.
    profile = logged_in_profile()
    store(profile)
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"\xff\xd8x")
    kw, pages, _ = harness()
    await flow_generate(profile, "video", "an apple", start_frame=str(photo), ingredients=[IMG],
                        i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    names = [c[0] for c in pages[0].calls]
    upload = names.index("upload")
    assert "session" in names[:upload]  # expands a compact prompt box before uploading (live)
    after = names[upload:]
    assert after.index("open") < after.index("session") < after.index("attach")


async def test_each_upload_is_followed_by_a_reload(logged_in_profile, tmp_path):
    # Plan G review Minor 3 (re-graded): a second "+" press could close the open panel.
    profile = logged_in_profile()
    store(profile)
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    first.write_bytes(b"\xff\xd8x")
    second.write_bytes(b"\xff\xd8y")
    kw, pages, _ = harness()
    await flow_generate(profile, "video", "an apple", start_frame=str(first),
                        end_frame=str(second), i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    names = [c[0] for c in pages[0].calls]
    uploads = [i for i, n in enumerate(names) if n == "upload"]
    assert names[uploads[0] + 1] == "open" and names[uploads[1] + 1] == "open"



async def test_uploaded_tiles_are_shown_before_attaching(logged_in_profile, tmp_path):
    profile = logged_in_profile()
    store(profile)
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    first.write_bytes(b"\xff\xd8x")
    second.write_bytes(b"\xff\xd8y")
    kw, pages, _ = harness()
    await flow_generate(profile, "video", "an apple", start_frame=str(first),
                        end_frame=str(second), i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    calls = pages[0].calls
    shown = calls.index(("show_tiles", ("a.jpg", "b.jpg")))
    assert shown < next(i for i, c in enumerate(calls) if c[0] == "attach")



async def test_a_paid_run_never_takes_its_own_uploads(logged_in_profile, tmp_path):
    # Plan H review M2: an upload made seconds before Approve is inside the time slack.
    profile = logged_in_profile()
    store(profile)
    photo = tmp_path / "me.jpg"
    photo.write_bytes(b"\xff\xd8x")
    kw, pages, _ = harness()
    await flow_generate(profile, "video", "an apple", start_frame=str(photo), max_credits=10,
                        i_have_rights=True, out=tmp_path / "a.mp4", **kw)
    approve = next(c for c in pages[0].calls if c[0] == "approve")
    assert "up1" in approve[1]


async def test_upload_errors_map_files_to_ids(logged_in_profile, tmp_path):
    from aistudio.exceptions import BrowserError

    profile = logged_in_profile()
    store(profile)
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    first.write_bytes(b"\xff\xd8x")
    second.write_bytes(b"\xff\xd8y")
    kw, _, _ = harness(tiles_error=BrowserError("Flow does not show b.jpg yet; nothing was sent",
                                                hint="re-run later"))
    with pytest.raises(BrowserError) as info:
        await flow_generate(profile, "video", "an apple", start_frame=str(first),
                            end_frame=str(second), i_have_rights=True, out=tmp_path / "x.mp4",
                            **kw)
    assert "a.jpg=up1" in info.value.hint and "b.jpg=up2" in info.value.hint
