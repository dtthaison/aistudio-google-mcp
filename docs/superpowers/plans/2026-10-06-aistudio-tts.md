# aistudio-py TTS: `generate speech` and `voices list` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two commands:

- `aistudio generate speech "text" [-m MODEL] [--voice NAME] [--style TEXT] [-o FILE.wav]` writes a WAV.
  Expression tags such as `<laugh>` go inline in the text.
- `aistudio voices list` lists the available voices.

**Architecture:**

- **The page builds generation requests.** BotGuard binds its token to the request content and
  refuses headless Chrome (findings, "H4 resolution" and "TTS end-to-end drive"). So a visible,
  1600×1000 Chrome window drives AI Studio's speech page, and the response is captured from the network.
- **`Ui` interface.** A narrow `Ui` protocol (`_browser/ui.py`) isolates Playwright.
  - `SpeechPage` (`_browser/speech_page.py`) is written against `Ui`, so a scripted `FakeUi` tests
    every branch offline.
  - `PlaywrightUi` is thin glue, verified by the opt-in live test.
- **Audio.** A decoder (`rows/speech.py`) joins the streamed PCM chunks, and a WAV writer
  (`_media/wav.py`) adds the header. Both are golden-tested against the captured 10.56 s response.

**Tech Stack:** unchanged (Python ≥3.10, click, httpx, rich, filelock, Playwright via `[browser]`, pytest, respx, ruff).

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` (§5.4 revised, §6.4 speech row).
**Evidence:** `docs/wire/findings.md` ("Follow-up session"), the fixtures `GenerateContent/1` and `/3`, and `scripts/spikes/tts_*.py`.

**Scope:**

- **Implements:**
  - `generate speech` (single speaker, model, voice, style, inline expressions);
  - `voices list`;
  - the browser `Ui` layer;
  - fixes to two deferred minors that now matter here: the misleading "install Chrome" hint when
    the profile is already in use, and the missing `viewport` for desktop layouts.
- **Deferred:**
  - multi-speaker dialogs (`--speakers`, "Add speech block");
  - custom voices ("New voice");
  - voice filters (Language, Gender, Pitch, Style, Use case);
  - MCP tools;
  - an off-screen or minimised window, which would need its own BotGuard test.

## Global Constraints

- All Global Constraints of the M0–M1 and M2 plans still hold, including:
  - the `--json` envelope and exit codes;
  - ruff line length 100;
  - no secrets in output or logs;
  - decoders raise `SchemaDriftError` only for required data.
- **Visible browser.** Generation opens a **visible** Chrome window by default. `--headless` exists
  but is documented as currently refused by Google. `voices list` runs headless.
- **Viewport.** The page is driven at exactly 1600×1000.
- **Controls by accessible role and name**, as mapped in findings:

  | Purpose | Role | Name |
  |---|---|---|
  | Open a dialog | button | `Create new dialog` (regex) |
  | Text | textbox | `Speech block text` |
  | Model | button | matching `gemini-[\w.-]*tts`; a "Search" textbox in the picker |
  | Voice | button | `^Speaker 1 - ` |
  | Voice search | textbox | `Search voices` |
  | Voice style | button, then textbox | `Style` (exact), then `Describe the voice style` |
  | Voice groups | button | `^View all \d+` |
  | Back from a group | button | matching `back` (case-insensitive) |
  | Voice names | button | `^(?:Favorite\|Remove from favorites) (\S+)$` |

  - **Run** is pressed with **Meta+Enter** on macOS (Control+Enter elsewhere) in the text box.
  - After a voice is chosen, the voice panel is closed with **Escape**.
- **Audio format.** `audio/l16; rate=24000; channels=1` PCM. The WAV is 16-bit with the rate and
  channels from the mime type. Only `audio/l16` is accepted.
- **Default model** `gemini-3.8-flash-tts`. The default output is
  `aistudio-speech-YYYYMMDD-HHMMSS.wav` in the current directory. An existing file is never
  overwritten without `--force`, and nothing is written unless the response decodes.
- **The model is checked against the cached catalog before any browser opens.** It must have
  `command == "generate speech"`.

## Review Focus

1. **The output file already exists.** It is not overwritten without `--force`. A failed or
   refused generation leaves **no** file. Tests in Task 4.
2. **A voice typed in any case, or one that doesn't exist.** `tova` works. An unknown voice gives
   `VALIDATION_ERROR` with a `voices list` hint, and the panel is closed. Tests in Task 3.
3. **A non-TTS model** (`gemini-3.5-flash`, `lyria-3.5`). It fails before any browser opens and
   the hint names the right command. Test in Task 4.
4. **Google returns 403** (anti-bot, or a paid model on a free account). The result is
   `BOTGUARD_REJECTED` carrying the server message and a hint, never a crash or an empty WAV.
   Test in Task 4.
5. **A response with missing or corrupt audio.** It gives `SCHEMA_DRIFT`, never a silent or broken
   WAV. Test in Task 1, using the captured fixture whose media was replaced by placeholders.

---

## File Structure

```
src/aistudio/
  _types/media.py              SpeechAudio
  _rpc/rows/speech.py          decode_speech (collect audio parts, PCM)
  _media/{__init__,wav}.py     pcm_to_wav
  _auth/login.py               launch_persistent(..., viewport=None); profile-in-use hint
  _browser/{__init__,ui}.py    Ui protocol, CapturedResponse, PlaywrightUi, open_browser
  _browser/speech_page.py      SpeechPage driver
  _app/media.py                check_speech_model, generate_speech, list_voices
  cli/generate_cmd.py          `generate speech`, `voices list`
  cli/state.py                 + require_browser()
  cli/main.py                  register generate, voices
tests/conftest.py              + FakeUi class fixture (fake_ui)
tests/unit/{test_rows_speech,test_wav,test_browser_ui,test_speech_page,test_app_media}.py
tests/cli/test_generate_cmd.py, tests/unit/test_login.py (+1 test)
tests/live/test_live_speech.py
```

---

### Task 1: Speech audio decoding and WAV writing

**Files:**
- Create: `src/aistudio/_types/media.py`, `src/aistudio/_rpc/rows/speech.py`, `src/aistudio/_media/__init__.py` (empty), `src/aistudio/_media/wav.py`
- Test: `tests/unit/test_rows_speech.py`, `tests/unit/test_wav.py`

**Interfaces:**
- Consumes: `SchemaDriftError`; the `captured` fixture (M2).
- Produces:
  - `_types.media.SpeechAudio(mime_type: str, rate: int, channels: int, pcm: bytes)` with a `.seconds` property.
  - `rows.speech.decode_speech(response) -> SpeechAudio`.
  - `_media.wav.pcm_to_wav(pcm, *, rate, channels=1, sample_width=2) -> bytes`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_wav.py`:

