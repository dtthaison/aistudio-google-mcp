"""``flow_image_batch``: many prompts, one browser session, one at a time."""

import contextlib

import pytest

from aistudio._app.flow import flow_image_batch, prompt_slug
from aistudio._flow.page import AgentTurn, Defaults
from aistudio._flow.rows import Generated, Quote
from aistudio.exceptions import OverBudgetError, ValidationError
from tests.unit.test_app_flow import NOW, FakePage

FREE = Defaults("Nano Banana 2.1", "16:9", 1, asks=True)
QUOTE = AgentTurn("kick off this 1 image generation, costing 2 credits?", Quote(1, "image", 2))


@pytest.fixture
def shots(tmp_path):
    """The output folder (tmp_path also holds the fake home)."""
    folder = tmp_path / "shots"
    folder.mkdir()
    return folder


def made(media_id):
    return AgentTurn("done", None, generated=(Generated(media_id, "success", "image",
                                                        "Nano Banana 2.1", "16:9"),))


def batch_kw(turns):
    """One scripted turn per prompt run, in order; each browser opening is counted."""
    pages, opened, queue = [], [], list(turns)

    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        opened.append(window)
        yield object()

    def factory(ui):
        turn = queue.pop(0) if len(queue) > 1 else queue[0]
        pages.append(FakePage(ui, turn=turn, defaults=FREE, credit_values=(100, 100)))
        return pages[-1]

    async def downloader(url):
        return b"\xff\xd8\xff jpeg"

    return {"browser": browser, "page_factory": factory, "downloader": downloader,
            "now": lambda: NOW}, pages, opened


def approved(pages):
    return [p for p in pages if ("approve", 1) in p.calls]


async def test_a_free_batch_runs_in_one_session_and_names_files_by_prompt(logged_in_profile,
                                                                         shots):
    kw, pages, opened = batch_kw([made("g1"), made("g2")])
    result = await flow_image_batch(logged_in_profile(), ["A red apple!", "A  blue\nlemon"],
                                    out=shots, **kw)
    assert opened == ["minimized"] and len(pages) == 2  # one Chrome, two prompt runs
    assert result["status"] == "done" and result["total"] == 2 and result["credits_spent"] == 0
    assert [r["status"] for r in result["results"]] == ["done", "done"]
    assert [r["index"] for r in result["results"]] == [1, 2]
    assert result["results"][0]["media_ids"] == ["g1"]
    assert result["results"][1]["credits_spent"] == 0
    assert sorted(p.name for p in shots.iterdir()) == ["01-a-red-apple-1.jpg",
                                                          "02-a-blue-lemon-1.jpg"]
    assert result["results"][0]["files"][0]["path"] == str(shots / "01-a-red-apple-1.jpg")


async def test_shared_options_apply_to_every_prompt(logged_in_profile, shots):
    kw, pages, _ = batch_kw([made("g1"), made("g2")])
    await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots, aspect="9:16",
                           count=2, **kw)
    assert len(pages) == 2
    for page in pages:
        assert ("apply", Defaults("Nano Banana 2.1", "9:16", 2)) in page.calls
        submit = next(c[1] for c in page.calls if c[0] == "submit")
        assert "Generate exactly 2 image" in submit


async def test_every_option_reaches_every_single_prompt_run(logged_in_profile, shots):
    calls = []

    async def generate(profile, kind, prompt, **kwargs):
        calls.append((kind, prompt, kwargs))
        return {"status": "done", "files": [], "credits_spent": 0, "project": "p"}

    media = "11111111-1111-1111-1111-111111111111"
    browser = batch_kw([made("g1")])[0]["browser"]
    await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots, generate=generate,
                           browser=browser,
                           model="nano-banana-2.1", aspect="9:16", count=2, ingredients=[media],
                           characters=["Robo"], project="p1", timeout_s=99.0, overwrite=True)
    assert [(k, p) for k, p, _ in calls] == [("image", "one"), ("image", "two")]
    for _, _, kw in calls:
        assert (kw["model"], kw["aspect"], kw["count"], kw["max_credits"]) == (
            "nano-banana-2.1", "9:16", 2, None)
        assert (kw["ingredients"], kw["characters"], kw["project"], kw["timeout_s"]) == (
            [media], ["Robo"], "p1", 99.0)
        assert kw["out"] == shots and kw["overwrite"] is True
    assert [kw["stem"] for _, _, kw in calls] == ["01-one", "02-two"]


async def test_a_paid_batch_without_a_budget_only_quotes(logged_in_profile, shots):
    kw, pages, opened = batch_kw([QUOTE])
    result = await flow_image_batch(logged_in_profile(), ["one", "two", "three"], out=shots,
                                    **kw)
    assert result["status"] == "quoted" and result["quote_credits"] == 6
    assert [r["quote_credits"] for r in result["results"]] == [2, 2, 2]
    assert result["credits_spent"] == 0 and opened == ["minimized"]
    assert approved(pages) == [] and list(shots.iterdir()) == []


async def test_a_batch_over_budget_is_refused_before_anything_is_approved(logged_in_profile,
                                                                         shots):
    kw, pages, _ = batch_kw([QUOTE])
    with pytest.raises(OverBudgetError) as info:
        await flow_image_batch(logged_in_profile(), ["one", "two", "three"], out=shots,
                               max_credits=5, **kw)
    assert "6 credits" in info.value.message and "--max-credits 6" in info.value.hint
    assert approved(pages) == [] and list(shots.iterdir()) == []


