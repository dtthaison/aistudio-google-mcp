import contextlib

import httpx
import pytest
import respx

from aistudio._app.build import create_app, prompt_app
from aistudio._browser.build_page import BuildOutcome
from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import AuthError, NotFoundError, ValidationError

APP = "912435b3-26c1-41b4-ba22-058d464afceb"
RUN = "463f35e4-afb1-41fc-925f-8d550c1d0ffc"
DONE = BuildProgress(RUN, APP, 24, True, ("index.html", "src/App.tsx"), "Snapshot")


def serve_apps(extra=()):
    apps = [[None, [APP, ["1791279373", 0], ["1791279197", 0],
                    ["Tally - Simple Big Button Counter", "", "a counter"]]], *extra]
    return respx.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([apps]))
    )


class FakeBuildPage:
    calls = []

    def __init__(self, outcome):
        self._outcome = outcome

    async def create(self, idea, *, timeout_s):
        FakeBuildPage.calls.append(("create", idea, timeout_s))
        return self._outcome

    async def prompt(self, app_id, text, *, timeout_s):
        FakeBuildPage.calls.append(("prompt", app_id, text, timeout_s))
        return self._outcome


def browser_recording(opened):
    @contextlib.asynccontextmanager
    async def browser(profile, *, window):
        opened.append(window)
        yield "ui"

    return browser


@pytest.fixture
def build_page(monkeypatch):
    FakeBuildPage.calls = []

    def install(outcome):
        monkeypatch.setattr("aistudio._app.build.BuildPage", lambda ui: FakeBuildPage(outcome))

    return install


def clock():
    ticks = iter([100.0, 251.4])
    return lambda: next(ticks)


@respx.mock
async def test_create_reports_the_finished_app(logged_in_profile, make_client, build_page):
    serve_apps()
    build_page(BuildOutcome(APP, RUN, DONE))
    opened = []
    result = await create_app(logged_in_profile(), "a counter", timeout_s=300,
                              client_factory=lambda p: make_client(),
                              browser=browser_recording(opened), now=clock())
    assert opened == ["minimized"]
    assert FakeBuildPage.calls == [("create", "a counter", 300)]
    assert result == {
        "app_id": APP, "run_id": RUN, "url": f"https://aistudio.google.com/apps/{APP}",
        "title": "Tally - Simple Big Button Counter", "status": "done", "steps": 24,
        "files": ["index.html", "src/App.tsx"], "seconds": 151,
    }


@respx.mock
async def test_create_timeout_still_reports_the_app(logged_in_profile, make_client, build_page):
    serve_apps()
    build_page(BuildOutcome(APP, RUN, None))
    result = await create_app(logged_in_profile(), "a counter", visible=True,
                              client_factory=lambda p: make_client(),
                              browser=browser_recording([]), now=clock())
    assert (result["status"], result["steps"], result["files"]) == ("timeout", None, [])
    assert result["url"].endswith(APP)


@respx.mock
async def test_create_reports_which_option_was_kept(logged_in_profile, make_client, build_page):
    serve_apps()
    build_page(BuildOutcome(APP, RUN, DONE, ("opt-a", "opt-b"), "opt-b"))
    result = await create_app(logged_in_profile(), "a counter",
                              client_factory=lambda p: make_client(),
                              browser=browser_recording([]), now=clock())
    assert result["status"] == "done"
    assert (result["options"], result["kept_option"]) == (["opt-a", "opt-b"], "opt-b")


@respx.mock
async def test_a_single_build_reports_no_options(logged_in_profile, make_client, build_page):
    serve_apps()
    build_page(BuildOutcome(APP, RUN, DONE))
    result = await create_app(logged_in_profile(), "a counter",
                              client_factory=lambda p: make_client(),
                              browser=browser_recording([]), now=clock())
    assert "options" not in result and "kept_option" not in result


@pytest.mark.parametrize("idea", ["", "   "])
async def test_blank_idea_is_rejected_before_anything(logged_in_profile, idea):
    opened = []
    with pytest.raises(ValidationError):
        await create_app(logged_in_profile(), idea, browser=browser_recording(opened))
    assert opened == []