```python
import io
import wave

from aistudio._media.wav import pcm_to_wav


def test_pcm_to_wav_writes_a_readable_header():
    pcm = b"\x01\x00\xff\x7f" * 12000  # 24000 16-bit samples
    data = pcm_to_wav(pcm, rate=24000, channels=1)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.getnframes() == 24000
        assert w.readframes(24000) == pcm
```

Create `tests/unit/test_rows_speech.py`:

```python
import pytest

from aistudio._rpc.rows.speech import decode_speech
from aistudio.exceptions import SchemaDriftError


def test_decodes_the_captured_speech_response(captured):
    audio = decode_speech(captured("GenerateContent", 3).response)
    assert audio.mime_type == "audio/l16; rate=24000; channels=1"
    assert (audio.rate, audio.channels) == (24000, 1)
    assert len(audio.pcm) == 506880
    assert audio.seconds == pytest.approx(10.56)


def test_placeholder_media_is_schema_drift(captured):
    with pytest.raises(SchemaDriftError):
        decode_speech(captured("GenerateContent", 1).response)


@pytest.mark.parametrize(
    "response",
    [
        [],
        [[[[[[[[None, None, ["image/jpeg", "AAAA"]]]]]]]]],
        [[[[[[[[None, None, ["audio/mpeg", "AAAA"]]]]]]]]],
    ],
)
def test_missing_or_non_pcm_audio_is_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_speech(response)


def chunk(data):
    return [[[[[[[None, None, ["audio/l16; rate=8000; channels=1", data]]]]]]]]


def test_chunks_are_joined_in_order():
    audio = decode_speech([[chunk("AQA="), chunk("AgA="), chunk("AwA=")]])
    assert audio.pcm == b"\x01\x00\x02\x00\x03\x00"
    assert (audio.rate, audio.channels) == (8000, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wav.py tests/unit/test_rows_speech.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._media'` (or `…rows.speech`)

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_types/media.py`:

```python
"""Media generation result types (spec §6.4)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechAudio:
    mime_type: str  # e.g. "audio/l16; rate=24000; channels=1"
    rate: int
    channels: int
    pcm: bytes  # 16-bit little-endian samples

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (self.rate * self.channels * 2)
```

Create `src/aistudio/_media/__init__.py` as an empty file.

Create `src/aistudio/_media/wav.py`:

```python
"""WAV container for the raw PCM that AI Studio's TTS returns (spec §6.4)."""

from __future__ import annotations

import io
import wave


