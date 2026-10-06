# aistudio-py Build: `app create` and `app prompt` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two commands:

- `aistudio app create "idea"` builds a new AI Studio Build app.
- `aistudio app prompt <app> "change"` updates an existing one.

Both wait until Google's building agent finishes and report the app id, link, title and the files
it produced.

**Architecture:**

- **Page driving.** Starting a build (`CodeAssistantOffline`) carries a BotGuard token that is bound
  to its content. So the CLI drives AI Studio's own page in the user's Chrome, **minimized**
  (proven: create and update both returned 200 from a minimized window, findings "Build
  create/update discovery").
- **`Ui` interface.** A narrow `Ui` protocol (`_browser/ui.py`) hides Playwright: `goto`, fill or
  press the first visible element, and a per-method response log.
- **`BuildPage` driver** (`_browser/build_page.py`):
  1. Types into the prompt box and presses ⌘↵.
  2. Reads `CreateApplet` for the app id (create only).
  3. Reads `CodeAssistantOffline` for the run id.
  4. Waits for the `StreamCodeAssistantOfflineGeneration` response for that run. Its final
     snapshot holds the model message with the produced file list, so a completed stream means
     the build is done.
- **Decoders** (`rows/build.py`) are golden-tested against the M1 follow-up fixtures.

**Tech Stack:** unchanged (Playwright via the `[browser]` extra).

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` §6.7 (`app create`), §5.4 (page driving, revised).
**Evidence:** `docs/wire/findings.md` ("Build create/update discovery"); fixtures `CreateApplet/2`,
`CodeAssistantOffline/2-3`, `StreamCodeAssistantOfflineGeneration/1`; `scripts/spikes/build_drive.py`.

**Scope:**

- **Implements:**
  - `app create IDEA` and `app prompt APP TEXT`, both with `[--timeout S] [--visible] [--json]`;
  - the browser `Ui` layer (minimized window by default);
  - `CliState.require_browser`;
  - the `launch_persistent` viewport and the "profile in use" hint;
  - the `app` group help no longer says "read-only".
- **Deferred:**
  - `app export`: the final snapshot carries file **hashes**, not contents, and the
    content-fetch call has not been identified yet;
  - choosing the model (the page default `gemini-3.8-flash` is used);
  - a `--no-wait` mode: whether a run finishes and saves if the window closes early is unproven;
  - `app delete`;
  - `gallery remix`.

## Global Constraints

- All earlier Global Constraints hold:
  - the `--json` envelope and exit codes 0/1/2/130;
  - ruff line length 100;
  - no secrets in output;
  - decoders raise `SchemaDriftError` only for required data.
- **Browser window.** Generation opens the profile's Chrome **minimized** by default. `--visible`
  shows it. The viewport is 1600×1000.
- **Login check first.** Before any browser opens, one `ListApplets` call over plain HTTP checks the
  login, so an expired session fails fast with `AUTH_ERROR`.
- **Prompt box.** It is the first **visible** `textarea`, on both `/apps` and `/apps/<id>`. The run
  shortcut is ⌘↵ on macOS and Ctrl+↵ elsewhere.
- **Timeouts.** `CreateApplet` and `CodeAssistantOffline` must arrive within **60 s** of pressing
  Run; otherwise the result is `BROWSER_ERROR` ("page changed or did not load"). The whole build
  waits up to `--timeout`, default **600 s**.
- **Done.** A `StreamCodeAssistantOfflineGeneration` response whose snapshots carry this run's id
  and a final `["model", summary, _, ts, [[path, sha256], ...]]` message.
- **Timeout result.** The command still prints the result (`status: "timeout"`, with the app link)
  and exits **2**.
- **Refusals.** A 403 on starting a build gives `BOTGUARD_REJECTED` with a hint to retry with
  `--visible`. Other HTTP errors map through `error_for_response`.
- **Drive-stored apps** (`storage: "drive"`) cannot be prompted. The result is `VALIDATION_ERROR`
  telling the user to open the app in AI Studio.

## Review Focus

1. **Expired login.** `AUTH_ERROR` before any browser window opens. Test in Task 4.
2. **The build outlives `--timeout`.** The result is printed with `status: "timeout"` and the app
   link, and the exit code is 2; never a traceback or a silent success. Tests in Tasks 3 and 5.
3. **The page streams an older run first** (an app's previous runs). It is ignored; only this
   run's completed stream counts. Test in Task 3.
4. **An empty or blank idea or change text.** `VALIDATION_ERROR` with no browser and no request.
   Test in Task 4.
5. **An ambiguous or unknown app prefix, or a Drive-stored app, for `app prompt`.** The result is
   `VALIDATION_ERROR` or `NOT_FOUND` before any browser opens. Test in Task 4.

---

### Task 1: Build decoders

**Files:**
- Create: `src/aistudio/_rpc/rows/build.py`; add `BuildProgress` to `src/aistudio/_types/apps.py`
- Test: `tests/unit/test_rows_build.py`

**Interfaces:**
- Consumes: `wire.opt_index`, `wire.safe_index`, `rows.as_str`/`as_int`, `SchemaDriftError`; the `captured` fixture
- Produces:
  - `_types.apps.BuildProgress(run_id, app_id, steps, done, files=(), summary=None)`.
  - `rows.build.decode_created_app(response) -> str`.
  - `rows.build.decode_started_run(response) -> tuple[str, str]`, returning `(run_id, app_id)`.
  - `rows.build.decode_build_stream(response) -> BuildProgress`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rows_build.py`:

```python
import pytest

from aistudio._rpc.rows.build import decode_build_stream, decode_created_app, decode_started_run
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import SchemaDriftError

APP = "912435b3-26c1-41b4-ba22-058d464afceb"
RUN = "463f35e4-afb1-41fc-925f-8d550c1d0ffc"
FILES = ("index.html", "src/main.tsx", "package.json", "src/App.tsx", ".gitignore",
         "src/index.css", "vite.config.ts", "metadata.json", ".env.example", "tsconfig.json")


def test_created_app_id(captured):
    assert decode_created_app(captured("CreateApplet", 2).response) == APP
    with pytest.raises(SchemaDriftError):
        decode_created_app([])


def test_started_runs(captured):
    assert decode_started_run(captured("CodeAssistantOffline", 2).response) == (RUN, APP)
    assert decode_started_run(captured("CodeAssistantOffline", 3).response) == (
        "66ca59ad-237a-4446-b206-eca5cabac1c7", APP)
    with pytest.raises(SchemaDriftError):
        decode_started_run(["only-run"])


def test_completed_stream(captured):
    assert decode_build_stream(captured("StreamCodeAssistantOfflineGeneration").response) == (
        BuildProgress(run_id=RUN, app_id=APP, steps=24, done=True, files=FILES,
                      summary="Snapshot from A simple counter web page with one big button "
                              "that increments a number.")
    )


def test_unfinished_stream_is_not_done(captured):
    response = captured("StreamCodeAssistantOfflineGeneration").response
    partial = [response[0][:-1]]
    progress = decode_build_stream(partial)
    assert (progress.run_id, progress.done, progress.steps, progress.files) == (RUN, False, 23, ())


@pytest.mark.parametrize("response", [None, [], [None], ["junk"], [[["junk"]]]])
def test_empty_or_odd_streams_decode_as_not_done(response):
    assert decode_build_stream(response) == BuildProgress(None, None, 0, False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rows_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.rows.build'`

- [ ] **Step 3: Write the implementation**

Append to `src/aistudio/_types/apps.py`:

```python


@dataclass(frozen=True)
class BuildProgress:
    run_id: str | None
    app_id: str | None
    steps: int  # highest step counter seen in the stream
    done: bool  # the final model message (with the produced file list) arrived
    files: tuple[str, ...] = ()
    summary: str | None = None
```

Create `src/aistudio/_rpc/rows/build.py`:

```python
"""Decode Build create/update responses (fixtures CreateApplet/2, CodeAssistantOffline/2-3,
StreamCodeAssistantOfflineGeneration/1; docs/wire/findings.md "Build create/update discovery").

CreateApplet -> ``[app_id, [...]]``. CodeAssistantOffline -> ``[run_id, app_id]``.
StreamCodeAssistantOfflineGeneration -> ``[[snapshot, ...]]``; snapshot ``[_, _, [run], app_id]``
where run ``[11]`` is the step counter, ``[16]`` the run id, and the final snapshot's ``[8]`` is
``["model", summary, _, timestamp, [[path, sha256], ...]]``.
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_int, as_str
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import SchemaDriftError


def decode_created_app(response: Any) -> str:
    app_id = safe_index(response, 0, method="CreateApplet", expected="the new app id")
    if not isinstance(app_id, str):
        raise SchemaDriftError("CreateApplet", (0,), "the new app id")
    return app_id


def decode_started_run(response: Any) -> tuple[str, str]:
    run_id = safe_index(response, 0, method="CodeAssistantOffline", expected="a run id")
    app_id = safe_index(response, 1, method="CodeAssistantOffline", expected="an app id")
    if not isinstance(run_id, str) or not isinstance(app_id, str):
        raise SchemaDriftError("CodeAssistantOffline", (0,), "run and app ids")
    return run_id, app_id


def decode_build_stream(response: Any) -> BuildProgress:
    items = opt_index(response, 0)
    run_id = app_id = summary = None
    steps, done, files = 0, False, ()
    for item in items if isinstance(items, list) else []:
        run = opt_index(item, 2, 0)
        run_id = as_str(opt_index(run, 16)) or run_id
        app_id = as_str(opt_index(item, 3)) or app_id
        steps = max(steps, as_int(opt_index(run, 11)) or 0)
        final = opt_index(run, 8)
        if as_str(opt_index(final, 0)) == "model":
            done = True
            summary = as_str(opt_index(final, 1))
            entries = opt_index(final, 4)
            entries = entries if isinstance(entries, list) else []
            files = tuple(p for p in (as_str(opt_index(e, 0)) for e in entries) if p)
    return BuildProgress(run_id, app_id, steps, done, files, summary)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_rpc/rows/build.py src/aistudio/_types/apps.py tests/unit/test_rows_build.py
git commit -m "feat: decode Build create, run start and generation stream"
```

---

### Task 2: Browser `Ui` layer

**Files:**
- Create: `src/aistudio/_browser/__init__.py` (empty), `src/aistudio/_browser/ui.py`
- Modify:
  - `src/aistudio/_auth/login.py` (`launch_persistent` gains `viewport`; profile-in-use hint);
  - `src/aistudio/cli/state.py` (`require_browser`);
  - `tests/conftest.py` (add the `fake_ui` fixture).
- Test: `tests/unit/test_browser_ui.py`, plus one new test in `tests/unit/test_login.py`

**Interfaces:**
- Consumes: `launch_persistent`, `default_playwright_factory`, `BrowserError`, `ValidationError`
- Produces:
  - `launch_persistent(..., viewport: dict[str, int] | None = None)`.
  - `_browser.ui`:
    - the constants `VIEWPORT`, `RUN_SHORTCUT` and `RPC_MARKER`;
    - `CapturedResponse(method, status, text, request_body)`;
    - the Protocols `ResponseLog` (`async next(method, *, timeout_s) -> CapturedResponse | None`) and `Ui` (`goto`, `fill_first`, `press_first`, `responses()`);
    - `PlaywrightUi(page)`;
    - `open_browser(profile, *, window="minimized", playwright_factory=None)`, an async context manager yielding a `Ui`.
  - `CliState.require_browser()`.
  - The pytest fixture `fake_ui`, which returns the class `FakeUi(responses: dict[str, list[CapturedResponse]] | None = None)` with `.ops`.

- [ ] **Step 1: Add `FakeUi` to `tests/conftest.py`**

Add `import contextlib` to the imports at the top of `tests/conftest.py`, then append:

```python
class FakeUi:
    """Scripted stand-in for aistudio._browser.ui.Ui: records ops, replays responses per method.

    ``next`` returns the next scripted response for a method, or None (a timeout) when none
    are left.
    """

    def __init__(self, responses=None):
        self.ops = []
        self._responses = {k: list(v) for k, v in (responses or {}).items()}

    async def goto(self, url):
        self.ops.append(("goto", url))

    async def fill_first(self, selector, text, *, timeout_s=30.0):
        self.ops.append(("fill", selector, text))

    async def press_first(self, selector, key):
        self.ops.append(("press", selector, key))

    @contextlib.asynccontextmanager
    async def responses(self):
        yield self

    async def next(self, method, *, timeout_s):
        queue = self._responses.get(method) or []
        return queue.pop(0) if queue else None


@pytest.fixture
def fake_ui():
    return FakeUi
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_login.py`:

```python


async def test_profile_in_use_gets_a_close_the_other_window_hint(aistudio_home):
    chromium = FakeChromium(error=Exception(
        "Failed to create a ProcessSingleton for your profile directory"
    ))
    with pytest.raises(BrowserError) as info:
        await run_login(get_profile("default"), playwright_factory=factory(chromium), poll=0)
    assert "another aistudio browser window" in info.value.hint
```

Create `tests/unit/test_browser_ui.py`:

```python
import pytest

from aistudio._browser.ui import VIEWPORT, PlaywrightUi, open_browser
from aistudio.exceptions import BrowserError
from aistudio.paths import get_profile


class Element:
    def __init__(self, visible):
        self._visible = visible
        self.filled = None

    async def is_visible(self):
        return self._visible

    async def fill(self, text):
        self.filled = text


class Locator:
    def __init__(self, elements):
        self._elements = elements

    async def all(self):
        return self._elements


class Response:
    def __init__(self, url, status=200, text="[]"):
        self.url = url
        self.status = status
        self._text = text
        self.request = type("Req", (), {"post_data": "[1]"})()

    async def text(self):
        return self._text


class Page:
    def __init__(self, elements=()):
        self._elements = list(elements)
        self.handlers = []

    def locator(self, selector):
        return Locator(self._elements)

    def on(self, event, handler):
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)


async def test_fill_first_uses_the_first_visible_element():
    hidden, shown = Element(False), Element(True)
    await PlaywrightUi(Page([hidden, shown])).fill_first("textarea", "hi", timeout_s=1)
    assert (hidden.filled, shown.filled) == (None, "hi")


async def test_missing_element_is_a_clear_browser_error():
    with pytest.raises(BrowserError) as info:
        await PlaywrightUi(Page([Element(False)])).fill_first("textarea", "hi", timeout_s=0.1)
    assert "AI Studio page changed or did not load" in info.value.message


async def test_responses_are_logged_per_method_until_the_block_ends():
    page = Page()
    ui = PlaywrightUi(page)
    async with ui.responses() as log:
        base = "https://x.clients6.google.com/$rpc/s.MakerSuiteService/"
        for handler in page.handlers:
            handler(Response(base + "CreateApplet", text='["app"]'))
            handler(Response("https://www.gstatic.com/x.js"))
        got = await log.next("CreateApplet", timeout_s=1)
        assert (got.method, got.status, got.text, got.request_body) == (
            "CreateApplet", 200, '["app"]', "[1]")
        assert await log.next("CodeAssistantOffline", timeout_s=0.05) is None
    assert page.handlers == []


class Cdp:
    def __init__(self):
        self.sent = []

    async def send(self, method, params=None):
        self.sent.append((method, params))
        return {"windowId": 7}


class Context:
    def __init__(self):
        self.pages = ["page"]
        self.closed = False
        self.cdp = Cdp()

    async def new_cdp_session(self, page):
        return self.cdp

    async def close(self):
        self.closed = True


class Chromium:
    def __init__(self, context):
        self.context = context
        self.kwargs = None

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.kwargs = kwargs
        return self.context


class Playwright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_open_browser_minimizes_a_desktop_sized_window_and_closes(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = Context()
    chromium = Chromium(context)
    async with open_browser(profile, playwright_factory=lambda: Playwright(chromium)) as ui:
        assert isinstance(ui, PlaywrightUi)
    assert chromium.kwargs["viewport"] == VIEWPORT
    assert chromium.kwargs["headless"] is False
    assert context.cdp.sent[-1] == (
        "Browser.setWindowBounds", {"windowId": 7, "bounds": {"windowState": "minimized"}})
    assert context.closed


async def test_visible_window_is_not_minimized(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = Context()
    async with open_browser(profile, window="visible",
                            playwright_factory=lambda: Playwright(Chromium(context))):
        pass
    assert context.cdp.sent == []


async def test_open_browser_requires_a_browser_session(aistudio_home):
    with pytest.raises(BrowserError) as info:
        async with open_browser(get_profile("default"), playwright_factory=lambda: None):
            pass
    assert info.value.hint == 'run "aistudio login" first'
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_browser_ui.py tests/unit/test_login.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._browser'`, and the new login test fails on its hint assertion.

- [ ] **Step 4: Write the implementation**

In `src/aistudio/_auth/login.py`, replace the whole `launch_persistent` function with:

```python
_PROFILE_IN_USE = re.compile(r"ProcessSingleton|SingletonLock|already in use", re.I)


async def launch_persistent(playwright: Any, profile: Profile, *, browser: str, headless: bool,
                            viewport: dict[str, int] | None = None) -> Any:
    """Open the profile's persistent Chrome; translate launch failures into BrowserError."""
    options: dict[str, Any] = {
        "channel": None if browser == "chromium" else browser,
        "headless": headless,
        "args": CHROME_ARGS,
        "ignore_default_args": IGNORE_DEFAULT_ARGS,
    }
    if viewport:
        options["viewport"] = viewport
    else:
        options["no_viewport"] = True
    try:
        return await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), **options
        )
    except Exception as exc:  # playwright.async_api.Error, missing executables
        if _PROFILE_IN_USE.search(str(exc)):
            hint = "another aistudio browser window is using this profile; close it and retry"
        elif browser == "chrome":
            hint = (
                'install Google Chrome, or use "--browser chromium" '
                'after "playwright install chromium"'
            )
        else:
            hint = f'run "playwright install {browser}", or use "--browser chrome"'
        raise BrowserError(f"could not launch {browser}: {exc}", hint=hint) from exc
```

Create `src/aistudio/_browser/__init__.py` as an empty file.

Create `src/aistudio/_browser/ui.py`:

```python
"""A narrow UI-automation interface over a Playwright page (spec §5.4 page driving).

Page drivers (BuildPage, ...) talk to ``Ui``; ``PlaywrightUi`` is the thin real implementation
and tests use a scripted fake. Generation requests carry a content-bound BotGuard token, so they
must be sent by AI Studio's own page; a minimized window is accepted, headless is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio.exceptions import BrowserError
from aistudio.paths import Profile

VIEWPORT = {"width": 1600, "height": 1000}  # narrower hides parts of AI Studio's UI
RUN_SHORTCUT = "Meta+Enter" if sys.platform == "darwin" else "Control+Enter"
RPC_MARKER = "MakerSuiteService/"


@dataclass(frozen=True)
class CapturedResponse:
    method: str
    status: int
    text: str
    request_body: str


class ResponseLog(Protocol):
    async def next(self, method: str, *, timeout_s: float) -> CapturedResponse | None: ...


class Ui(Protocol):
    async def goto(self, url: str) -> None: ...

    async def fill_first(self, selector: str, text: str, *, timeout_s: float = 30.0) -> None: ...

    async def press_first(self, selector: str, key: str) -> None: ...

    def responses(self) -> contextlib.AbstractAsyncContextManager[ResponseLog]: ...


class _QueueLog:
    def __init__(self) -> None:
        self._queues: defaultdict[str, asyncio.Queue[CapturedResponse]] = defaultdict(
            asyncio.Queue
        )

    def put(self, response: CapturedResponse) -> None:
        self._queues[response.method].put_nowait(response)

    async def next(self, method: str, *, timeout_s: float) -> CapturedResponse | None:
        try:
            return await asyncio.wait_for(self._queues[method].get(), max(timeout_s, 0))
        except asyncio.TimeoutError:
            return None


class PlaywrightUi:
    def __init__(self, page: Any) -> None:
        self._page = page

    async def goto(self, url: str) -> None:
        await self._page.goto(url)

    async def _first_visible(self, selector: str, timeout_s: float) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            for element in await self._page.locator(selector).all():
                if await element.is_visible():
                    return element
            if loop.time() >= deadline:
                raise BrowserError(
                    f"AI Studio page changed or did not load: no visible {selector!r}",
                    hint="re-run; if it persists, the AI Studio UI changed and aistudio "
                         "needs an update",
                )
            await asyncio.sleep(0.25)

    async def fill_first(self, selector: str, text: str, *, timeout_s: float = 30.0) -> None:
        await (await self._first_visible(selector, timeout_s)).fill(text)

    async def press_first(self, selector: str, key: str) -> None:
        await (await self._first_visible(selector, 5.0)).press(key)

    @contextlib.asynccontextmanager
    async def responses(self) -> AsyncIterator[ResponseLog]:
        log = _QueueLog()
        pending: set[asyncio.Task[None]] = set()

        async def store(response: Any) -> None:
            try:
                text = await response.text()  # a streamed response resolves when it ends
            except Exception:
                text = ""
            method = response.url.split("?", 1)[0].rsplit("/", 1)[-1]
            log.put(CapturedResponse(method, response.status, text,
                                     response.request.post_data or ""))

        def on_response(response: Any) -> None:
            if RPC_MARKER in response.url:
                task = asyncio.ensure_future(store(response))
                pending.add(task)
                task.add_done_callback(pending.discard)

        self._page.on("response", on_response)
        try:
            yield log
        finally:
            self._page.remove_listener("response", on_response)
            for task in pending:
                task.cancel()


async def _minimize(context: Any, page: Any) -> None:
    try:
        cdp = await context.new_cdp_session(page)
        window = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds",
                       {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
    except Exception:
        pass  # cosmetic: a visible window works the same


@contextlib.asynccontextmanager
async def open_browser(
    profile: Profile,
    *,
    window: str = "minimized",
    playwright_factory: Callable[[], Any] | None = None,
) -> AsyncIterator[Ui]:
    """The profile's Chrome at a desktop viewport; ``window`` is minimized, visible or headless."""
    if not profile.chrome_dir.exists():
        raise BrowserError(
            f"profile {profile.name!r} has no browser session", hint='run "aistudio login" first'
        )
    factory = playwright_factory or default_playwright_factory()
    async with factory() as playwright:
        context = await launch_persistent(playwright, profile, browser="chrome",
                                          headless=window == "headless", viewport=VIEWPORT)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            if window == "minimized":
                await _minimize(context, page)
            yield PlaywrightUi(page)
        finally:
            try:
                await context.close()
            except Exception:  # the user may have closed the window already
                pass
```

In `src/aistudio/cli/state.py`, add this method to `CliState`:

```python
    def require_browser(self) -> None:
        """Generation is page-driven (spec §5.4); plain HTTP cannot do it."""
        if self.backend == "http":
            raise ValidationError(
                "this command needs the browser; --backend http cannot generate",
                hint="drop --backend or use --backend browser",
            )
```

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS (existing login tests included); `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/aistudio/_auth/login.py src/aistudio/_browser src/aistudio/cli/state.py tests/conftest.py tests/unit/test_browser_ui.py tests/unit/test_login.py
git commit -m "feat: add the browser Ui layer (minimized window, response log)"
```

---

### Task 3: `BuildPage` driver

**Files:**
- Create: `src/aistudio/_browser/build_page.py`
- Test: `tests/unit/test_build_page.py`

**Interfaces:**
- Consumes:
  - Task 1 decoders and `BuildProgress`;
  - Task 2 `Ui`, `CapturedResponse`, `RUN_SHORTCUT` and the `fake_ui` fixture;
  - `error_for_response`, `wire.decode`, `BotGuardError`, `BrowserError`.
- Produces:
  - The constants `APPS_URL`, `PROMPT_BOX = "textarea"`, `START_TIMEOUT_S = 60.0`, `CREATE`, `START` and `STREAM`.
  - `app_url(app_id)`.
  - `BuildOutcome(app_id, run_id, progress: BuildProgress | None)`, where `progress` is None on a timeout.
  - `BuildPage(ui, *, now=time.monotonic)` with `async create(idea, *, timeout_s)` and `async prompt(app_id, text, *, timeout_s)`, both returning `BuildOutcome`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_build_page.py`:

```python
import pytest

from aistudio._browser.build_page import APPS_URL, PROMPT_BOX, BuildPage, app_url
from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse
from aistudio._rpc import wire
from aistudio.exceptions import BotGuardError, BrowserError

APP = "912435b3-26c1-41b4-ba22-058d464afceb"
RUN = "463f35e4-afb1-41fc-925f-8d550c1d0ffc"
UPDATE_RUN = "66ca59ad-237a-4446-b206-eca5cabac1c7"


def reply(method, captured_record, status=200):
    return CapturedResponse(method, status, captured_record.raw["response"]["body"], "")


def started(captured, n):
    return [reply("CodeAssistantOffline", captured("CodeAssistantOffline", n))]


def stream(captured, run_id=RUN, finished=True):
    response = captured("StreamCodeAssistantOfflineGeneration").response
    if not finished:
        response = [response[0][:-1]]
    text = wire.encode(response).replace(RUN, run_id)
    return CapturedResponse("StreamCodeAssistantOfflineGeneration", 200, text, "")


async def test_create_follows_the_run_to_completion(fake_ui, captured):
    ui = fake_ui({
        "CreateApplet": [reply("CreateApplet", captured("CreateApplet", 2))],
        "CodeAssistantOffline": started(captured, 2),
        "StreamCodeAssistantOfflineGeneration": [stream(captured, finished=False),
                                                 stream(captured)],
    })
    outcome = await BuildPage(ui).create("a counter", timeout_s=600)
    assert (outcome.app_id, outcome.run_id) == (APP, RUN)
    assert outcome.progress.done and outcome.progress.steps == 24
    assert ui.ops == [("goto", APPS_URL), ("fill", PROMPT_BOX, "a counter"),
                      ("press", PROMPT_BOX, RUN_SHORTCUT)]


async def test_prompt_ignores_streams_of_older_runs(fake_ui, captured):
    ui = fake_ui({
        "CodeAssistantOffline": started(captured, 3),
        "StreamCodeAssistantOfflineGeneration": [stream(captured, run_id=RUN),
                                                 stream(captured, run_id=UPDATE_RUN)],
    })
    outcome = await BuildPage(ui).prompt(APP, "make it blue", timeout_s=600)
    assert outcome.run_id == UPDATE_RUN and outcome.progress.run_id == UPDATE_RUN
    assert ui.ops[0] == ("goto", app_url(APP))


async def test_no_completed_stream_is_a_timeout_outcome(fake_ui, captured):
    ui = fake_ui({
        "CodeAssistantOffline": started(captured, 3),
        "StreamCodeAssistantOfflineGeneration": [stream(captured, run_id=RUN)],
    })
    outcome = await BuildPage(ui).prompt(APP, "make it blue", timeout_s=600)
    assert (outcome.run_id, outcome.progress) == (UPDATE_RUN, None)


async def test_a_refused_start_is_botguard_rejected(fake_ui):
    refused = CapturedResponse("CodeAssistantOffline", 403,
                               '[,[7,"The caller does not have permission"]]', "")
    ui = fake_ui({"CodeAssistantOffline": [refused]})
    with pytest.raises(BotGuardError) as info:
        await BuildPage(ui).prompt(APP, "x", timeout_s=60)
    assert "--visible" in info.value.hint


async def test_a_missing_create_response_is_a_browser_error(fake_ui):
    with pytest.raises(BrowserError) as info:
        await BuildPage(fake_ui()).create("a counter", timeout_s=60)
    assert "CreateApplet" in info.value.message


async def test_an_undecodable_stream_is_skipped(fake_ui, captured):
    ui = fake_ui({
        "CodeAssistantOffline": started(captured, 2),
        "StreamCodeAssistantOfflineGeneration": [
            CapturedResponse("StreamCodeAssistantOfflineGeneration", 200, "<html>", ""),
            stream(captured),
        ],
    })
    outcome = await BuildPage(ui).prompt(APP, "x", timeout_s=60)
    assert outcome.progress.done
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_build_page.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._browser.build_page'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_browser/build_page.py`:

```python
"""Drive AI Studio Build to create or update an app (spec §6.7, §5.4 page driving).

The page sends CreateApplet (create only), then CodeAssistantOffline (BotGuard-protected; returns
the run id), then follows the run through StreamCodeAssistantOfflineGeneration, whose response
completes with the final model message when the build is done (docs/wire/findings.md).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, ResponseLog, Ui
from aistudio._rpc import wire
from aistudio._rpc.rows.build import decode_build_stream, decode_created_app, decode_started_run
from aistudio._transport.http import error_for_response
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import BotGuardError, BrowserError

APPS_URL = "https://aistudio.google.com/apps"
PROMPT_BOX = "textarea"
START_TIMEOUT_S = 60.0
CREATE = "CreateApplet"
START = "CodeAssistantOffline"
STREAM = "StreamCodeAssistantOfflineGeneration"


def app_url(app_id: str) -> str:
    return f"{APPS_URL}/{app_id}"


@dataclass(frozen=True)
class BuildOutcome:
    app_id: str
    run_id: str
    progress: BuildProgress | None  # None: --timeout passed before the build finished


class BuildPage:
    def __init__(self, ui: Ui, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ui = ui
        self._now = now

    async def create(self, idea: str, *, timeout_s: float) -> BuildOutcome:
        async with self._ui.responses() as log:
            await self._ui.goto(APPS_URL)
            await self._submit(idea)
            decode_created_app(wire.decode((await self._expect(log, CREATE)).text))
            return await self._follow(log, timeout_s)

    async def prompt(self, app_id: str, text: str, *, timeout_s: float) -> BuildOutcome:
        async with self._ui.responses() as log:
            await self._ui.goto(app_url(app_id))
            await self._submit(text)
            return await self._follow(log, timeout_s)

    async def _submit(self, text: str) -> None:
        await self._ui.fill_first(PROMPT_BOX, text)
        await self._ui.press_first(PROMPT_BOX, RUN_SHORTCUT)

    async def _expect(self, log: ResponseLog, method: str) -> CapturedResponse:
        response = await log.next(method, timeout_s=START_TIMEOUT_S)
        if response is None:
            raise BrowserError(
                f"AI Studio did not send {method} within {START_TIMEOUT_S:.0f}s",
                hint="re-run; if it persists, the AI Studio UI changed",
            )
        if response.status >= 400:
            err = error_for_response(response.status, response.text)
            if response.status == 403:
                raise BotGuardError(f"Google refused the build request: {err.message}",
                                    hint="re-run; if it keeps failing, add --visible")
            raise err
        return response

    async def _follow(self, log: ResponseLog, timeout_s: float) -> BuildOutcome:
        run_id, app_id = decode_started_run(wire.decode((await self._expect(log, START)).text))
        deadline = self._now() + timeout_s
        while (remaining := deadline - self._now()) > 0:
            response = await log.next(STREAM, timeout_s=remaining)
            if response is None:
                break
            if response.status >= 400:
                continue
            try:
                progress = decode_build_stream(wire.decode(response.text))
            except ValueError:  # an aborted or non-JSON stream body
                continue
            if progress.run_id == run_id and progress.done:
                return BuildOutcome(app_id, run_id, progress)
        return BuildOutcome(app_id, run_id, None)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_browser/build_page.py tests/unit/test_build_page.py
git commit -m "feat: drive AI Studio Build to create and update apps"
```

---

### Task 4: Build use cases

**Files:**
- Create: `src/aistudio/_app/build.py`
- Test: `tests/unit/test_app_build.py`

**Interfaces:**
- Consumes:
  - `BuildPage`, `BuildOutcome` and `app_url` (Task 3), `open_browser` (Task 2);
  - `pick` (`_app/apps.py`), `AIStudioClient.from_profile`;
  - `ValidationError`.
- Produces:
  - `_app.build.DEFAULT_TIMEOUT_S = 600`.
  - `async create_app(profile, idea, *, timeout_s=600, visible=False, client_factory=AIStudioClient.from_profile, browser=open_browser, now=time.monotonic) -> dict`.
  - `async prompt_app(profile, app_ref, text, *, …same…) -> dict`.
  - Both return `{app_id, run_id, url, title, status ("done"|"timeout"), steps, files, seconds}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_app_build.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_app_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._app.build'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_app/build.py`:

```python
"""Create and update AI Studio Build apps (spec §6.7). Page-driven in a minimized window."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from aistudio._app.apps import pick
from aistudio._browser.build_page import BuildOutcome, BuildPage, app_url
from aistudio._browser.ui import open_browser
from aistudio.client import AIStudioClient
from aistudio.exceptions import ValidationError
from aistudio.paths import Profile

DEFAULT_TIMEOUT_S = 600


async def create_app(
    profile: Profile,
    idea: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    visible: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not idea.strip():
        raise ValidationError("nothing to build: the idea is empty")
    started = now()
    async with client_factory(profile) as client:
        await client.apps.list(1)  # fails fast with AUTH_ERROR before any browser opens
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        outcome = await BuildPage(ui).create(idea, timeout_s=timeout_s)
    return await _result(profile, outcome, started, now, client_factory)


async def prompt_app(
    profile: Profile,
    app_ref: str,
    text: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    visible: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not text.strip():
        raise ValidationError("nothing to change: the prompt is empty")
    started = now()
    async with client_factory(profile) as client:
        app = pick(await client.apps.list(), lambda a: a.id, app_ref, noun="app",
                   hint='see "aistudio app list"')
    if app.storage != "applet":
        raise ValidationError(
            f"{app.title or app.id!r} is stored in Google Drive (an older Build app); "
            "aistudio can only update Build-store apps",
            hint="open it at https://aistudio.google.com/apps",
        )
    async with browser(profile, window="visible" if visible else "minimized") as ui:
        outcome = await BuildPage(ui).prompt(app.id, text, timeout_s=timeout_s)
    return await _result(profile, outcome, started, now, client_factory)


async def _result(
    profile: Profile,
    outcome: BuildOutcome,
    started: float,
    now: Callable[[], float],
    client_factory: Callable[[Profile], AIStudioClient],
) -> dict[str, Any]:
    async with client_factory(profile) as client:
        title = next((a.title for a in await client.apps.list() if a.id == outcome.app_id), None)
    progress = outcome.progress
    return {
        "app_id": outcome.app_id,
        "run_id": outcome.run_id,
        "url": app_url(outcome.app_id),
        "title": title,
        "status": "done" if progress else "timeout",
        "steps": progress.steps if progress else None,
        "files": list(progress.files) if progress else [],
        "seconds": round(now() - started),
    }
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_app/build.py tests/unit/test_app_build.py
git commit -m "feat: create and update Build apps use cases"
```

---

### Task 5: CLI `app create` and `app prompt`

**Files:**
- Modify: `src/aistudio/cli/app_cmd.py`
- Test: `tests/cli/test_app_build_cmd.py`

**Interfaces:**
- Consumes: `create_app`, `prompt_app` and `DEFAULT_TIMEOUT_S` (Task 4); `CliState.require_browser` (Task 2); `cells`, `emit`, `stderr`, `stdout` (`cli.output`)
- Produces: the click commands `app create IDEA` and `app prompt APP TEXT`, each with `[--timeout S] [--visible] [--json]`. A timeout prints the result and exits 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_app_build_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli

RESULT = {"app_id": "a1", "run_id": "r1", "url": "https://aistudio.google.com/apps/a1",
          "title": "Pomodoro [Focus] Timer", "status": "done", "steps": 12,
          "files": ["index.html", "src/App.tsx"], "seconds": 151}


def fake(result, calls):
    async def run(profile, *args, **kwargs):
        calls.append((profile.name, args, kwargs))
        return result

    return run


def test_app_create(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.app_cmd.create_app", fake(RESULT, calls))
    res = CliRunner().invoke(cli, ["app", "create", "a pomodoro timer", "--timeout", "300",
                                   "--visible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == RESULT
    assert calls == [("default", ("a pomodoro timer",), {"timeout_s": 300, "visible": True})]


def test_app_prompt_human_output(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.app_cmd.prompt_app", fake(RESULT, calls))
    res = CliRunner().invoke(cli, ["app", "prompt", "a1", "make it blue"])
    assert res.exit_code == 0, res.output
    assert "Pomodoro [Focus] Timer" in res.stdout
    assert "https://aistudio.google.com/apps/a1" in res.stdout
    assert calls[0][1] == ("a1", "make it blue")


def test_timeout_prints_the_result_and_exits_2(logged_in_profile, monkeypatch):
    logged_in_profile()
    monkeypatch.setattr("aistudio.cli.app_cmd.create_app",
                        fake({**RESULT, "status": "timeout", "steps": None, "files": []}, []))
    res = CliRunner().invoke(cli, ["app", "create", "x", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["status"] == "timeout"


def test_http_backend_cannot_build(logged_in_profile):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["--backend", "http", "app", "create", "x", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_app_group_help_is_not_read_only():
    res = CliRunner().invoke(cli, ["--help"])
    assert "read-only" not in res.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_app_build_cmd.py -v`
Expected: FAIL with `No such command 'create'` (and the help test fails on "read-only")

- [ ] **Step 3: Write the implementation**

In `src/aistudio/cli/app_cmd.py`:

- add `from aistudio._app.build import DEFAULT_TIMEOUT_S, create_app, prompt_app` to the imports;
- change `from aistudio.cli.output import cells, emit, json_option, stdout` to
  `from aistudio.cli.output import cells, emit, json_option, stderr, stdout`;
- change the `app` group docstring from `"""Your AI Studio Build apps (read-only)."""` to
  `"""Your AI Studio Build apps: list, inspect, create and update."""`;
- append:

```python


def _render_build(d: dict[str, Any]) -> None:
    state = "done" if d["status"] == "done" else "still building (timed out waiting)"
    stdout.print(f"{d['title'] or '(untitled)'}  [{d['app_id']}]  {state} after {d['seconds']}s",
                 markup=False)
    stdout.print(d["url"], markup=False)
    if d["files"]:
        stdout.print(f"Files: {', '.join(d['files'])}", markup=False)


def _build(state: CliState, run: Callable[..., Awaitable[dict[str, Any]]], *args: Any,
           timeout: int, visible: bool, json_output: bool) -> None:
    state.require_browser()
    if not json_output:
        stderr.print("Building in a minimized Chrome window; this usually takes a few minutes…")
    result = asyncio.run(run(state.profile(), *args, timeout_s=timeout, visible=visible))
    emit(result, json_output=json_output, render=_render_build)
    if result["status"] != "done":
        raise SystemExit(2)


@app.command("create")
@click.argument("idea")
@click.option("--timeout", type=click.IntRange(30, 3600), default=DEFAULT_TIMEOUT_S,
              show_default=True, help="Seconds to wait for the build to finish.")
@click.option("--visible", is_flag=True, help="Show the browser window instead of minimizing it.")
@json_option
@click.pass_obj
@handle_errors
def app_create(state: CliState, idea: str, timeout: int, visible: bool,
               json_output: bool) -> None:
    """Build a new app from IDEA (uses your AI Studio Build quota)."""
    _build(state, create_app, idea, timeout=timeout, visible=visible, json_output=json_output)


@app.command("prompt")
@click.argument("app_id")
@click.argument("text")
@click.option("--timeout", type=click.IntRange(30, 3600), default=DEFAULT_TIMEOUT_S,
              show_default=True, help="Seconds to wait for the update to finish.")
@click.option("--visible", is_flag=True, help="Show the browser window instead of minimizing it.")
@json_option
@click.pass_obj
@handle_errors
def app_prompt(state: CliState, app_id: str, text: str, timeout: int, visible: bool,
               json_output: bool) -> None:
    """Ask the builder to change an app (id or unique prefix) with TEXT."""
    _build(state, prompt_app, app_id, text, timeout=timeout, visible=visible,
           json_output=json_output)
```

`Awaitable` and `Callable` are already imported in `app_cmd.py` from `collections.abc`.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/cli/app_cmd.py tests/cli/test_app_build_cmd.py
git commit -m "feat: add app create and app prompt commands"
```

---

### Task 6: Live check, README, skill

**Files:**
- Create: `tests/live/test_live_build_create.py`
- Modify: `README.md`, `integrations/claude-code/aistudio/SKILL.md` (then copy it to `~/.claude/skills/aistudio/SKILL.md`)

- [ ] **Step 1: Write the live test**

Create `tests/live/test_live_build_create.py`:

```python
"""Opt-in: `uv run pytest -m live_slow tests/live/test_live_build_create.py`.

Builds one tiny app and updates it once (uses Build quota, takes several minutes, opens a
minimized Chrome window). The app stays in your Build account.
"""

import os

import pytest

from aistudio._app.build import create_app, prompt_app
from aistudio.paths import get_profile, resolve_profile_name

pytestmark = pytest.mark.live_slow


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.chrome_dir.exists():
        pytest.skip('no browser session; run "aistudio login"')
    return p


async def test_live_create_then_prompt(profile):
    created = await create_app(profile, "A web page that shows today's date in large text.")
    assert created["status"] == "done" and "index.html" in created["files"]
    updated = await prompt_app(profile, created["app_id"], "Make the text dark blue.")
    assert updated["status"] == "done" and updated["app_id"] == created["app_id"]
```

- [ ] **Step 2: Run the default suite and try the real commands**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: the default suite passes, and the live tests are deselected.

With the user's approval, run one real create and one real prompt:
- `aistudio app create "A web page that shows a random motivational quote with a New quote button" --json`
- `aistudio app prompt <app_id> "Use a dark theme" --json`

Expected: `"status": "done"` with `index.html` among the files.

- [ ] **Step 3: Update the README quick start and the skill**

In `README.md`, add these lines to the Quick start block, after the `app get` line:

```bash
uv run aistudio app create "a pomodoro timer with dark mode"   # builds in a minimized Chrome (~2-4 min)
uv run aistudio app prompt <id-prefix> "make the start button green"
```

In `integrations/claude-code/aistudio/SKILL.md`, move "Creating, updating or exporting Build apps"
out of "Not available yet". Replace that bullet with "Exporting Build apps' code". Add a section
before "## Ask first":

````markdown
## Build or change an app (only when the user asks)

```bash
aistudio app create "<idea>" --json             # new app
aistudio app prompt <app-id-or-prefix> "<change>" --json
```

- Both take a few minutes, use the user's Build quota, and open a minimized Chrome window.
  Run them in the background if your harness supports that.
- Result: `{app_id, url, title, status, files, seconds}`. `status: "timeout"` (exit 2) means
  it is still building: give the user the `url`.
- Pass the user's own words as the idea/change; do not expand them.
````

Copy the result: `cp integrations/claude-code/aistudio/SKILL.md ~/.claude/skills/aistudio/SKILL.md`

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_live_build_create.py README.md integrations/claude-code/aistudio/SKILL.md
git commit -m "docs: live Build create test; document app create/prompt in README and skill"
```