async def test_a_paid_batch_within_budget_spends_each_quote(logged_in_profile, shots):
    kw, pages, opened = batch_kw([QUOTE])
    result = await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots,
                                    max_credits=4, **kw)
    assert result["status"] == "done" and result["credits_spent"] == 4
    assert [r["credits_spent"] for r in result["results"]] == [2, 2]
    assert len(approved(pages)) == 2 and opened == ["minimized"]
    assert sorted(p.name for p in shots.iterdir()) == ["01-one-1.jpg", "02-two-1.jpg"]


async def test_the_first_error_stops_the_batch_and_says_where_to_resume(logged_in_profile,
                                                                       shots):
    kw, pages, _ = batch_kw([made("g1"), AgentTurn("Which apple?", None), made("g3")])
    result = await flow_image_batch(logged_in_profile(), ["one", "two", "three"], out=shots,
                                    **kw)
    assert len(pages) == 2  # the third prompt never ran, nothing was retried
    assert result["status"] == "stopped" and result["resume_at"] == 2
    assert [r["status"] for r in result["results"]] == ["done", "failed", "not_run"]
    assert result["results"][0]["media_ids"] == ["g1"]
    assert "Which apple" in result["error"]["message"] and result["error"]["code"]
    assert result["results"][1]["error"] == result["error"]
    assert [p.name for p in shots.iterdir()] == ["01-one-1.jpg"]


async def test_an_error_while_quoting_spends_nothing_and_resumes_from_the_start(
        logged_in_profile, shots):
    kw, pages, _ = batch_kw([QUOTE, AgentTurn("Which apple?", None)])
    result = await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots,
                                    max_credits=10, **kw)
    assert result["status"] == "stopped" and result["resume_at"] == 1
    assert [r["status"] for r in result["results"]] == ["quoted", "failed"]
    assert approved(pages) == [] and result["credits_spent"] == 0


async def test_start_at_resumes_with_the_same_numbering(logged_in_profile, shots):
    kw, pages, _ = batch_kw([made("g2"), made("g3")])
    result = await flow_image_batch(logged_in_profile(), ["one", "two", "three"], out=shots,
                                    start_at=2, **kw)
    assert len(pages) == 2 and result["start_at"] == 2 and result["total"] == 3
    assert [r["index"] for r in result["results"]] == [2, 3]
    assert sorted(p.name for p in shots.iterdir()) == ["02-two-1.jpg", "03-three-1.jpg"]


async def test_existing_files_are_never_overwritten_without_force(logged_in_profile, shots):
    (shots / "02-two-1.png").write_bytes(b"mine")
    kw, pages, opened = batch_kw([made("g1")])
    with pytest.raises(ValidationError) as info:
        await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots, **kw)
    assert "02-two-1.png" in info.value.message and "--force" in info.value.hint
    assert opened == []  # refused before Chrome opened and before any prompt ran
    (shots / "01-one-1.jpg").write_bytes(b"old")
    result = await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots,
                                    overwrite=True, **kw)
    assert result["status"] == "done"
    assert (shots / "01-one-1.jpg").read_bytes() == b"\xff\xd8\xff jpeg"


async def test_resuming_ignores_files_of_earlier_prompts(logged_in_profile, shots):
    (shots / "01-one-1.jpg").write_bytes(b"mine")
    kw, _, _ = batch_kw([made("g2")])
    result = await flow_image_batch(logged_in_profile(), ["one", "two"], out=shots,
                                    start_at=2, **kw)
    assert result["status"] == "done"
    assert (shots / "01-one-1.jpg").read_bytes() == b"mine"


async def test_bad_inputs_fail_before_chrome(logged_in_profile, shots):
    kw, _, opened = batch_kw([made("g1")])
    profile = logged_in_profile()
    cases = [dict(prompts=[], out=shots), dict(prompts=["a", "  "], out=shots),
             dict(prompts=["a"], out=shots, start_at=2),
             dict(prompts=["a"], out=shots, start_at=0),
             dict(prompts=["a"], out=shots / "missing"),
             dict(prompts=["a"], out=shots, max_credits=-1),
             dict(prompts=["a"], out=shots, count=9)]
    for case in cases:
        prompts = case.pop("prompts")
        with pytest.raises(ValidationError):
            await flow_image_batch(profile, prompts, **case, **kw)
    assert opened == []


async def test_a_local_ingredient_file_is_refused(logged_in_profile, shots):
    # An upload cannot repeat for each prompt (the same file name cannot be uploaded twice)
    kw, _, opened = batch_kw([made("g1")])
    with pytest.raises(ValidationError) as info:
        await flow_image_batch(logged_in_profile(), ["a"], out=shots,
                               ingredients=["photo.jpg"], **kw)
    assert "media id" in info.value.message and opened == []


def test_the_slug_is_short_ascii_and_never_empty():
    assert prompt_slug("A red apple, on a table!") == "a-red-apple-on-a-table"
    assert prompt_slug("") == "prompt" and prompt_slug("!!! ???") == "prompt"
    assert prompt_slug("Một con mèo đẹp") == "mot-con-meo-dep"
    assert len(prompt_slug("word " * 40)) <= 40 and not prompt_slug("word " * 40).endswith("-")