def pcm_to_wav(pcm: bytes, *, rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()
```

Create `src/aistudio/_rpc/rows/speech.py`:

```python
"""Decode text-to-speech ``GenerateContent`` responses (fixtures GenerateContent/1, /3).

The response streams chunks ``resp[0][i]``; each carries one audio part at
``chunk[0][0][0][0][2] = [mime, base64 PCM]`` with mime ``audio/l16; rate=24000; channels=1``.
The decoder collects every ``[mime, data]`` pair whose mime starts with ``audio/`` in document
order, so it does not depend on the exact nesting depth.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from aistudio._types.media import SpeechAudio
from aistudio.exceptions import SchemaDriftError

METHOD = "GenerateContent"
_PARAM = re.compile(r"(\w+)=(\d+)")


def decode_speech(response: Any) -> SpeechAudio:
    parts: list[tuple[str, str]] = []
    _collect(response, parts)
    if not parts:
        raise SchemaDriftError(METHOD, (), "audio parts")
    mime = parts[0][0]
    if not mime.startswith("audio/l16"):
        raise SchemaDriftError(METHOD, (), "audio/l16 PCM")
    try:
        pcm = b"".join(base64.b64decode(data, validate=True) for _, data in parts)
    except ValueError as exc:
        raise SchemaDriftError(METHOD, (), "base64 audio data") from exc
    params = dict(_PARAM.findall(mime))
    return SpeechAudio(
        mime_type=mime,
        rate=int(params.get("rate", 24000)),
        channels=int(params.get("channels", 1)),
        pcm=pcm,
    )


def _collect(node: Any, out: list[tuple[str, str]]) -> None:
    if not isinstance(node, list):
        return
    if (len(node) >= 2 and isinstance(node[0], str) and node[0].startswith("audio/")
            and isinstance(node[1], str)):
        out.append((node[0], node[1]))
        return
    for child in node:
        _collect(child, out)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_wav.py tests/unit/test_rows_speech.py -v 2>&1 | tail -3 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_types/media.py src/aistudio/_rpc/rows/speech.py src/aistudio/_media tests/unit/test_wav.py tests/unit/test_rows_speech.py
git commit -m "feat: decode TTS audio chunks and write WAV files"
```

---

### Task 2: Browser `Ui` layer

**Files:**
- Create: `src/aistudio/_browser/__init__.py` (empty), `src/aistudio/_browser/ui.py`
- Modify: `src/aistudio/_auth/login.py` (`launch_persistent` gains `viewport`; profile-in-use hint), `tests/conftest.py` (add the `fake_ui` fixture)
- Test: `tests/unit/test_browser_ui.py`, plus one new test in `tests/unit/test_login.py`

**Interfaces:**
- Consumes:
  - `launch_persistent(playwright, profile, *, browser, headless)` and `default_playwright_factory()` (M0–M1 Task 8);
  - `BrowserError`.
- Produces:
  - `launch_persistent(..., viewport: dict[str, int] | None = None)`. With a viewport it passes `viewport=`, otherwise `no_viewport=True`.
  - `_browser.ui`:
    - the constants `VIEWPORT = {"width": 1600, "height": 1000}` and `RUN_SHORTCUT`;
    - `CapturedResponse(status, text, request_body)`;
    - the `Ui` Protocol (`goto`, `click`, `fill`, `press`, `key`, `visible`, `labels`, `pause`, `capture`);
    - `PlaywrightUi(page, *, timeout_ms=30000)`;
    - `open_browser(profile, *, headless=False, playwright_factory=None)`, an async context manager yielding a `Ui`.
  - The pytest fixture `fake_ui`, which returns the `FakeUi` class. Its constructor is `FakeUi(labels=None, visible=None, response=None)`; it records `.ops` and takes scripted answers.

- [ ] **Step 1: Add `FakeUi` to `tests/conftest.py`**

Add `import re` to the imports at the top of `tests/conftest.py`, then append:

```python
class FakeUi:
    """Scripted stand-in for aistudio._browser.ui.Ui.

    Records every operation in ``ops``. ``labels``/``visible`` map ``(role, name)`` — where a
    regex name is keyed by its pattern string — to a list of answers consumed one per call
    (the last answer repeats).
    """

    def __init__(self, labels=None, visible=None, response=None):
        from aistudio._browser.ui import CapturedResponse

        self.ops = []
        self._labels = {k: list(v) for k, v in (labels or {}).items()}
        self._visible = {k: list(v) for k, v in (visible or {}).items()}
        self.response = response or CapturedResponse(200, "[]", "[]")

    @staticmethod
    def _name(name):
        return name.pattern if isinstance(name, re.Pattern) else name

    def _next(self, table, key, default):
        answers = table.get(key)
        if not answers:
            return default
        return answers.pop(0) if len(answers) > 1 else answers[0]

    async def goto(self, url):
        self.ops.append(("goto", url))

    async def click(self, role, name, *, exact=False, nth=0):
        self.ops.append(("click", role, self._name(name), nth))

    async def fill(self, role, name, text):
        self.ops.append(("fill", role, self._name(name), text))

    async def press(self, role, name, key):
        self.ops.append(("press", role, self._name(name), key))

    async def key(self, key):
        self.ops.append(("key", key))

    async def visible(self, role, name, *, exact=False):
        return self._next(self._visible, (role, self._name(name)), False)

    async def labels(self, role, name):
        return self._next(self._labels, (role, self._name(name)), [])

    async def pause(self, ms):
        pass

    async def capture(self, url_part, trigger, *, timeout_ms):
        self.ops.append(("capture", url_part))
        await trigger()
        return self.response


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


class FailingLocator:
    def nth(self, _):
        return self

    @property
    def first(self):
        return self

    async def click(self, timeout=None):
        raise TimeoutError("Timeout 30000ms exceeded")

    async def fill(self, text, timeout=None):
        raise TimeoutError("Timeout 30000ms exceeded")


class FailingPage:
    def get_by_role(self, role, name=None, exact=False):
        return FailingLocator()


async def test_missing_controls_become_a_clear_browser_error():
    ui = PlaywrightUi(FailingPage())
    with pytest.raises(BrowserError) as info:
        await ui.fill("textbox", "Speech block text", "hi")
    assert "Speech block text" in info.value.message
    assert "AI Studio page changed" in info.value.message


class Context:
    def __init__(self):
        self.pages = ["page"]
        self.closed = False

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


async def test_open_browser_uses_a_desktop_viewport_and_closes(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    context = Context()
    chromium = Chromium(context)
    async with open_browser(profile, playwright_factory=lambda: Playwright(chromium)) as ui:
        assert isinstance(ui, PlaywrightUi)
    assert chromium.kwargs["viewport"] == VIEWPORT
    assert chromium.kwargs["headless"] is False
    assert "no_viewport" not in chromium.kwargs
    assert context.closed


async def test_open_browser_requires_a_logged_in_profile(aistudio_home):
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

Page drivers such as SpeechPage talk to ``Ui``; ``PlaywrightUi`` is the thin real implementation
and tests use a scripted fake. Controls are found by accessible role and name, which survives
styling changes better than CSS selectors.
"""

from __future__ import annotations

import contextlib
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio.exceptions import BrowserError
from aistudio.paths import Profile

VIEWPORT = {"width": 1600, "height": 1000}  # narrower hides AI Studio's run-settings panel
RUN_SHORTCUT = "Meta+Enter" if sys.platform == "darwin" else "Control+Enter"
Name = str | re.Pattern[str]


@dataclass(frozen=True)
class CapturedResponse:
    status: int
    text: str
    request_body: str


class Ui(Protocol):
    async def goto(self, url: str) -> None: ...

    async def click(self, role: str, name: Name, *, exact: bool = False, nth: int = 0) -> None: ...

    async def fill(self, role: str, name: Name, text: str) -> None: ...

    async def press(self, role: str, name: Name, key: str) -> None: ...

    async def key(self, key: str) -> None: ...

    async def visible(self, role: str, name: Name, *, exact: bool = False) -> bool: ...

    async def labels(self, role: str, name: Name) -> list[str]: ...

    async def pause(self, ms: int) -> None: ...

    async def capture(
        self, url_part: str, trigger: Callable[[], Awaitable[None]], *, timeout_ms: int
    ) -> CapturedResponse: ...


def _describe(role: str, name: Name) -> str:
    return f"{role} {name.pattern if isinstance(name, re.Pattern) else name!r}"


def _drift(role: str, name: Name, exc: Exception) -> BrowserError:
    return BrowserError(
        f"AI Studio page changed or did not load: {_describe(role, name)} not found "
        f"({type(exc).__name__})",
        hint="re-run; if it persists, the AI Studio UI changed and aistudio needs an update",
    )


class PlaywrightUi:
    def __init__(self, page: Any, *, timeout_ms: int = 30_000) -> None:
        self._page = page
        self._timeout = timeout_ms

    def _locator(self, role: str, name: Name, exact: bool = False) -> Any:
        return self._page.get_by_role(role, name=name, exact=exact)

    async def goto(self, url: str) -> None:
        await self._page.goto(url)

    async def click(self, role: str, name: Name, *, exact: bool = False, nth: int = 0) -> None:
        try:
            await self._locator(role, name, exact).nth(nth).click(timeout=self._timeout)
        except Exception as exc:
            raise _drift(role, name, exc) from exc

    async def fill(self, role: str, name: Name, text: str) -> None:
        try:
            await self._locator(role, name).first.fill(text, timeout=self._timeout)
        except Exception as exc:
            raise _drift(role, name, exc) from exc

    async def press(self, role: str, name: Name, key: str) -> None:
        try:
            await self._locator(role, name).first.press(key, timeout=self._timeout)
        except Exception as exc:
            raise _drift(role, name, exc) from exc

    async def key(self, key: str) -> None:
        await self._page.keyboard.press(key)

    async def visible(self, role: str, name: Name, *, exact: bool = False) -> bool:
        locator = self._locator(role, name, exact)
        return await locator.count() > 0 and await locator.first.is_visible()

    async def labels(self, role: str, name: Name) -> list[str]:
        out: list[str] = []
        for element in await self._locator(role, name).all():
            if not await element.is_visible():
                continue
            text = await element.get_attribute("aria-label") or await element.inner_text() or ""
            out.append(" ".join(text.split()))
        return out

    async def pause(self, ms: int) -> None:
        await self._page.wait_for_timeout(ms)

    async def capture(
        self, url_part: str, trigger: Callable[[], Awaitable[None]], *, timeout_ms: int
    ) -> CapturedResponse:
        try:
            async with self._page.expect_response(
                lambda r: url_part in r.url, timeout=timeout_ms
            ) as info:
                await trigger()
            response = await info.value
            return CapturedResponse(
                response.status, await response.text(), response.request.post_data or ""
            )
        except BrowserError:
            raise
        except Exception as exc:
            method = url_part.rsplit("/", 1)[-1]
            raise BrowserError(
                f"no {method} response within {timeout_ms // 1000}s ({type(exc).__name__})",
                hint="re-run; if it persists, the AI Studio UI changed",
            ) from exc


@contextlib.asynccontextmanager
async def open_browser(
    profile: Profile,
    *,
    headless: bool = False,
    playwright_factory: Callable[[], Any] | None = None,
) -> AsyncIterator[Ui]:
    """The profile's Chrome at a desktop viewport. Generation needs ``headless=False``."""
    if not profile.chrome_dir.exists():
        raise BrowserError(
            f"profile {profile.name!r} has no browser session", hint='run "aistudio login" first'
        )
    factory = playwright_factory or default_playwright_factory()
    async with factory() as playwright:
        context = await launch_persistent(
            playwright, profile, browser="chrome", headless=headless, viewport=VIEWPORT
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            yield PlaywrightUi(page)
        finally:
            try:
                await context.close()
            except Exception:  # the user may have closed the window already
                pass
```

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS (existing login tests included); `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/aistudio/_auth/login.py src/aistudio/_browser tests/conftest.py tests/unit/test_browser_ui.py tests/unit/test_login.py
git commit -m "feat: add the browser Ui layer for page-driven generation"
```

---

### Task 3: `SpeechPage` driver

**Files:**
- Create: `src/aistudio/_browser/speech_page.py`
- Test: `tests/unit/test_speech_page.py`

**Interfaces:**
- Consumes: `Ui`, `RUN_SHORTCUT`, `CapturedResponse` (Task 2); the `fake_ui` fixture (Task 2); `BrowserError`, `ValidationError`
- Produces:
  - The constants `SPEECH_URL`, `TEXT_BOX`, `MODEL_BUTTON`, `SPEAKER`, `VOICE_SEARCH`, `STYLE_BOX`, `VIEW_ALL`, `BACK`, `FAVORITE` and `GENERATE_URL`.
  - `voice_display_name(voice) -> str`.
  - `SpeechPage(ui)` with:
    - `async open()`;
    - `async select_model(model_id)`;
    - `async select_voice(voice) -> str`, which returns the display name;
    - `async set_style(style)` and `async set_text(text)`;
    - `async run(*, timeout_ms=180000) -> CapturedResponse`;
    - `async list_voices() -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_speech_page.py`:

```python
import pytest

from aistudio._browser.speech_page import (
    BACK,
    FAVORITE,
    GENERATE_URL,
    MODEL_BUTTON,
    SPEAKER,
    SPEECH_URL,
    TEXT_BOX,
    VIEW_ALL,
    VOICE_SEARCH,
    SpeechPage,
    voice_display_name,
)
from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse
from aistudio.exceptions import BrowserError, ValidationError

CURRENT_MODEL = "Gemini 3.8 Flash TTS gemini-3.8-flash-tts Flagship TTS model"
LITE = "gemini-3.8-flash-lite-tts"


def test_voice_display_name():
    assert voice_display_name(" tova ") == "Tova"
    assert voice_display_name("SADALTAGER") == "Sadaltager"


async def test_open_creates_a_dialog(fake_ui):
    ui = fake_ui()
    await SpeechPage(ui).open()
    assert ui.ops == [("goto", SPEECH_URL), ("click", "button", "Create new dialog", 0)]


async def test_select_model_is_a_no_op_when_already_current(fake_ui):
    ui = fake_ui(labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})
    await SpeechPage(ui).select_model("gemini-3.8-flash-tts")
    assert ui.ops == []


async def test_select_model_searches_and_clicks_the_option(fake_ui):
    ui = fake_ui(
        labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]},
        visible={("button", r"gemini\-3\.8\-flash\-lite\-tts(?![\w.-])"): [True]},
    )
    await SpeechPage(ui).select_model(LITE)
    assert ui.ops == [
        ("click", "button", MODEL_BUTTON.pattern, 0),
        ("fill", "textbox", "Search", LITE),
        ("click", "button", r"gemini\-3\.8\-flash\-lite\-tts(?![\w.-])", 0),
    ]