@respx.mock
async def test_expired_login_fails_before_the_browser(logged_in_profile, make_client):
    respx.post(method_url("ListApplets")).mock(return_value=httpx.Response(401))
    opened = []
    with pytest.raises(AuthError):
        await create_app(logged_in_profile(), "a counter",
                         client_factory=lambda p: make_client(), browser=browser_recording(opened))
    assert opened == []


@respx.mock
async def test_prompt_resolves_a_prefix(logged_in_profile, make_client, build_page):
    serve_apps()
    build_page(BuildOutcome(APP, "66ca59ad-237a-4446-b206-eca5cabac1c7",
                            BuildProgress("66ca59ad-237a-4446-b206-eca5cabac1c7", APP, 3, True)))
    result = await prompt_app(logged_in_profile(), "9124", "make it blue",
                              client_factory=lambda p: make_client(),
                              browser=browser_recording([]), now=clock())
    assert FakeBuildPage.calls == [("prompt", APP, "make it blue", 600)]
    assert result["status"] == "done"


@respx.mock
async def test_prompt_rejects_drive_apps_unknown_ids_and_blank_text(logged_in_profile, make_client):
    drive = [[[[["Old app", "", "p", None, "default"]], None, None, "1" + "d" * 32]]]
    serve_apps(extra=drive)
    profile = logged_in_profile()
    opened = []
    kwargs = {"client_factory": lambda p: make_client(), "browser": browser_recording(opened)}
    with pytest.raises(ValidationError) as info:
        await prompt_app(profile, "1ddd", "x", **kwargs)
    assert "Google Drive" in info.value.message
    with pytest.raises(NotFoundError):
        await prompt_app(profile, "zzz", "x", **kwargs)
    with pytest.raises(ValidationError):
        await prompt_app(profile, APP, "  ", **kwargs)
    assert opened == []


@respx.mock
async def test_title_lookup_failure_still_returns_the_finished_build(logged_in_profile, make_client,
                                                                    build_page):
    ok = serve_apps()
    ok.side_effect = [ok.return_value, httpx.Response(404)]
    build_page(BuildOutcome(APP, RUN, DONE))
    result = await create_app(logged_in_profile(), "a counter",
                              client_factory=lambda p: make_client(),
                              browser=browser_recording([]), now=clock())
    assert (result["app_id"], result["status"], result["title"]) == (APP, "done", None)


@respx.mock
async def test_prompt_rejects_blank_and_ambiguous_refs(logged_in_profile, make_client):
    twin = [[None, [APP[:8] + "-twin", ["1", 0], ["1", 0], ["Twin", "", "p"]]]]
    serve_apps(extra=twin)
    opened = []
    kwargs = {"client_factory": lambda p: make_client(), "browser": browser_recording(opened)}
    with pytest.raises(ValidationError):
        await prompt_app(logged_in_profile(), "   ", "x", **kwargs)
    with pytest.raises(ValidationError) as info:
        await prompt_app(logged_in_profile(), APP[:8], "x", **kwargs)
    assert "Twin" not in info.value.message and APP in info.value.message
    assert opened == []


@respx.mock
async def test_blank_ref_does_not_pick_the_only_app(logged_in_profile, make_client):
    serve_apps()
    opened = []
    with pytest.raises(ValidationError):
        await prompt_app(logged_in_profile(), "  ", "make it blue",
                         client_factory=lambda p: make_client(), browser=browser_recording(opened))
    assert opened == []


@respx.mock
async def test_build_notice_comes_after_the_checks(logged_in_profile, make_client, build_page):
    # Deferred minor #20: "Building… 5-7 minutes" printed, then NOT_FOUND at once.
    serve_apps()
    build_page(BuildOutcome(APP, RUN, DONE))
    order = []
    opened = browser_recording(order)
    with pytest.raises(NotFoundError):
        await prompt_app(logged_in_profile(), "nosuch", "x", browser=opened,
                         client_factory=lambda p: make_client(),
                         on_start=lambda: order.append("notice"))
    with pytest.raises(ValidationError):
        await create_app(logged_in_profile(), " ", browser=opened,
                         on_start=lambda: order.append("notice"))
    assert order == []
    await create_app(logged_in_profile(), "a counter", client_factory=lambda p: make_client(),
                     browser=opened, now=clock(), on_start=lambda: order.append("notice"))
    assert order == ["notice", "minimized"]