async def test_unknown_model_is_a_validation_error_and_closes_the_picker(fake_ui):
    ui = fake_ui(labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]})
    with pytest.raises(ValidationError) as info:
        await SpeechPage(ui).select_model("gemini-9-tts")
    assert ui.ops[-1] == ("key", "Escape")
    assert "models list" in info.value.hint


async def test_select_voice_normalizes_the_name_and_closes_the_panel(fake_ui):
    ui = fake_ui(
        labels={("button", SPEAKER.pattern): [["Speaker 1 - Fola arrow_drop_down"],
                                              ["Speaker 1 - Tova arrow_drop_down"]]},
        visible={("button", "Tova"): [True], ("textbox", VOICE_SEARCH): [True]},
    )
    assert await SpeechPage(ui).select_voice("tova") == "Tova"
    assert ui.ops == [
        ("click", "button", SPEAKER.pattern, 0),
        ("fill", "textbox", VOICE_SEARCH, "Tova"),
        ("click", "button", "Tova", 0),
        ("key", "Escape"),
    ]


async def test_select_voice_is_a_no_op_when_already_current(fake_ui):
    ui = fake_ui(labels={("button", SPEAKER.pattern): [["Speaker 1 - Tova arrow_drop_down"]]})
    assert await SpeechPage(ui).select_voice("Tova") == "Tova"
    assert ui.ops == []


async def test_unknown_voice_is_a_validation_error(fake_ui):
    ui = fake_ui(labels={("button", SPEAKER.pattern): [["Speaker 1 - Fola arrow_drop_down"]]})
    with pytest.raises(ValidationError) as info:
        await SpeechPage(ui).select_voice("Nobody")
    assert info.value.hint == 'see "aistudio voices list"'
    assert ui.ops[-1] == ("key", "Escape")


async def test_voice_choice_that_does_not_apply_is_a_browser_error(fake_ui):
    ui = fake_ui(
        labels={("button", SPEAKER.pattern): [["Speaker 1 - Fola arrow_drop_down"]]},
        visible={("button", "Tova"): [True]},
    )
    with pytest.raises(BrowserError):
        await SpeechPage(ui).select_voice("Tova")


async def test_style_text_and_run(fake_ui):
    response = CapturedResponse(200, "[1]", "[2]")
    ui = fake_ui(response=response)
    page = SpeechPage(ui)
    await page.set_style("warm")
    await page.set_text("Hello <laugh>")
    assert await page.run() is response
    assert ui.ops == [
        ("click", "button", "Style", 0),
        ("fill", "textbox", "Describe the voice style", "warm"),
        ("fill", "textbox", TEXT_BOX, "Hello <laugh>"),
        ("capture", GENERATE_URL),
        ("press", "textbox", TEXT_BOX, RUN_SHORTCUT),
    ]


async def test_list_voices_walks_every_group(fake_ui):
    ui = fake_ui(labels={
        ("button", FAVORITE.pattern): [
            ["Favorite Bodi", "Remove from favorites Tova", "Favorite Fola"],
            ["Favorite Bodi", "Favorite Zephyr"],
            ["Favorite Sulafat"],
        ],
        ("button", VIEW_ALL.pattern): [["View all 7", "View all 5"]],
    })
    assert await SpeechPage(ui).list_voices() == ["Bodi", "Fola", "Sulafat", "Tova", "Zephyr"]
    clicks = [op for op in ui.ops if op[0] == "click"]
    assert clicks == [
        ("click", "button", SPEAKER.pattern, 0),
        ("click", "button", VIEW_ALL.pattern, 0),
        ("click", "button", BACK.pattern, 0),
        ("click", "button", VIEW_ALL.pattern, 1),
        ("click", "button", BACK.pattern, 0),
    ]
    assert ui.ops[-1] == ("key", "Escape")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_speech_page.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._browser.speech_page'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_browser/speech_page.py`:

```python
"""Drive AI Studio's speech page (spec §5.4 page driving, §6.4 generate speech).

Controls are located by accessible role and name as mapped in docs/wire/findings.md
("TTS end-to-end drive"). Generation must run in a visible browser: BotGuard refuses headless.
"""

from __future__ import annotations

import re

from aistudio._browser.ui import RUN_SHORTCUT, CapturedResponse, Ui
from aistudio.exceptions import BrowserError, ValidationError

SPEECH_URL = "https://aistudio.google.com/generate-speech"
GENERATE_URL = "MakerSuiteService/GenerateContent"
CREATE_DIALOG = re.compile(r"Create new dialog")
TEXT_BOX = "Speech block text"
MODEL_BUTTON = re.compile(r"gemini-[\w.-]*tts")
MODEL_SEARCH = "Search"
SPEAKER = re.compile(r"^Speaker 1 - ")
VOICE_SEARCH = "Search voices"
STYLE_BUTTON = "Style"
STYLE_BOX = "Describe the voice style"
VIEW_ALL = re.compile(r"^View all \d+")
BACK = re.compile(r"back", re.I)
FAVORITE = re.compile(r"^(?:Favorite|Remove from favorites) (\S+)$")


def voice_display_name(voice: str) -> str:
    name = voice.strip()
    return name[:1].upper() + name[1:].lower()


class SpeechPage:
    def __init__(self, ui: Ui) -> None:
        self._ui = ui

    async def open(self) -> None:
        await self._ui.goto(SPEECH_URL)
        await self._ui.click("button", CREATE_DIALOG)

    async def select_model(self, model_id: str) -> None:
        current = await self._ui.labels("button", MODEL_BUTTON)
        if current and model_id in current[0].split():
            return
        await self._ui.click("button", MODEL_BUTTON)
        await self._ui.fill("textbox", MODEL_SEARCH, model_id)
        await self._ui.pause(1500)
        option = re.compile(re.escape(model_id) + r"(?![\w.-])")
        if not await self._ui.visible("button", option):
            await self._ui.key("Escape")
            raise ValidationError(
                f"the speech page has no model {model_id!r}",
                hint='see "aistudio models list --category audio"',
            )
        await self._ui.click("button", option)
        await self._ui.pause(1000)

    async def select_voice(self, voice: str) -> str:
        name = voice_display_name(voice)
        if await self._speaker_is(name):
            return name
        await self._ui.click("button", SPEAKER)
        await self._ui.fill("textbox", VOICE_SEARCH, name)
        await self._ui.pause(1500)
        if not await self._ui.visible("button", name, exact=True):
            await self._ui.key("Escape")
            raise ValidationError(f"no voice named {name!r}", hint='see "aistudio voices list"')
        await self._ui.click("button", name, exact=True)
        await self._ui.pause(1000)
        if await self._ui.visible("textbox", VOICE_SEARCH):
            await self._ui.key("Escape")  # the panel stays open after a choice
        if not await self._speaker_is(name):
            raise BrowserError(
                f"choosing voice {name!r} did not take effect",
                hint="re-run; if it persists, the AI Studio UI changed",
            )
        return name

    async def set_style(self, style: str) -> None:
        await self._ui.click("button", STYLE_BUTTON, exact=True)
        await self._ui.fill("textbox", STYLE_BOX, style)

    async def set_text(self, text: str) -> None:
        await self._ui.fill("textbox", TEXT_BOX, text)

    async def run(self, *, timeout_ms: int = 180_000) -> CapturedResponse:
        return await self._ui.capture(
            GENERATE_URL,
            lambda: self._ui.press("textbox", TEXT_BOX, RUN_SHORTCUT),
            timeout_ms=timeout_ms,
        )

    async def list_voices(self) -> list[str]:
        await self._ui.click("button", SPEAKER)
        names = await self._voice_names()
        groups = len(await self._ui.labels("button", VIEW_ALL))
        for index in range(groups):
            await self._ui.click("button", VIEW_ALL, nth=index)
            await self._ui.pause(1000)
            names += await self._voice_names()
            await self._ui.click("button", BACK)
            await self._ui.pause(500)
        await self._ui.key("Escape")
        return sorted(set(names))

    async def _speaker_is(self, name: str) -> bool:
        labels = await self._ui.labels("button", SPEAKER)
        return bool(labels) and labels[0].startswith(f"Speaker 1 - {name}")

    async def _voice_names(self) -> list[str]:
        names = []
        for label in await self._ui.labels("button", FAVORITE):
            match = FAVORITE.match(label)
            if match:
                names.append(match.group(1))
        return names
```

`_speaker_is` uses `startswith("Speaker 1 - Tova")`, so a voice named `Tov` would also match
`Tova`. Voice names in the panel are distinct whole words, so this is acceptable. The final check
after clicking is what guards correctness.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_speech_page.py -v 2>&1 | tail -3 && uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_browser/speech_page.py tests/unit/test_speech_page.py
git commit -m "feat: drive the AI Studio speech page (model, voice, style, text, run, voices)"
```

---

### Task 4: Speech use cases

**Files:**
- Create: `src/aistudio/_app/media.py`
- Test: `tests/unit/test_app_media.py`

**Interfaces:**
- Consumes:
  - `SpeechPage` (Task 3), `open_browser` (Task 2), `decode_speech` and `pcm_to_wav` (Task 1);
  - `load_catalog` and `normalize_model_id` (M2), `AIStudioClient.from_profile` (M2);
  - `error_for_response`, `wire.decode`, `wire.opt_index`;
  - `BotGuardError`, `NotFoundError`, `ValidationError`.
- Produces:
  - `_app.media.DEFAULT_SPEECH_MODEL = "gemini-3.8-flash-tts"`.
  - `default_output(now) -> Path`.
  - `async check_speech_model(client, profile, model) -> str`.
  - `async generate_speech(profile, text, *, model=DEFAULT_SPEECH_MODEL, voice=None, style=None, out=None, overwrite=False, headless=False, client_factory=AIStudioClient.from_profile, browser=open_browser, now=datetime.now) -> dict`.
  - `async list_voices(profile, *, search=None, browser=open_browser) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_app_media.py`:

```python
import contextlib
import io
import wave
from datetime import datetime

import httpx
import pytest
import respx

from aistudio._app.media import default_output, generate_speech, list_voices
from aistudio._browser.speech_page import FAVORITE, MODEL_BUTTON, VIEW_ALL
from aistudio._browser.ui import CapturedResponse
from aistudio._transport.http import method_url
from aistudio.exceptions import BotGuardError, ValidationError

CURRENT_MODEL = "Gemini 3.8 Flash TTS gemini-3.8-flash-tts Flagship TTS model"


def browser_with(ui, opened):
    @contextlib.asynccontextmanager
    async def browser(profile, *, headless):
        opened.append(headless)
        yield ui

    return browser


def serve_catalog(captured):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )


def speech_ui(fake_ui, captured, status=200):
    record = captured("GenerateContent", 3).raw
    refused = '[,[7,"The caller does not have permission"]]'
    body = record["response"]["body"] if status == 200 else refused
    return fake_ui(
        labels={("button", MODEL_BUTTON.pattern): [[CURRENT_MODEL]]},
        response=CapturedResponse(status, body, record["request"]["body"]),
    )


def test_default_output_name():
    assert str(default_output(datetime(2026, 10, 6, 14, 5, 9))) == (
        "aistudio-speech-20261006-140509.wav"
    )


@respx.mock
async def test_generate_speech_writes_a_wav(logged_in_profile, make_client, fake_ui, captured,
                                            tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    ui, opened = speech_ui(fake_ui, captured), []
    out = tmp_path / "hello.wav"
    result = await generate_speech(profile, "Hello <laugh>", style="warm", out=out,
                                   client_factory=lambda p: make_client(),
                                   browser=browser_with(ui, opened))
    assert opened == [False]
    assert result == {
        "files": [{"path": str(out), "mime_type": "audio/wav", "seconds": 10.56}],
        "model": "gemini-3.8-flash-tts", "voice": "Fola", "style": "warm",
    }
    with wave.open(io.BytesIO(out.read_bytes())) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 253440)
    assert ("fill", "textbox", "Describe the voice style", "warm") in ui.ops


@respx.mock
async def test_refused_generation_writes_nothing(logged_in_profile, make_client, fake_ui,
                                                 captured, tmp_path):
    profile = logged_in_profile()
    serve_catalog(captured)
    out = tmp_path / "x.wav"
    with pytest.raises(BotGuardError) as info:
        await generate_speech(profile, "Hello", out=out, client_factory=lambda p: make_client(),
                              browser=browser_with(speech_ui(fake_ui, captured, 403), []))
    assert "The caller does not have permission" in info.value.message
    assert "--headless" in info.value.hint
    assert not out.exists()


@pytest.mark.parametrize("model,hint", [("gemini-3.5-flash", 'use "aistudio ask"'),
                                        ("lyria-3.5", 'use "aistudio generate music"')])
@respx.mock
async def test_non_tts_models_fail_before_any_browser_opens(logged_in_profile, make_client,
                                                           fake_ui, captured, tmp_path, model,
                                                           hint):
    profile = logged_in_profile()
    serve_catalog(captured)
    opened = []
    with pytest.raises(ValidationError) as info:
        await generate_speech(profile, "Hello", model=model, out=tmp_path / "x.wav",
                              client_factory=lambda p: make_client(),
                              browser=browser_with(fake_ui(), opened))
    assert info.value.hint == hint
    assert opened == []


async def test_existing_output_is_not_overwritten(logged_in_profile, fake_ui, tmp_path):
    profile = logged_in_profile()
    out = tmp_path / "taken.wav"
    out.write_bytes(b"keep me")
    with pytest.raises(ValidationError) as info:
        await generate_speech(profile, "Hello", out=out, browser=browser_with(fake_ui(), []))
    assert "--force" in info.value.hint
    assert out.read_bytes() == b"keep me"


async def test_empty_text_is_rejected(logged_in_profile, fake_ui, tmp_path):
    with pytest.raises(ValidationError):
        await generate_speech(logged_in_profile(), "  ", out=tmp_path / "x.wav",
                              browser=browser_with(fake_ui(), []))


async def test_list_voices_runs_headless_and_filters(logged_in_profile, fake_ui):
    ui = fake_ui(labels={("button", FAVORITE.pattern): [["Favorite Tova", "Favorite Fola"]],
                         ("button", VIEW_ALL.pattern): [[]]})
    opened = []
    result = await list_voices(logged_in_profile(), search="TO", browser=browser_with(ui, opened))
    assert opened == [True]
    assert result == {"voices": ["Tova"], "count": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_app_media.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._app.media'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_app/media.py`:

```python
"""Media generation use cases. Text-to-speech is page-driven (spec §5.4, §6.4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.models import load_catalog, normalize_model_id
from aistudio._browser.speech_page import SpeechPage
from aistudio._browser.ui import open_browser
from aistudio._media.wav import pcm_to_wav
from aistudio._rpc import wire
from aistudio._rpc.rows.speech import decode_speech
from aistudio._transport.http import error_for_response
from aistudio.client import AIStudioClient
from aistudio.exceptions import BotGuardError, NotFoundError, ValidationError
from aistudio.paths import Profile

DEFAULT_SPEECH_MODEL = "gemini-3.8-flash-tts"


def default_output(now: datetime) -> Path:
    return Path(f"aistudio-speech-{now:%Y%m%d-%H%M%S}.wav")


async def check_speech_model(client: AIStudioClient, profile: Profile, model: str) -> str:
    """The catalog must list the model as text-to-speech (spec §6.2 category validation)."""
    model_id = normalize_model_id(model)
    found = {m.id: m for m in await load_catalog(client, profile)}.get(model_id)
    if found is None:
        raise NotFoundError(f"no model {model_id!r} in the catalog",
                            hint='see "aistudio models list --category audio"')
    if found.command != "generate speech":
        hint = (f'use "aistudio {found.command}"' if found.command
                else 'see "aistudio models list --category audio"')
        raise ValidationError(f"{model_id} is not a text-to-speech model", hint=hint)
    return model_id


async def generate_speech(
    profile: Profile,
    text: str,
    *,
    model: str = DEFAULT_SPEECH_MODEL,
    voice: str | None = None,
    style: str | None = None,
    out: Path | None = None,
    overwrite: bool = False,
    headless: bool = False,
    client_factory: Callable[[Profile], AIStudioClient] = AIStudioClient.from_profile,
    browser: Callable[..., Any] = open_browser,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, Any]:
    if not text.strip():
        raise ValidationError("nothing to speak: the text is empty")
    path = out or default_output(now())
    if path.exists() and not overwrite:
        raise ValidationError(f"{path} already exists", hint="pass --force or choose another -o")
    async with client_factory(profile) as client:
        model_id = await check_speech_model(client, profile, model)
    async with browser(profile, headless=headless) as ui:
        page = SpeechPage(ui)
        await page.open()
        await page.select_model(model_id)
        chosen_voice = await page.select_voice(voice) if voice else None
        if style:
            await page.set_style(style)
        await page.set_text(text)
        captured = await page.run()
    if captured.status >= 400:
        err = error_for_response(captured.status, captured.text)
        if captured.status == 403:
            raise BotGuardError(
                f"Google refused the speech request: {err.message}",
                hint="generation needs a visible browser window (do not pass --headless); "
                     "a paid model may also need a paid tier",
            )
        raise err
    audio = decode_speech(wire.decode(captured.text))
    sent = wire.decode(captured.request_body) if captured.request_body else None
    path.write_bytes(pcm_to_wav(audio.pcm, rate=audio.rate, channels=audio.channels))
    return {
        "files": [{"path": str(path), "mime_type": "audio/wav",
                   "seconds": round(audio.seconds, 2)}],
        "model": model_id,
        "voice": wire.opt_index(sent, 3, 15, 0, 0, 0) or chosen_voice,
        "style": style,
    }


async def list_voices(
    profile: Profile, *, search: str | None = None, browser: Callable[..., Any] = open_browser
) -> dict[str, Any]:
    """Read the speech page's voice panel; reading works headless (no generation)."""
    async with browser(profile, headless=True) as ui:
        page = SpeechPage(ui)
        await page.open()
        voices = await page.list_voices()
    if search:
        needle = search.lower()
        voices = [v for v in voices if needle in v.lower()]
    return {"voices": voices, "count": len(voices)}
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_app/media.py tests/unit/test_app_media.py
git commit -m "feat: generate speech and list voices use cases"
```

---

### Task 5: CLI `generate speech` and `voices list`

**Files:**
- Create: `src/aistudio/cli/generate_cmd.py`
- Modify: `src/aistudio/cli/state.py` (add `require_browser`), `src/aistudio/cli/main.py` (register `generate` and `voices`, keeping the import names sorted)
- Test: `tests/cli/test_generate_cmd.py`

**Interfaces:**
- Consumes: `generate_speech`, `list_voices` and `DEFAULT_SPEECH_MODEL` (Task 4); the CLI helpers (M0–M2)
- Produces: the click groups `generate_cmd.generate` (with `speech`) and `generate_cmd.voices` (with `list`), and `CliState.require_browser()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_generate_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def fake(result, calls):
    async def run(profile, *args, **kwargs):
        calls.append((profile.name, args, kwargs))
        return result

    return run


def test_generate_speech_passes_options(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    result = {"files": [{"path": "a.wav", "mime_type": "audio/wav", "seconds": 1.5}],
              "model": "gemini-3.8-flash-tts", "voice": "Tova", "style": "warm"}
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_speech", fake(result, calls))
    out = tmp_path / "a.wav"
    res = CliRunner().invoke(cli, ["generate", "speech", "Hi <laugh>", "--voice", "tova",
                                   "--style", "warm", "-o", str(out), "--force", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == result
    (name, args, kwargs) = calls[0]
    assert (name, args) == ("default", ("Hi <laugh>",))
    assert kwargs == {"model": "gemini-3.8-flash-tts", "voice": "tova", "style": "warm",
                      "out": out, "overwrite": True, "headless": False}


def test_generate_speech_reads_a_text_file(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.generate_speech",
                        fake({"files": [{"path": "a.wav", "mime_type": "audio/wav",
                                         "seconds": 1.0}], "model": "m", "voice": None,
                              "style": None}, calls))
    script = tmp_path / "script.txt"
    script.write_text("Line one. <gasp> Line two.")
    res = CliRunner().invoke(cli, ["generate", "speech", "--text-file", str(script)])
    assert res.exit_code == 0, res.output
    assert calls[0][1] == ("Line one. <gasp> Line two.",)
    assert "Wrote a.wav" in res.stdout


def test_generate_speech_needs_exactly_one_text_source(logged_in_profile, tmp_path):
    logged_in_profile()
    script = tmp_path / "s.txt"
    script.write_text("x")
    for args in (["generate", "speech"],
                 ["generate", "speech", "a", "--text-file", str(script)]):
        res = CliRunner().invoke(cli, [*args, "--json"])
        assert res.exit_code == 1
        assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_http_backend_is_refused_for_generation(logged_in_profile):
    logged_in_profile()
    res = CliRunner().invoke(cli, ["--backend", "http", "generate", "speech", "hi", "--json"])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["code"] == "VALIDATION_ERROR"


def test_voices_list(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []
    monkeypatch.setattr("aistudio.cli.generate_cmd.list_voices",
                        fake({"voices": ["Fola", "Tova"], "count": 2}, calls))
    res = CliRunner().invoke(cli, ["voices", "list", "--search", "o"])
    assert res.exit_code == 0, res.output
    assert "Fola, Tova" in res.stdout
    assert calls[0][2] == {"search": "o"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_generate_cmd.py -v`
Expected: FAIL with `No such command 'generate'`

- [ ] **Step 3: Write the implementation**

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

Create `src/aistudio/cli/generate_cmd.py`:

```python
"""``aistudio generate speech`` and ``aistudio voices list`` (spec §6.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.media import DEFAULT_SPEECH_MODEL, generate_speech, list_voices
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState
from aistudio.exceptions import ValidationError


@click.group()
def generate() -> None:
    """Generate media with AI Studio."""


def _render_speech(data: dict[str, Any]) -> None:
    file = data["files"][0]
    voice = f", voice {data['voice']}" if data["voice"] else ""
    stdout.print(f"Wrote {file['path']} ({file['seconds']} s{voice})")


@generate.command("speech")
@click.argument("text", required=False)
@click.option("--text-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the text from a file instead.")
@click.option("-m", "--model", default=DEFAULT_SPEECH_MODEL, show_default=True,
              help="A text-to-speech model id.")
@click.option("--voice", help='Voice name, e.g. Fola or Tova (see "aistudio voices list").')
@click.option("--style", help='How to speak, e.g. "warm, slow and smiling".')
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path),
              help="Output .wav (default: aistudio-speech-<time>.wav).")
@click.option("--force", is_flag=True, help="Overwrite the output file if it exists.")
@click.option("--headless", is_flag=True,
              help="Hide the browser window (Google currently refuses headless generation).")
@json_option
@click.pass_obj
@handle_errors
def speech(state: CliState, text: str | None, text_file: Path | None, model: str,
           voice: str | None, style: str | None, out: Path | None, force: bool,
           headless: bool, json_output: bool) -> None:
    """Turn TEXT into speech. Put expression tags such as <laugh> inside the text.

    Opens a Chrome window on AI Studio while it works.
    """
    state.require_browser()
    if (text is None) == (text_file is None):
        raise ValidationError("give either TEXT or --text-file", hint="not both, not neither")
    content = text if text_file is None else text_file.read_text(encoding="utf-8")
    profile = state.profile()
    if not json_output:
        stderr.print("Opening AI Studio in a Chrome window to generate speech…")
    result = asyncio.run(generate_speech(
        profile, content, model=model, voice=voice, style=style, out=out, overwrite=force,
        headless=headless,
    ))
    emit(result, json_output=json_output, render=_render_speech)


@click.group()
def voices() -> None:
    """Voices available for text-to-speech."""


@voices.command("list")
@click.option("--search", help="Only voices whose name contains this text.")
@json_option
@click.pass_obj
@handle_errors
def voices_list(state: CliState, search: str | None, json_output: bool) -> None:
    """List the speech page's voices (reads the page; generates nothing)."""
    state.require_browser()
    result = asyncio.run(list_voices(state.profile(), search=search))
    emit(result, json_output=json_output,
         render=lambda d: stdout.print(", ".join(d["voices"]) or "No voices found"))
```

In `src/aistudio/cli/main.py`, add `generate_cmd` to the `from aistudio.cli import …` line (sorted)
and add these after the other `add_command` calls:

```python
cli.add_command(generate_cmd.generate)
cli.add_command(generate_cmd.voices)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/cli tests/cli/test_generate_cmd.py
git commit -m "feat: add generate speech and voices list commands"
```

---

### Task 6: Live checks and README

**Files:**
- Create: `tests/live/test_live_speech.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `generate_speech`, `list_voices` (Task 4)
- Produces: opt-in live checks. `voices list` is marked `live` (headless, free). Speech generation is marked `live_slow`: it opens a window and spends one short TTS generation.

- [ ] **Step 1: Write the live tests**

Create `tests/live/test_live_speech.py`:

```python
"""Opt-in checks against the real speech page.

`uv run pytest -m live` lists voices headless (free).
`uv run pytest -m live_slow` also generates one short phrase in a visible window (uses quota).
"""

import io
import os
import wave

import pytest

from aistudio._app.media import generate_speech, list_voices
from aistudio.paths import get_profile, resolve_profile_name


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.chrome_dir.exists():
        pytest.skip('no browser session; run "aistudio login"')
    return p


@pytest.mark.live
async def test_live_voices_include_the_default(profile):
    result = await list_voices(profile)
    assert "Fola" in result["voices"]
    assert result["count"] >= 20


@pytest.mark.live_slow
async def test_live_generate_speech_with_voice_and_style(profile, tmp_path):
    out = tmp_path / "live.wav"
    result = await generate_speech(profile, "Hello from the live test. <laugh>", voice="tova",
                                   style="warm and slow", out=out)
    assert result["voice"] == "Tova"
    with wave.open(io.BytesIO(out.read_bytes())) as w:
        assert w.getframerate() == 24000
        assert w.getnframes() / w.getframerate() > 0.5
```

- [ ] **Step 2: Run them**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: the default suite passes, and the live tests are deselected.

Run: `uv run pytest -m live tests/live/test_live_speech.py -v 2>&1 | tail -3`
Expected: `1 passed`. It is headless; no window appears and no quota is spent.

Run: `uv run pytest -m live_slow tests/live/test_live_speech.py -v 2>&1 | tail -3`
Expected: `1 passed`. A Chrome window opens for about 30 seconds and one short generation is spent.
If it fails on a control that can't be found, re-run `scripts/spikes/tts_controls.py` to re-map
the page.

- [ ] **Step 3: Update the README quick start**

In `README.md`, add these lines to the Quick start block, before the `dev capture` line:

```bash
uv run aistudio voices list
uv run aistudio generate speech "Hello! <laugh> Nice to meet you." --voice Tova --style "warm, slow" -o hello.wav
```

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_live_speech.py README.md
git commit -m "test: live speech checks; document generate speech"
```
