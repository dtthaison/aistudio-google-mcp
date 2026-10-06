# aistudio-py Foundation + Discovery (M0–M1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the parts of aistudio-py that do not depend on AI Studio's undocumented request formats, then use them to capture those formats from a real session and decide the M1 gate.

**Architecture:**

- **Foundation:**
  - Profiles and owner-only credential storage.
  - Playwright login that saves cookies and the web API key.
  - A `SAPISIDHASH` request signer.
  - A tolerant `json+protobuf` codec.
  - A plain-HTTP transport.
  - A click CLI with the `--json`, error-envelope and exit-code conventions.
- **Discovery tools:**
  - `dev capture` records the browser's MakerSuite traffic and redacts it in memory before writing fixtures.
  - `dev replay` re-sends fixtures over plain HTTP to learn which RPCs need BotGuard.
- **The plan ends with a capture session run together with the user.** It produces the fixtures and
  `docs/wire/findings.md`. The M2–M5 plan is written from those.

**Tech Stack:** Python ≥3.10, uv, click ≥8.2, httpx, rich, filelock, Playwright (the `[browser]` extra), pytest, pytest-asyncio, respx, ruff.

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`

**Scope:**

- This plan implements spec §3 (layering), §4.1–4.3, §5.2 (without retry or refresh middleware),
  §6.1 (`login`, `auth check/logout`, `profile *`), §6.8, §8 (capture and HTTP replay), §9
  (exception hierarchy and status mapping) and milestones M0–M1.
- Deferred to the M2–M5 plan, which is written from the M1 fixtures:
  - `--browser-cookies` login
  - `auth check --test`/`--browser` probes
  - the keepalive and refresh chain (§4.4)
  - retry middleware
  - the browser daemon and backend
  - BotGuard strategies
  - every model, prompt, chat, media and MCP feature

## Global Constraints

- `requires-python = ">=3.10"`. The project is uv-managed with a `src/` layout. The distribution is `aistudio-py`, the import name `aistudio`, and the command `aistudio`.
- Core dependencies are only `click`, `httpx`, `rich` and `filelock`. Playwright comes only through the `[browser]` extra and is imported lazily inside functions.
- The home directory is `$AISTUDIO_HOME`, defaulting to `~/.aistudio`. The profile comes from `--profile`, then `$AISTUDIO_PROFILE`, then `config.json`'s `default_profile`, then `"default"`.
- Credential files (`storage_state.json`, `session.json`, `config.json`) are written atomically with mode `0600` under a sibling filelock.
- Every command accepts `--json`. Errors are printed on stdout as `{"error": true, "code", "message", "hint"?}`.
- Exit codes:
  - `0`: success
  - `1`: handled error
  - `2`: unexpected error or `TIMEOUT`
  - `130`: interrupt
- Cookies, `Authorization` values and the API key are never logged or printed.
- Capture redacts in memory before anything is written to disk.
- Credentials are only ever sent over `https` to hosts ending in `.google.com` or `.googleapis.com`.
- Fixed endpoint values:
  - Origin: `https://aistudio.google.com`.
  - API host: `alkalimakersuite-pa.clients6.google.com`.
  - Service path: `$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/<Method>`.
- Browser launches use system Chrome by default (`channel="chrome"`), pass
  `--disable-blink-features=AutomationControlled`, and drop `--enable-automation`.
- Ruff runs with line length 100 and targets `py310`.

## Review Focus

1. **Replaying mutating or paid RPCs.** If `dev replay tests/fixtures` runs on a directory that holds
   `DeletePrompt`, `GenerateContent` or music fixtures, those requests must not be sent unless
   `--allow-mutating` is passed. Test in Task 11.
2. **Credentials in unexpected places.** A header with unusual casing, a `?key=AIza…` in a URL, or an
   email inside response JSON must never reach a fixture file. Test in Task 9.
3. **System Chrome not installed.** `login` and `capture` must exit 1 with `BROWSER_ERROR` and a hint
   naming `--browser chromium`, not a Playwright traceback. Test in Task 8.
4. **Corrupt or half-written `storage_state.json`.** `auth check` must report `status: error` with
   the login hint, and other commands must fail with `AUTH_ERROR` instead of a traceback. Tests in Tasks 3 and 7.
5. **Commas, brackets and escaped quotes inside JSPB strings.** These must decode unchanged
   (`"a,,b"`, `"[,"`, `"q\","`). Test in Task 5.

---

## File Structure

```
pyproject.toml, .gitignore, README.md
src/aistudio/
  __init__.py              version
  exceptions.py            error hierarchy + gRPC/HTTP status mapping
  _fs.py                   atomic 0600 JSON writes under filelock
  paths.py                 home/profile resolution, Profile paths, config.json
  _auth/storage.py         storage_state + session.json persistence, cookie checks
  _auth/session.py         runtime Session: cookies for a host, api key, authuser
  _auth/signer.py          SAPISIDHASH headers
  _auth/login.py           Playwright login, shared launch helper
  _rpc/wire.py             json+protobuf encode/decode, error envelope, safe_index
  _transport/http.py       HttpTransport (sign, post, decode, map errors)
  _app/auth.py             auth status use case
  _app/profiles.py         profile use cases
  _dev/fixtures.py         fixture naming, writing, loading
  _dev/redact.py           RawExchange + Redactor
  _dev/capture.py          CaptureRecorder + run_capture
  _dev/replay.py           replay_fixture / replay_all
  cli/state.py             CliState (global options)
  cli/output.py            --json option, emit, confirm
  cli/error_handler.py     handle_errors decorator
  cli/main.py              root group
  cli/session_cmd.py       login, auth check/logout
  cli/profile_cmd.py       profile list/create/switch/delete
  cli/dev_cmd.py           dev capture/replay
scripts/spikes/route_rewrite.py   throwaway H4 spike
docs/wire/findings.md             M1 results
tests/conftest.py, tests/unit/*, tests/cli/*
```

---

### Task 1: Project scaffold and exception hierarchy

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/aistudio/__init__.py`, `src/aistudio/exceptions.py`
- Test: `tests/unit/test_exceptions.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `AIStudioError(message, *, hint=None, retriable=None)` with `.code`, `.message`, `.hint`, `.retriable` and `.to_dict() -> dict`.
  - Subclasses, with their `code` values:

    | Class | `code` |
    |---|---|
    | `AuthError` | `AUTH_ERROR` |
    | `RateLimitError` | `RATE_LIMITED` |
    | `QuotaExceededError` | `QUOTA_EXCEEDED` |
    | `BotGuardError` | `BOTGUARD_REJECTED` |
    | `SchemaDriftError(method, index_path, expected)` | `SCHEMA_DRIFT` |
    | `NotFoundError` | `NOT_FOUND` |
    | `ValidationError` | `VALIDATION_ERROR` |
    | `NetworkError` | `NETWORK_ERROR` |
    | `ServerError` | `SERVER_ERROR` |
    | `BrowserError` | `BROWSER_ERROR` |
    | `WaitTimeoutError` | `TIMEOUT` |

  - `error_from_status(status: int, message: str) -> AIStudioError`
  - `error_from_http(status: int, body_text: str) -> AIStudioError`

- [ ] **Step 1: Install uv and create the project files**

Run: `command -v uv || brew install uv`

Create `pyproject.toml`:

```toml
[project]
name = "aistudio-py"
version = "0.1.0"
description = "Unofficial CLI and MCP server that automates the Google AI Studio web app"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
  "click>=8.2",
  "httpx>=0.27",
  "rich>=13",
  "filelock>=3.13",
]

[project.optional-dependencies]
browser = ["playwright>=1.45"]
dev = [
  "playwright>=1.45",
  "pytest>=8",
  "pytest-asyncio>=0.24",
  "respx>=0.21",
  "ruff>=0.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aistudio"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-m 'not live and not live_slow' --import-mode=importlib"
markers = [
  "live: hits the real AI Studio service (opt-in)",
  "live_slow: slow live tests such as video and music (opt-in)",
]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

Create `.gitignore`:

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
.ruff_cache/
dist/
build/
```

Create `README.md`:

````markdown
# aistudio-py

Unofficial CLI and MCP server that automates the Google AI Studio web app
(https://aistudio.google.com) with your own Google session.

> **Warning:** this project uses undocumented internal endpoints of AI Studio. It can break
> whenever Google ships a change, and automating a personal Google session may violate
> Google's Terms of Service. Use a dedicated Google account, and use it at your own risk.

## Install (development)

```bash
brew install uv
uv sync --extra dev
```

## Quick start

```bash
uv run aistudio login          # sign in once in a browser window
uv run aistudio auth check     # verify the stored session
uv run aistudio dev capture    # record AI Studio traffic into tests/fixtures
```

Design: `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`
````

Create `src/aistudio/__init__.py`:

```python
"""Unofficial CLI and MCP server for the Google AI Studio web app."""

__version__ = "0.1.0"
```

Run: `uv sync --extra dev`
Expected: creates `.venv/` and installs `aistudio-py` in editable mode.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_exceptions.py`:

```python
import pytest

from aistudio.exceptions import (
    AuthError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    SchemaDriftError,
    ServerError,
    ValidationError,
    error_from_http,
    error_from_status,
)


@pytest.mark.parametrize(
    "status,message,cls,retriable",
    [
        (3, "bad arg", ValidationError, False),
        (5, "no such prompt", NotFoundError, False),
        (8, "Resource has been exhausted", RateLimitError, True),
        (8, "Quota exceeded for model", QuotaExceededError, False),
        (7, "The caller does not have permission", AuthError, False),
        (7, "Quota exceeded", QuotaExceededError, False),
        (16, "unauthenticated", AuthError, False),
        (13, "Internal error encountered.", ServerError, True),
        (14, "unavailable", ServerError, True),
        (99, "weird", ServerError, False),
    ],
)
def test_error_from_status(status, message, cls, retriable):
    err = error_from_status(status, message)
    assert type(err) is cls
    assert err.retriable is retriable


def test_auth_errors_carry_login_hint():
    assert error_from_status(16, "x").hint == 'run "aistudio login"'
    assert error_from_http(401, "").hint == 'run "aistudio login"'


@pytest.mark.parametrize(
    "status,cls",
    [(401, AuthError), (403, AuthError), (404, NotFoundError), (429, RateLimitError),
     (503, ServerError), (400, ValidationError)],
)
def test_error_from_http(status, cls):
    assert type(error_from_http(status, "<html>")) is cls


def test_error_from_http_truncates_body():
    err = error_from_http(500, "x" * 1000)
    assert len(err.message) < 250


def test_to_dict_envelope():
    err = NotFoundError("gone", hint="check the id")
    assert err.to_dict() == {
        "error": True, "code": "NOT_FOUND", "message": "gone", "hint": "check the id",
    }


def test_to_dict_omits_missing_hint():
    assert "hint" not in ValidationError("bad").to_dict()


def test_schema_drift_names_method_and_path():
    err = SchemaDriftError("ListPrompts", (0, 2, 1), "a string")
    assert err.code == "SCHEMA_DRIFT"
    assert "ListPrompts" in err.message
    assert "[0][2][1]" in err.message
    assert err.hint == 're-capture ListPrompts with "aistudio dev capture"'
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_exceptions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio.exceptions'`

- [ ] **Step 4: Write the implementation**

Create `src/aistudio/exceptions.py`:

```python
"""Exception hierarchy shared by the client, CLI and MCP layers.

Every error carries a stable ``code`` (used in ``--json`` envelopes and MCP tool
errors), an optional ``hint`` telling the user what to do next, and a
``retriable`` flag for the retry middleware.
"""

from __future__ import annotations

_LOGIN_HINT = 'run "aistudio login"'


class AIStudioError(Exception):
    code = "ERROR"
    default_retriable = False

    def __init__(
        self, message: str, *, hint: str | None = None, retriable: bool | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retriable = self.default_retriable if retriable is None else retriable

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"error": True, "code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data


class AuthError(AIStudioError):
    code = "AUTH_ERROR"


class RateLimitError(AIStudioError):
    code = "RATE_LIMITED"
    default_retriable = True


class QuotaExceededError(AIStudioError):
    code = "QUOTA_EXCEEDED"


class BotGuardError(AIStudioError):
    code = "BOTGUARD_REJECTED"


class SchemaDriftError(AIStudioError):
    code = "SCHEMA_DRIFT"

    def __init__(self, method: str, index_path: tuple[int, ...], expected: str) -> None:
        path = "".join(f"[{i}]" for i in index_path) or "root"
        super().__init__(
            f"{method}: expected {expected} at {path}; the response shape changed",
            hint=f're-capture {method} with "aistudio dev capture"',
        )
        self.method = method
        self.index_path = index_path
        self.expected = expected


class NotFoundError(AIStudioError):
    code = "NOT_FOUND"


class ValidationError(AIStudioError):
    code = "VALIDATION_ERROR"


class NetworkError(AIStudioError):
    code = "NETWORK_ERROR"
    default_retriable = True


class ServerError(AIStudioError):
    code = "SERVER_ERROR"


class BrowserError(AIStudioError):
    code = "BROWSER_ERROR"


class WaitTimeoutError(AIStudioError):
    code = "TIMEOUT"


def error_from_status(status: int, message: str) -> AIStudioError:
    """Map a gRPC status from an error envelope to an exception (spec §9)."""
    quota = "quota" in message.lower()
    if status == 3:
        return ValidationError(message)
    if status == 5:
        return NotFoundError(message)
    if status == 8:
        return QuotaExceededError(message) if quota else RateLimitError(message)
    if status in (7, 16):
        return QuotaExceededError(message) if quota else AuthError(message, hint=_LOGIN_HINT)
    if status in (13, 14):
        return ServerError(message, retriable=True)
    return ServerError(f"gRPC status {status}: {message}")


def error_from_http(status: int, body_text: str) -> AIStudioError:
    """Fallback mapping for a non-2xx response without a decodable error envelope."""
    snippet = body_text.strip()[:200]
    message = f"HTTP {status}" + (f": {snippet}" if snippet else "")
    if status in (401, 403):
        return AuthError(message, hint=_LOGIN_HINT)
    if status == 404:
        return NotFoundError(message)
    if status == 429:
        return RateLimitError(message)
    if status >= 500:
        return ServerError(message, retriable=True)
    return ValidationError(message)
```

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest tests/unit/test_exceptions.py -v && uv run ruff check .`
Expected: all tests PASS; ruff prints `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore README.md src tests
git commit -m "feat: scaffold aistudio-py and exception hierarchy"
```

---

### Task 2: Paths, profiles and private JSON files

**Files:**
- Create: `src/aistudio/_fs.py`, `src/aistudio/paths.py`, `tests/conftest.py`
- Test: `tests/unit/test_paths.py`

**Interfaces:**
- Consumes: `ValidationError` (Task 1)
- Produces:
  - `_fs.write_private_json(path: Path, data: Any) -> None`, `_fs.read_json(path: Path) -> Any` and `_fs.lock_for(path) -> FileLock`.
  - `paths.home_dir() -> Path`, `resolve_profile_name(cli_value: str | None = None) -> str` and `get_profile(name) -> Profile`.
  - `list_profiles() -> list[str]`, `set_default_profile(name)`, `clear_default_profile(name)`, `validate_profile_name(name) -> str` and `load_config() -> dict`.
  - `Profile(name, root)`: properties `storage_state`, `session`, `context`, `jobs`, `models`, `browser_json`, `chrome_dir`, plus `ensure() -> Profile`.
  - The pytest fixture `aistudio_home` (a temporary `AISTUDIO_HOME`).

- [ ] **Step 1: Write the failing test**

Create `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def aistudio_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AISTUDIO_HOME", str(home))
    monkeypatch.delenv("AISTUDIO_PROFILE", raising=False)
    return home
```

Create `tests/unit/test_paths.py`:

```python
import json

import pytest

from aistudio import paths
from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import ValidationError


def test_home_dir_uses_env(aistudio_home):
    assert paths.home_dir() == aistudio_home


def test_home_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AISTUDIO_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    assert paths.home_dir() == tmp_path / ".aistudio"


def test_profile_precedence(aistudio_home, monkeypatch):
    assert paths.resolve_profile_name() == "default"
    paths.set_default_profile("fromconfig")
    assert paths.resolve_profile_name() == "fromconfig"
    monkeypatch.setenv("AISTUDIO_PROFILE", "fromenv")
    assert paths.resolve_profile_name() == "fromenv"
    assert paths.resolve_profile_name("fromcli") == "fromcli"


@pytest.mark.parametrize("name", ["../x", "a/b", "", ".hidden", "x" * 65, "a..b", "sp ace"])
def test_rejects_unsafe_profile_names(aistudio_home, name):
    with pytest.raises(ValidationError):
        paths.get_profile(name)


def test_profile_paths(aistudio_home):
    p = paths.get_profile("work")
    assert p.root == aistudio_home / "profiles" / "work"
    assert p.storage_state == p.root / "storage_state.json"
    assert p.session == p.root / "session.json"
    assert p.context == p.root / "context.json"
    assert p.jobs == p.root / "jobs.json"
    assert p.models == p.root / "models.json"
    assert p.browser_json == p.root / "browser.json"
    assert p.chrome_dir == p.root / "chrome"
    assert not p.root.exists()
    assert p.ensure().root.is_dir()


def test_list_profiles(aistudio_home):
    assert paths.list_profiles() == []
    paths.get_profile("b").ensure()
    paths.get_profile("a").ensure()
    assert paths.list_profiles() == ["a", "b"]


def test_clear_default_profile(aistudio_home):
    paths.set_default_profile("work")
    paths.clear_default_profile("other")
    assert paths.load_config()["default_profile"] == "work"
    paths.clear_default_profile("work")
    assert "default_profile" not in paths.load_config()


def test_write_private_json_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "dir" / "secret.json"
    write_private_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(target.parent.glob("*.tmp"))
    write_private_json(target, {"a": 2})
    assert read_json(target) == {"a": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_paths.py -v`
Expected: FAIL with `ImportError: cannot import name 'paths' from 'aistudio'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_fs.py`:

```python
"""Atomic, owner-only JSON files guarded by a sibling lock file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock


def lock_for(path: Path) -> FileLock:
    return FileLock(str(path.with_name(f".{path.name}.lock")))


def write_private_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_for(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def read_json(path: Path) -> Any:
    with lock_for(path):
        return json.loads(path.read_text(encoding="utf-8"))
```

Create `src/aistudio/paths.py`:

```python
"""Locations of the AI Studio home directory and per-profile files (spec §4.1)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import ValidationError

DEFAULT_PROFILE = "default"
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def home_dir() -> Path:
    return Path(os.environ.get("AISTUDIO_HOME") or Path.home() / ".aistudio")


def config_path() -> Path:
    return home_dir() / "config.json"


def validate_profile_name(name: str) -> str:
    if not _PROFILE_NAME.match(name) or ".." in name:
        raise ValidationError(
            f"invalid profile name {name!r}", hint="use letters, digits, '.', '_' or '-'"
        )
    return name


def load_config() -> dict[str, Any]:
    path = config_path()
    return read_json(path) if path.exists() else {}


def set_default_profile(name: str) -> None:
    config = load_config()
    config["default_profile"] = validate_profile_name(name)
    write_private_json(config_path(), config)


def clear_default_profile(name: str) -> None:
    config = load_config()
    if config.get("default_profile") == name:
        del config["default_profile"]
        write_private_json(config_path(), config)


def resolve_profile_name(cli_value: str | None = None) -> str:
    name = (
        cli_value
        or os.environ.get("AISTUDIO_PROFILE")
        or load_config().get("default_profile")
        or DEFAULT_PROFILE
    )
    return validate_profile_name(name)


@dataclass(frozen=True)
class Profile:
    name: str
    root: Path

    @property
    def storage_state(self) -> Path:
        return self.root / "storage_state.json"

    @property
    def session(self) -> Path:
        return self.root / "session.json"

    @property
    def context(self) -> Path:
        return self.root / "context.json"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs.json"

    @property
    def models(self) -> Path:
        return self.root / "models.json"

    @property
    def browser_json(self) -> Path:
        return self.root / "browser.json"

    @property
    def chrome_dir(self) -> Path:
        return self.root / "chrome"

    def ensure(self) -> Profile:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self


def get_profile(name: str) -> Profile:
    return Profile(name=validate_profile_name(name), root=home_dir() / "profiles" / name)


def list_profiles() -> list[str]:
    base = home_dir() / "profiles"
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and _PROFILE_NAME.match(p.name))
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_paths.py -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_fs.py src/aistudio/paths.py tests/conftest.py tests/unit/test_paths.py
git commit -m "feat: add profile paths and atomic private JSON storage"
```

---

### Task 3: Credential storage and runtime session

**Files:**
- Create: `src/aistudio/_auth/__init__.py` (empty), `src/aistudio/_auth/storage.py`, `src/aistudio/_auth/session.py`
- Test: `tests/unit/test_storage.py`, `tests/unit/test_session.py`

**Interfaces:**
- Consumes: `Profile`, `get_profile` (Task 2); `write_private_json`, `read_json` (Task 2); `AuthError` (Task 1)
- Produces:
  - `storage.AccountInfo(email: str | None = None, authuser: int = 0)` and `storage.REQUIRED_COOKIES = ("SID", "SAPISID")`.
  - `filter_state(state) -> dict`, `save_state(profile, state, account) -> None` and `load_state(profile) -> dict`, which raises `AuthError`.
  - `account_info(state) -> AccountInfo`, `missing_cookies(state) -> list[str]`, `save_session_info(profile, api_key: str | None) -> None`, `load_api_key(profile) -> str | None` and `delete_credentials(profile) -> list[str]`.
  - `session.Session(cookies: list[dict], api_key: str | None = None, authuser: int = 0)` with `Session.load(profile)`, `.cookies_for(host, now=None) -> dict[str, str]` and `.cookie_header(host, now=None) -> str`.
  - `session.format_cookie_header(jar: dict[str, str]) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_storage.py`:

```python
import pytest

from aistudio._auth.storage import (
    AccountInfo,
    account_info,
    delete_credentials,
    load_api_key,
    load_state,
    missing_cookies,
    save_session_info,
    save_state,
)
from aistudio.exceptions import AuthError
from aistudio.paths import get_profile


def cookie(name, domain=".google.com", value="v"):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": -1}


STATE = {
    "cookies": [
        cookie("SID"),
        cookie("SAPISID"),
        cookie("PREF", domain=".youtube.com"),
        cookie("X", domain="notgoogle.com"),
        cookie("Y", domain="google.com.evil.io"),
    ],
    "origins": [
        {"origin": "https://aistudio.google.com", "localStorage": []},
        {"origin": "https://www.youtube.com", "localStorage": []},
    ],
}


def test_save_filters_foreign_domains_and_records_account(aistudio_home):
    profile = get_profile("default")
    save_state(profile, STATE, AccountInfo(email="me@example.com", authuser=2))
    state = load_state(profile)
    assert [c["name"] for c in state["cookies"]] == ["SID", "SAPISID"]
    assert [o["origin"] for o in state["origins"]] == ["https://aistudio.google.com"]
    assert account_info(state) == AccountInfo(email="me@example.com", authuser=2)


def test_saved_state_is_owner_only(aistudio_home):
    profile = get_profile("default")
    save_state(profile, STATE, AccountInfo())
    assert profile.storage_state.stat().st_mode & 0o777 == 0o600


def test_load_missing_state_raises_auth_error_with_hint(aistudio_home):
    with pytest.raises(AuthError) as info:
        load_state(get_profile("default"))
    assert info.value.hint == 'run "aistudio login"'


@pytest.mark.parametrize("content", ["{not json", "[]", '{"cookies": "nope"}', ""])
def test_load_corrupt_state_raises_auth_error(aistudio_home, content):
    profile = get_profile("default").ensure()
    profile.storage_state.write_text(content)
    with pytest.raises(AuthError):
        load_state(profile)


def test_missing_cookies():
    assert missing_cookies(STATE) == []
    assert missing_cookies({"cookies": [cookie("SID")]}) == ["SAPISID"]
    assert missing_cookies({"cookies": [cookie("SAPISID", domain=".youtube.com")]}) == [
        "SID", "SAPISID",
    ]


def test_session_info_roundtrip(aistudio_home):
    profile = get_profile("default")
    assert load_api_key(profile) is None
    save_session_info(profile, "AIza-test")
    assert load_api_key(profile) == "AIza-test"
    assert profile.session.stat().st_mode & 0o777 == 0o600


def test_corrupt_session_info_reads_as_no_key(aistudio_home):
    profile = get_profile("default").ensure()
    profile.session.write_text("garbage")
    assert load_api_key(profile) is None


def test_delete_credentials(aistudio_home):
    profile = get_profile("default")
    save_state(profile, STATE, AccountInfo())
    save_session_info(profile, "k")
    profile.chrome_dir.mkdir()
    assert delete_credentials(profile) == ["storage_state.json", "session.json", "chrome/"]
    assert not profile.storage_state.exists()
    assert not profile.session.exists()
    assert not profile.chrome_dir.exists()
    assert delete_credentials(profile) == []
```

Create `tests/unit/test_session.py`:

```python
from aistudio._auth.session import Session, format_cookie_header
from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio.paths import get_profile

HOST = "alkalimakersuite-pa.clients6.google.com"


def cookie(name, value="v", domain=".google.com", expires=-1):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": expires}


def test_cookies_for_matches_parent_domains_only():
    s = Session(cookies=[
        cookie("SID", "a"),
        cookie("AISTUDIO", "b", domain="aistudio.google.com"),
        cookie("YT", "c", domain=".youtube.com"),
    ])
    assert s.cookies_for(HOST) == {"SID": "a"}
    assert s.cookies_for("aistudio.google.com") == {"SID": "a", "AISTUDIO": "b"}


def test_cookies_for_skips_expired():
    s = Session(cookies=[cookie("OLD", expires=100.0), cookie("NEW", expires=10_000.0)])
    assert s.cookies_for(HOST, now=5_000.0) == {"NEW": "v"}


def test_more_specific_domain_wins():
    s = Session(cookies=[cookie("NID", "b", domain="clients6.google.com"), cookie("NID", "a")])
    assert s.cookies_for(HOST) == {"NID": "b"}


def test_cookie_header_format():
    assert format_cookie_header({"A": "1", "B": "2"}) == "A=1; B=2"
    assert Session(cookies=[cookie("A", "1")]).cookie_header(HOST) == "A=1"


def test_session_load(aistudio_home):
    profile = get_profile("default")
    save_state(profile, {"cookies": [cookie("SID")], "origins": []}, AccountInfo(authuser=3))
    save_session_info(profile, "AIza-test")
    s = Session.load(profile)
    assert s.api_key == "AIza-test"
    assert s.authuser == 3
    assert s.cookies_for(HOST) == {"SID": "v"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_storage.py tests/unit/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._auth'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_auth/__init__.py` as an empty file.

Create `src/aistudio/_auth/storage.py`:

```python
"""Persisted Google session: Playwright ``storage_state`` plus account metadata (spec §4)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import AuthError
from aistudio.paths import Profile

REQUIRED_COOKIES = ("SID", "SAPISID")
_ALLOWED_DOMAIN = re.compile(r"(^|\.)(google\.com|googleapis\.com)$")
_LOGIN_HINT = 'run "aistudio login"'


@dataclass(frozen=True)
class AccountInfo:
    email: str | None = None
    authuser: int = 0


def _domain_allowed(domain: str) -> bool:
    return bool(_ALLOWED_DOMAIN.search(domain.lstrip(".").lower()))


def filter_state(state: dict[str, Any]) -> dict[str, Any]:
    cookies = [c for c in state.get("cookies", []) if _domain_allowed(c.get("domain", ""))]
    origins = [
        o for o in state.get("origins", [])
        if _domain_allowed(urlparse(o.get("origin", "")).hostname or "")
    ]
    return {"cookies": cookies, "origins": origins}


def save_state(profile: Profile, state: dict[str, Any], account: AccountInfo) -> None:
    data = filter_state(state)
    data["aistudio"] = {"email": account.email, "authuser": account.authuser}
    profile.ensure()
    write_private_json(profile.storage_state, data)


def load_state(profile: Profile) -> dict[str, Any]:
    path = profile.storage_state
    if not path.exists():
        raise AuthError(f"profile {profile.name!r} is not logged in", hint=_LOGIN_HINT)
    try:
        data = read_json(path)
    except (ValueError, OSError) as exc:
        raise AuthError(f"cannot read {path}: {exc}", hint=_LOGIN_HINT) from exc
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise AuthError(f"{path} is not a valid storage state", hint=_LOGIN_HINT)
    return data


def account_info(state: dict[str, Any]) -> AccountInfo:
    meta = state.get("aistudio") or {}
    return AccountInfo(email=meta.get("email"), authuser=int(meta.get("authuser") or 0))


def missing_cookies(state: dict[str, Any]) -> list[str]:
    names = {
        c.get("name") for c in state.get("cookies", []) if _domain_allowed(c.get("domain", ""))
    }
    return [name for name in REQUIRED_COOKIES if name not in names]


def save_session_info(profile: Profile, api_key: str | None) -> None:
    profile.ensure()
    write_private_json(
        profile.session,
        {"api_key": api_key, "scraped_at": datetime.now(timezone.utc).isoformat()},
    )


def load_api_key(profile: Profile) -> str | None:
    if not profile.session.exists():
        return None
    try:
        data = read_json(profile.session)
    except (ValueError, OSError):
        return None
    return data.get("api_key") if isinstance(data, dict) else None


def delete_credentials(profile: Profile) -> list[str]:
    removed: list[str] = []
    for path in (profile.storage_state, profile.session):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    if profile.chrome_dir.exists():
        shutil.rmtree(profile.chrome_dir)
        removed.append(f"{profile.chrome_dir.name}/")
    return removed
```

Create `src/aistudio/_auth/session.py`:

```python
"""Runtime credentials for signing requests, built from a profile's stored state."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from aistudio._auth.storage import account_info, load_api_key, load_state
from aistudio.paths import Profile


def _domain_matches(host: str, domain: str) -> bool:
    d = domain.lstrip(".").lower()
    host = host.lower()
    return host == d or host.endswith("." + d)


def format_cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


@dataclass
class Session:
    cookies: list[dict[str, Any]]
    api_key: str | None = None
    authuser: int = 0

    @classmethod
    def load(cls, profile: Profile) -> Session:
        state = load_state(profile)
        return cls(
            cookies=list(state["cookies"]),
            api_key=load_api_key(profile),
            authuser=account_info(state).authuser,
        )

    def cookies_for(self, host: str, now: float | None = None) -> dict[str, str]:
        now = time.time() if now is None else now
        jar: dict[str, str] = {}
        # Shorter domains first so the most specific matching domain wins on name clashes.
        for c in sorted(self.cookies, key=lambda c: len(c.get("domain", "").lstrip("."))):
            expires = c.get("expires", -1)
            if expires not in (-1, None) and expires < now:
                continue
            if _domain_matches(host, c.get("domain", "")):
                jar[c["name"]] = c["value"]
        return jar

    def cookie_header(self, host: str, now: float | None = None) -> str:
        return format_cookie_header(self.cookies_for(host, now))
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_storage.py tests/unit/test_session.py -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_auth tests/unit/test_storage.py tests/unit/test_session.py
git commit -m "feat: persist Google session state and build per-host cookie jars"
```

---

### Task 4: SAPISIDHASH request signer

**Files:**
- Create: `src/aistudio/_auth/signer.py`
- Test: `tests/unit/test_signer.py`

**Interfaces:**
- Consumes: `Session`, `format_cookie_header` (Task 3); `AuthError` (Task 1)
- Produces:
  - Constants: `signer.ORIGIN = "https://aistudio.google.com"` and `signer.API_HOST = "alkalimakersuite-pa.clients6.google.com"`.
  - `sapisid_hash(secret, ts, origin=ORIGIN) -> str`
  - `authorization_header(cookies: dict[str, str], ts: int, origin=ORIGIN) -> str`
  - `build_headers(session, *, host=API_HOST, ts=None) -> dict[str, str]`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_signer.py`. The expected digests are SHA-1 of `"1700000000 <secret> https://aistudio.google.com"`, computed independently:

```python
import pytest

from aistudio._auth.session import Session
from aistudio._auth.signer import authorization_header, build_headers, sapisid_hash
from aistudio.exceptions import AuthError

TS = 1700000000
SAPISID_DIGEST = "8bb7c14d3d8aadd9a715140f82ef17b924c19bfd"
ONEP_DIGEST = "9dcc26d7523b112e44859b26f3b92dc3f84100df"
THREEP_DIGEST = "c3288cfe57bfdb9ae86d5ab7863d1c8774942ce4"


def test_sapisid_hash_known_answer():
    assert sapisid_hash("sapisid-secret", TS) == f"{TS}_{SAPISID_DIGEST}"


def test_authorization_header_includes_1p_and_3p_variants():
    cookies = {
        "SAPISID": "sapisid-secret",
        "__Secure-1PAPISID": "onep-secret",
        "__Secure-3PAPISID": "threep-secret",
    }
    assert authorization_header(cookies, TS) == (
        f"SAPISIDHASH {TS}_{SAPISID_DIGEST} "
        f"SAPISID1PHASH {TS}_{ONEP_DIGEST} "
        f"SAPISID3PHASH {TS}_{THREEP_DIGEST}"
    )


def test_authorization_header_sapisid_only():
    assert authorization_header({"SAPISID": "sapisid-secret"}, TS) == (
        f"SAPISIDHASH {TS}_{SAPISID_DIGEST}"
    )


def test_missing_sapisid_raises_auth_error():
    with pytest.raises(AuthError) as info:
        authorization_header({"__Secure-1PAPISID": "x"}, TS)
    assert info.value.hint == 'run "aistudio login"'


def make_session(api_key):
    cookies = [
        {"name": "SAPISID", "value": "sapisid-secret", "domain": ".google.com", "expires": -1},
        {"name": "SID", "value": "sid", "domain": ".google.com", "expires": -1},
    ]
    return Session(cookies=cookies, api_key=api_key, authuser=2)


def test_build_headers():
    headers = build_headers(make_session("AIza-test"), ts=TS)
    assert headers == {
        "Authorization": f"SAPISIDHASH {TS}_{SAPISID_DIGEST}",
        "Content-Type": "application/json+protobuf",
        "Cookie": "SAPISID=sapisid-secret; SID=sid",
        "Origin": "https://aistudio.google.com",
        "Referer": "https://aistudio.google.com/",
        "X-Goog-AuthUser": "2",
        "X-User-Agent": "grpc-web-javascript/0.1",
        "X-Goog-Api-Key": "AIza-test",
    }


def test_build_headers_without_api_key_omits_header():
    assert "X-Goog-Api-Key" not in build_headers(make_session(None), ts=TS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_signer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._auth.signer'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_auth/signer.py`:

```python
"""Per-request Google auth headers for the MakerSuite RPC endpoint (spec §4.3)."""

from __future__ import annotations

import hashlib
import time

from aistudio._auth.session import Session, format_cookie_header
from aistudio.exceptions import AuthError

ORIGIN = "https://aistudio.google.com"
API_HOST = "alkalimakersuite-pa.clients6.google.com"

_HASH_COOKIES = (
    ("SAPISID", "SAPISIDHASH"),
    ("__Secure-1PAPISID", "SAPISID1PHASH"),
    ("__Secure-3PAPISID", "SAPISID3PHASH"),
)


def sapisid_hash(secret: str, ts: int, origin: str = ORIGIN) -> str:
    digest = hashlib.sha1(f"{ts} {secret} {origin}".encode()).hexdigest()
    return f"{ts}_{digest}"


def authorization_header(cookies: dict[str, str], ts: int, origin: str = ORIGIN) -> str:
    if not cookies.get("SAPISID"):
        raise AuthError("SAPISID cookie missing; cannot sign requests", hint='run "aistudio login"')
    return " ".join(
        f"{scheme} {sapisid_hash(cookies[name], ts, origin)}"
        for name, scheme in _HASH_COOKIES
        if cookies.get(name)
    )


def build_headers(
    session: Session, *, host: str = API_HOST, ts: int | None = None
) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    cookies = session.cookies_for(host)
    headers = {
        "Authorization": authorization_header(cookies, ts),
        "Content-Type": "application/json+protobuf",
        "Cookie": format_cookie_header(cookies),
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
        "X-Goog-AuthUser": str(session.authuser),
        "X-User-Agent": "grpc-web-javascript/0.1",
    }
    if session.api_key:
        headers["X-Goog-Api-Key"] = session.api_key
    return headers
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_signer.py -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_auth/signer.py tests/unit/test_signer.py
git commit -m "feat: sign MakerSuite requests with SAPISIDHASH headers"
```

---

### Task 5: json+protobuf wire codec

**Files:**
- Create: `src/aistudio/_rpc/__init__.py` (empty), `src/aistudio/_rpc/wire.py`
- Test: `tests/unit/test_wire.py`

**Interfaces:**
- Consumes: `SchemaDriftError` (Task 1)
- Produces:
  - `wire.encode(body) -> str`, `wire.normalize_elisions(text) -> str` and `wire.decode(text) -> Any`, which raises `ValueError` on invalid JSON.
  - `wire.parse_error(decoded) -> tuple[int, str] | None`
  - `wire.safe_index(value, *path, method, expected) -> Any`
  - The constant `wire.XSSI_PREFIX`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wire.py`:

```python
import pytest

from aistudio._rpc import wire
from aistudio.exceptions import SchemaDriftError


def test_encode_is_compact_and_keeps_unicode():
    assert wire.encode([None, "héllo", [1, True]]) == '[null,"héllo",[1,true]]'


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('[,[13,"x"]]', '[null,[13,"x"]]'),
        ("[1,,2]", "[1,null,2]"),
        ("[1,]", "[1]"),
        ("[,]", "[null]"),
        ("[1,,]", "[1,null]"),
        ("[[,],]", "[[null]]"),
        ("[1, ]", "[1 ]"),
        ("[]", "[]"),
        ('{"a":[,1]}', '{"a":[null,1]}'),
    ],
)
def test_normalize_elisions(raw, expected):
    assert wire.normalize_elisions(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [r'["a,,b","[,"]', r'["q\",,",1]', r'["\\",2]', r'["]",3]'],
)
def test_normalize_leaves_string_contents_alone(raw):
    assert wire.normalize_elisions(raw) == raw


def test_decode_strips_xssi_prefix():
    assert wire.decode(")]}'\n[1,[2]]") == [1, [2]]


def test_decode_empty_body_is_none():
    assert wire.decode("") is None
    assert wire.decode("  \n") is None


def test_decode_google_error_envelope():
    assert wire.decode('[,[13,"Internal error encountered."]]') == [
        None, [13, "Internal error encountered."],
    ]


def test_decode_invalid_raises_value_error():
    with pytest.raises(ValueError):
        wire.decode("<html>")


def test_parse_error_shapes():
    assert wire.parse_error([None, [7, "denied"]]) == (7, "denied")
    assert wire.parse_error([5, "not found", []]) == (5, "not found")
    assert wire.parse_error([["models/a", None]]) is None
    assert wire.parse_error([True, "x"]) is None
    assert wire.parse_error(None) is None


def test_safe_index_returns_value_including_none():
    row = [None, ["a", [None, "deep"]]]
    assert wire.safe_index(row, 1, 1, 1, method="M", expected="str") == "deep"
    assert wire.safe_index(row, 0, method="M", expected="anything") is None


def test_safe_index_raises_schema_drift_with_path():
    with pytest.raises(SchemaDriftError) as info:
        wire.safe_index([None, ["a"]], 1, 3, method="ListPrompts", expected="a title")
    assert info.value.index_path == (1, 3)
    assert info.value.method == "ListPrompts"


def test_safe_index_through_non_list_raises():
    with pytest.raises(SchemaDriftError):
        wire.safe_index([None, "str"], 1, 0, method="M", expected="list")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_wire.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_rpc/__init__.py` as an empty file.

Create `src/aistudio/_rpc/wire.py`:

```python
"""Encoding and decoding of MakerSuite ``application/json+protobuf`` bodies (spec §5.2).

Google's JSPB serialisation writes sparse arrays with elided slots such as
``[,[13,"msg"]]``. That is valid JavaScript but not valid JSON, so ``decode``
rewrites elisions to ``null`` (and drops a JavaScript trailing comma) before
parsing, without touching string contents.
"""

from __future__ import annotations

import json
from typing import Any

from aistudio.exceptions import SchemaDriftError

XSSI_PREFIX = ")]}'"


def encode(body: Any) -> str:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def normalize_elisions(text: str) -> str:
    out: list[str] = []
    prev = ""  # last significant character emitted outside a string
    last_comma = -1  # index in ``out`` of the most recent comma outside a string
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                prev = '"'
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch == ",":
            if prev in ("[", ","):
                out.append("null")
            last_comma = len(out)
            out.append(",")
            prev = ","
            continue
        if ch == "]" and prev == ",":
            # JavaScript ignores one trailing comma: ``[1,]`` has length 1.
            del out[last_comma]
        out.append(ch)
        if not ch.isspace():
            prev = ch
    return "".join(out)


def decode(text: str) -> Any:
    body = text.lstrip()
    if body.startswith(XSSI_PREFIX):
        body = body[len(XSSI_PREFIX):]
    body = body.strip()
    if not body:
        return None
    return json.loads(normalize_elisions(body))


def parse_error(decoded: Any) -> tuple[int, str] | None:
    """Return ``(grpc_status, message)`` from an error body, or None.

    Accepts ``[null, [code, "msg", ...]]`` and ``[code, "msg", ...]`` (spec H3).
    Only meaningful for non-2xx responses.
    """
    if not isinstance(decoded, list):
        return None
    candidates = [decoded]
    if len(decoded) >= 2 and decoded[0] is None and isinstance(decoded[1], list):
        candidates.insert(0, decoded[1])
    for c in candidates:
        if (
            len(c) >= 2
            and isinstance(c[0], int)
            and not isinstance(c[0], bool)
            and isinstance(c[1], str)
        ):
            return c[0], c[1]
    return None


def safe_index(value: Any, *path: int, method: str, expected: str) -> Any:
    """Follow ``path`` into nested lists, raising SchemaDriftError if it does not exist."""
    current = value
    for depth, index in enumerate(path):
        if not isinstance(current, list) or not -len(current) <= index < len(current):
            raise SchemaDriftError(method, tuple(path[: depth + 1]), expected)
        current = current[index]
    return current
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_wire.py -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_rpc tests/unit/test_wire.py
git commit -m "feat: add tolerant json+protobuf codec with schema-drift indexing"
```

---

### Task 6: Plain-HTTP transport

**Files:**
- Create: `src/aistudio/_transport/__init__.py` (empty), `src/aistudio/_transport/http.py`
- Test: `tests/unit/test_http_transport.py`

**Interfaces:**
- Consumes:
  - `Session` (Task 3)
  - `build_headers`, `API_HOST` (Task 4)
  - `wire.encode`, `wire.decode`, `wire.parse_error` (Task 5)
  - `error_from_status`, `error_from_http`, `NetworkError`, `ValidationError`, `AIStudioError` (Task 1)
- Produces:
  - The constant `http.SERVICE_URL` and `method_url(method: str) -> str`.
  - `error_for_response(status: int, text: str) -> AIStudioError`
  - `HttpTransport(session, *, client=None, timeout=60.0)`:
    - an async context manager;
    - `async post_raw(url, body_text) -> httpx.Response`;
    - `async call(method, body) -> Any`, which raises the mapped `AIStudioError` on non-2xx;
    - `async aclose()`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_http_transport.py`:

```python
import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._transport.http import HttpTransport, method_url
from aistudio.exceptions import AuthError, NetworkError, NotFoundError, ServerError, ValidationError


def make_session():
    return Session(
        cookies=[
            {"name": "SAPISID", "value": "s", "domain": ".google.com", "expires": -1},
            {"name": "SID", "value": "x", "domain": ".google.com", "expires": -1},
        ],
        api_key="AIza-test",
        authuser=1,
    )


def test_method_url():
    assert method_url("ListModels") == (
        "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
        "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
    )


@respx.mock
async def test_call_posts_jspb_and_decodes():
    route = respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text='[[["models/a",,"A"]]]')
    )
    async with HttpTransport(make_session()) as transport:
        result = await transport.call("ListModels", [None, 50])
    assert result == [[["models/a", None, "A"]]]
    request = route.calls.last.request
    assert request.content == b"[null,50]"
    assert request.headers["content-type"] == "application/json+protobuf"
    assert request.headers["x-goog-api-key"] == "AIza-test"
    assert request.headers["x-goog-authuser"] == "1"
    assert request.headers["authorization"].startswith("SAPISIDHASH ")
    assert "SAPISID=s" in request.headers["cookie"]


@pytest.mark.parametrize(
    "status,body,exc",
    [
        (404, '[,[5,"Prompt not found"]]', NotFoundError),
        (403, '[,[7,"The caller does not have permission"]]', AuthError),
        (500, '[,[13,"Internal error encountered."]]', ServerError),
        (401, "<html>login</html>", AuthError),
        (400, "", ValidationError),
    ],
)
@respx.mock
async def test_error_responses_map_to_exceptions(status, body, exc):
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(status, text=body))
    async with HttpTransport(make_session()) as transport:
        with pytest.raises(exc):
            await transport.call("ListModels", [])


@respx.mock
async def test_transport_failures_become_network_error():
    respx.post(method_url("ListModels")).mock(side_effect=httpx.ConnectError("boom"))
    async with HttpTransport(make_session()) as transport:
        with pytest.raises(NetworkError):
            await transport.call("ListModels", [])


@pytest.mark.parametrize(
    "url",
    ["https://evil.example.com/x", "https://google.com.evil.io/x", "http://aistudio.google.com/x"],
)
async def test_refuses_to_send_credentials_elsewhere(url):
    async with HttpTransport(make_session()) as transport:
        with pytest.raises(ValidationError):
            await transport.post_raw(url, "[]")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_http_transport.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._transport'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_transport/__init__.py` as an empty file.

Create `src/aistudio/_transport/http.py`:

```python
"""Plain-HTTP backend for MakerSuite RPCs that BotGuard does not protect (spec §5.2)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from aistudio._auth.session import Session
from aistudio._auth.signer import API_HOST, build_headers
from aistudio._rpc import wire
from aistudio.exceptions import (
    AIStudioError,
    NetworkError,
    ValidationError,
    error_from_http,
    error_from_status,
)

SERVICE_URL = (
    f"https://{API_HOST}/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService"
)
_ALLOWED_HOST_SUFFIXES = (".google.com", ".googleapis.com")


def method_url(method: str) -> str:
    return f"{SERVICE_URL}/{method}"


def error_for_response(status: int, text: str) -> AIStudioError:
    try:
        parsed = wire.parse_error(wire.decode(text))
    except ValueError:
        parsed = None
    if parsed is not None:
        return error_from_status(*parsed)
    return error_from_http(status, text)


class HttpTransport:
    def __init__(
        self,
        session: Session,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._session = session
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def __aenter__(self) -> HttpTransport:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def post_raw(self, url: str, body_text: str) -> httpx.Response:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(_ALLOWED_HOST_SUFFIXES):
            raise ValidationError(f"refusing to send credentials to {parsed.scheme}://{host}")
        headers = build_headers(self._session, host=host)
        try:
            return await self._client.post(url, content=body_text.encode(), headers=headers)
        except httpx.TransportError as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

    async def call(self, method: str, body: Any) -> Any:
        response = await self.post_raw(method_url(method), wire.encode(body))
        if response.status_code >= 400:
            raise error_for_response(response.status_code, response.text)
        return wire.decode(response.text)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_http_transport.py -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_transport tests/unit/test_http_transport.py
git commit -m "feat: add plain-HTTP MakerSuite transport with error mapping"
```

---

### Task 7: CLI core, `auth check/logout` and `profile` commands

**Files:**
- Create:
  - `src/aistudio/_app/__init__.py` (empty), `src/aistudio/_app/auth.py`, `src/aistudio/_app/profiles.py`
  - `src/aistudio/cli/__init__.py` (empty), `src/aistudio/cli/state.py`, `src/aistudio/cli/output.py`, `src/aistudio/cli/error_handler.py`, `src/aistudio/cli/main.py`, `src/aistudio/cli/session_cmd.py`, `src/aistudio/cli/profile_cmd.py`
- Modify: `pyproject.toml` (add the `[project.scripts]` table), `tests/conftest.py` (add the `logged_in_profile` fixture)
- Test: `tests/cli/test_errors.py`, `tests/cli/test_auth_cmd.py`, `tests/cli/test_profile_cmd.py`

**Interfaces:**
- Consumes: Tasks 1–3 (`paths.*`, `storage.*`, exceptions)
- Produces:
  - `cli.state.CliState(profile_name=None, backend="auto", verbose=0, quiet=False)` with `.profile() -> Profile`.
  - `cli.output`:
    - `json_option`, a click decorator adding `--json` as the `json_output` keyword;
    - `emit(data, *, json_output, render)`;
    - `confirm_or_fail(prompt, *, yes, json_output)`;
    - the rich consoles `stdout` and `stderr`.
  - `cli.error_handler.handle_errors`, a decorator. The command must take a `json_output` keyword.
  - `cli.main.cli`, the root click group, and `cli.main.main()`.
  - `cli.session_cmd.auth`, a click group. Task 8 adds `login` to this module.
  - `_app.auth.auth_status(profile) -> dict`
  - `_app.profiles.profile_list(current) -> dict`, `profile_create(name) -> dict`, `profile_switch(name) -> dict` and `profile_delete(name) -> dict`.
  - The pytest fixture `logged_in_profile(name="default", api_key=TEST_API_KEY, authuser=1) -> Profile`.

- [ ] **Step 1: Add the entry point and the shared test fixture**

Append to `pyproject.toml` after the `[project.optional-dependencies]` table:

```toml
[project.scripts]
aistudio = "aistudio.cli.main:main"
```

Replace `tests/conftest.py` with:

```python
import pytest

from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio.paths import get_profile

TEST_API_KEY = "AIza" + "k" * 35
TEST_COOKIES = [
    {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/", "expires": -1},
    {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/", "expires": -1},
]


@pytest.fixture
def aistudio_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AISTUDIO_HOME", str(home))
    monkeypatch.delenv("AISTUDIO_PROFILE", raising=False)
    return home


@pytest.fixture
def logged_in_profile(aistudio_home):
    def make(name="default", api_key=TEST_API_KEY, authuser=1):
        profile = get_profile(name)
        save_state(
            profile,
            {"cookies": TEST_COOKIES, "origins": []},
            AccountInfo(email=None, authuser=authuser),
        )
        save_session_info(profile, api_key)
        return profile

    return make
```

Run: `uv sync --extra dev`

- [ ] **Step 2: Write the failing tests**

Create `tests/cli/test_errors.py`:

```python
import json

import click
from click.testing import CliRunner

from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import json_option
from aistudio.exceptions import NotFoundError, WaitTimeoutError


def make_command(exc):
    @click.command()
    @json_option
    @handle_errors
    def boom(json_output):
        raise exc

    return boom


def test_app_error_prints_json_envelope_and_exits_1():
    cmd = make_command(NotFoundError("no such prompt", hint="check the id"))
    result = CliRunner().invoke(cmd, ["--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": True, "code": "NOT_FOUND", "message": "no such prompt", "hint": "check the id",
    }


def test_app_error_without_json_goes_to_stderr():
    result = CliRunner().invoke(make_command(NotFoundError("no such prompt", hint="check")), [])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: no such prompt" in result.stderr
    assert "Hint: check" in result.stderr


def test_wait_timeout_exits_2():
    result = CliRunner().invoke(make_command(WaitTimeoutError("too slow")), ["--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["code"] == "TIMEOUT"


def test_unexpected_exception_still_returns_json_and_exits_2():
    result = CliRunner().invoke(make_command(RuntimeError("kaboom")), ["--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {
        "error": True, "code": "UNEXPECTED_ERROR", "message": "RuntimeError: kaboom",
    }


def test_keyboard_interrupt_exits_130():
    result = CliRunner().invoke(make_command(KeyboardInterrupt()), ["--json"])
    assert result.exit_code == 130
    assert json.loads(result.stdout)["code"] == "INTERRUPTED"
```

Create `tests/cli/test_auth_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli
from aistudio.paths import get_profile


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_version():
    result = run("--version")
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_check_ok(logged_in_profile):
    logged_in_profile()
    result = run("auth", "check", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["profile"] == "default"
    assert data["checks"] == {"cookies_present": True, "api_key_present": True}
    assert data["account"] == {"email": None, "authuser": 1}
    assert data["warnings"] == []


def test_check_warns_without_api_key(logged_in_profile):
    logged_in_profile(api_key=None)
    result = run("auth", "check", "--json")
    data = json.loads(result.stdout)
    assert result.exit_code == 0
    assert data["checks"]["api_key_present"] is False
    assert data["warnings"] == ['no web API key captured; re-run "aistudio login"']


def test_check_not_logged_in(aistudio_home):
    result = run("auth", "check", "--json")
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["hint"] == 'run "aistudio login"'


def test_check_corrupt_storage_reports_error_not_traceback(aistudio_home):
    profile = get_profile("default").ensure()
    profile.storage_state.write_text("{not json")
    result = run("auth", "check", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"


def test_check_human_output(logged_in_profile):
    logged_in_profile()
    result = run("auth", "check")
    assert result.exit_code == 0
    assert "cookies_present: yes" in result.stdout


def test_profile_option_selects_profile(logged_in_profile):
    logged_in_profile("work")
    data = json.loads(run("-p", "work", "auth", "check", "--json").stdout)
    assert data["profile"] == "work"


def test_invalid_profile_name_is_validation_error(aistudio_home):
    result = run("-p", "../escape", "auth", "check", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_quiet_and_verbose_conflict(aistudio_home):
    assert run("--quiet", "-v", "auth", "check").exit_code == 2


def test_logout_requires_yes_in_json_mode(logged_in_profile):
    profile = logged_in_profile()
    result = run("auth", "logout", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
    assert profile.storage_state.exists()


def test_logout_deletes_credentials(logged_in_profile):
    profile = logged_in_profile()
    profile.chrome_dir.mkdir()
    result = run("auth", "logout", "-y", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "profile": "default", "removed": ["storage_state.json", "session.json", "chrome/"],
    }
    assert not profile.storage_state.exists()
    assert not profile.chrome_dir.exists()
```

Create `tests/cli/test_profile_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_list_empty(aistudio_home):
    assert json.loads(run("profile", "list", "--json").stdout) == {
        "profiles": [], "count": 0, "current": "default",
    }


def test_create_list_switch_delete(logged_in_profile):
    logged_in_profile("work")
    assert run("profile", "create", "home", "--json").exit_code == 0
    data = json.loads(run("profile", "list", "--json").stdout)
    assert data["profiles"] == [
        {"name": "home", "current": False, "logged_in": False},
        {"name": "work", "current": False, "logged_in": True},
    ]
    assert run("profile", "switch", "work", "--json").exit_code == 0
    data = json.loads(run("profile", "list", "--json").stdout)
    assert data["current"] == "work"
    assert [p["current"] for p in data["profiles"]] == [False, True]
    assert run("profile", "delete", "work", "-y", "--json").exit_code == 0
    data = json.loads(run("profile", "list", "--json").stdout)
    assert [p["name"] for p in data["profiles"]] == ["home"]
    assert data["current"] == "default"


def test_create_existing_fails(aistudio_home):
    run("profile", "create", "home", "--json")
    result = run("profile", "create", "home", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_switch_missing_is_not_found(aistudio_home):
    result = run("profile", "switch", "ghost", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "NOT_FOUND"


def test_delete_requires_yes(aistudio_home):
    run("profile", "create", "home", "--json")
    result = run("profile", "delete", "home", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/cli -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio.cli'`

- [ ] **Step 4: Write the app layer**

Create `src/aistudio/_app/__init__.py` as an empty file.

Create `src/aistudio/_app/auth.py`:

```python
"""Authentication use cases shared by the CLI and MCP layers."""

from __future__ import annotations

from typing import Any

from aistudio._auth.storage import (
    REQUIRED_COOKIES,
    account_info,
    load_api_key,
    load_state,
    missing_cookies,
)
from aistudio.exceptions import AuthError
from aistudio.paths import Profile

_LOGIN_HINT = 'run "aistudio login"'


def auth_status(profile: Profile) -> dict[str, Any]:
    """Report whether the profile holds usable credentials (no network)."""
    api_key_present = load_api_key(profile) is not None
    try:
        state = load_state(profile)
    except AuthError as exc:
        return {
            "status": "error",
            "profile": profile.name,
            "account": None,
            "checks": {"cookies_present": False, "api_key_present": api_key_present},
            "missing_cookies": list(REQUIRED_COOKIES),
            "warnings": [],
            "message": exc.message,
            "hint": exc.hint,
        }
    missing = missing_cookies(state)
    account = account_info(state)
    result: dict[str, Any] = {
        "status": "error" if missing else "ok",
        "profile": profile.name,
        "account": {"email": account.email, "authuser": account.authuser},
        "checks": {"cookies_present": not missing, "api_key_present": api_key_present},
        "missing_cookies": missing,
        "warnings": [] if api_key_present else [f"no web API key captured; re-{_LOGIN_HINT}"],
    }
    if missing:
        result["hint"] = _LOGIN_HINT
    return result
```

Create `src/aistudio/_app/profiles.py`:

```python
"""Profile management use cases."""

from __future__ import annotations

import shutil
from typing import Any

from aistudio.exceptions import NotFoundError, ValidationError
from aistudio.paths import clear_default_profile, get_profile, list_profiles, set_default_profile

_LIST_HINT = 'see "aistudio profile list"'


def profile_list(current: str) -> dict[str, Any]:
    profiles = [
        {"name": name, "current": name == current,
         "logged_in": get_profile(name).storage_state.exists()}
        for name in list_profiles()
    ]
    return {"profiles": profiles, "count": len(profiles), "current": current}


def profile_create(name: str) -> dict[str, Any]:
    profile = get_profile(name)
    if profile.root.exists():
        raise ValidationError(f"profile {name!r} already exists")
    profile.ensure()
    return {"profile": name, "created": True}


def profile_switch(name: str) -> dict[str, Any]:
    if name not in list_profiles():
        raise NotFoundError(f"no profile named {name!r}", hint=_LIST_HINT)
    set_default_profile(name)
    return {"profile": name, "default": True}


def profile_delete(name: str) -> dict[str, Any]:
    profile = get_profile(name)
    if not profile.root.is_dir():
        raise NotFoundError(f"no profile named {name!r}", hint=_LIST_HINT)
    shutil.rmtree(profile.root)
    clear_default_profile(name)
    return {"profile": name, "deleted": True}
```

- [ ] **Step 5: Write the CLI core**

Create `src/aistudio/cli/__init__.py` as an empty file.

Create `src/aistudio/cli/state.py`:

```python
"""Global CLI options shared by every command via ``ctx.obj``."""

from __future__ import annotations

from dataclasses import dataclass

from aistudio.paths import Profile, get_profile, resolve_profile_name


@dataclass
class CliState:
    profile_name: str | None = None
    backend: str = "auto"
    verbose: int = 0
    quiet: bool = False

    def profile(self) -> Profile:
        return get_profile(resolve_profile_name(self.profile_name))
```

Create `src/aistudio/cli/output.py`:

```python
"""Output helpers: one JSON document on stdout for --json, rich text otherwise."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import click
from rich.console import Console

from aistudio.exceptions import ValidationError

stdout = Console()
stderr = Console(stderr=True)

json_option = click.option(
    "--json", "json_output", is_flag=True, help="Print machine-readable JSON."
)


def emit(data: Any, *, json_output: bool, render: Callable[[Any], None]) -> None:
    if json_output:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        render(data)


def confirm_or_fail(prompt: str, *, yes: bool, json_output: bool) -> None:
    """Require -y in non-interactive use; ask on a TTY otherwise."""
    if yes:
        return
    if json_output or not sys.stdin.isatty():
        raise ValidationError(f"{prompt} (confirmation required)", hint="pass -y/--yes")
    if not click.confirm(prompt, default=False):
        raise click.Abort()
```

Create `src/aistudio/cli/error_handler.py`:

```python
"""Map exceptions to the --json error envelope and exit codes (spec §6.8)."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

import click

from aistudio.exceptions import AIStudioError, WaitTimeoutError

F = TypeVar("F", bound=Callable[..., Any])


def _report(envelope: dict[str, Any], json_output: bool) -> None:
    if json_output:
        click.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
        return
    click.echo(f"Error: {envelope['message']}", err=True)
    if envelope.get("hint"):
        click.echo(f"Hint: {envelope['hint']}", err=True)


def handle_errors(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        json_output = bool(kwargs.get("json_output"))
        try:
            return func(*args, **kwargs)
        except (click.exceptions.Exit, click.ClickException, click.Abort):
            raise
        except KeyboardInterrupt:
            _report({"error": True, "code": "INTERRUPTED", "message": "interrupted"}, json_output)
            raise SystemExit(130) from None
        except AIStudioError as exc:
            _report(exc.to_dict(), json_output)
            raise SystemExit(2 if isinstance(exc, WaitTimeoutError) else 1) from None
        except Exception as exc:
            _report(
                {"error": True, "code": "UNEXPECTED_ERROR",
                 "message": f"{type(exc).__name__}: {exc}"},
                json_output,
            )
            raise SystemExit(2) from None

    return wrapper  # type: ignore[return-value]
```

Create `src/aistudio/cli/session_cmd.py`:

```python
"""Session commands: ``auth check`` and ``auth logout`` (login is added in Task 8)."""

from __future__ import annotations

from typing import Any

import click

from aistudio._app.auth import auth_status
from aistudio._auth.storage import delete_credentials
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import confirm_or_fail, emit, json_option, stdout
from aistudio.cli.state import CliState


@click.group()
def auth() -> None:
    """Authentication management."""


def _render_check(data: dict[str, Any]) -> None:
    status = "[green]ok[/green]" if data["status"] == "ok" else "[red]error[/red]"
    stdout.print(f"Profile: {data['profile']}")
    stdout.print(f"Status:  {status}")
    for name, value in data["checks"].items():
        stdout.print(f"  {name}: {'yes' if value else 'no'}")
    for warning in data["warnings"]:
        stdout.print(f"[yellow]warning:[/yellow] {warning}")
    if data.get("hint"):
        stdout.print(f"Hint: {data['hint']}")


@auth.command("check")
@json_option
@click.pass_obj
@handle_errors
def check(state: CliState, json_output: bool) -> None:
    """Check the stored credentials of the active profile (no network)."""
    data = auth_status(state.profile())
    emit(data, json_output=json_output, render=_render_check)
    if data["status"] != "ok":
        raise SystemExit(1)


@auth.command("logout")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@json_option
@click.pass_obj
@handle_errors
def logout(state: CliState, yes: bool, json_output: bool) -> None:
    """Delete the stored credentials and browser session of the active profile."""
    profile = state.profile()
    confirm_or_fail(
        f"Delete stored credentials for profile {profile.name!r}?", yes=yes, json_output=json_output
    )
    data = {"profile": profile.name, "removed": delete_credentials(profile)}
    emit(data, json_output=json_output,
         render=lambda d: stdout.print(f"Removed: {', '.join(d['removed']) or 'nothing'}"))
```

Create `src/aistudio/cli/profile_cmd.py`:

```python
"""``aistudio profile``: named profiles, one Google account each."""

from __future__ import annotations

from typing import Any

import click

from aistudio._app.profiles import profile_create, profile_delete, profile_list, profile_switch
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import confirm_or_fail, emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.paths import resolve_profile_name


@click.group()
def profile() -> None:
    """Manage named profiles."""


def _render_list(data: dict[str, Any]) -> None:
    if not data["profiles"]:
        stdout.print("No profiles yet. Run: aistudio login")
        return
    for p in data["profiles"]:
        marker = "*" if p["current"] else " "
        status = "logged in" if p["logged_in"] else "not logged in"
        stdout.print(f"{marker} {p['name']}  ({status})")


@profile.command("list")
@json_option
@click.pass_obj
@handle_errors
def list_cmd(state: CliState, json_output: bool) -> None:
    """List profiles; * marks the active one."""
    data = profile_list(resolve_profile_name(state.profile_name))
    emit(data, json_output=json_output, render=_render_list)


@profile.command("create")
@click.argument("name")
@json_option
@handle_errors
def create_cmd(name: str, json_output: bool) -> None:
    """Create an empty profile."""
    emit(profile_create(name), json_output=json_output,
         render=lambda d: stdout.print(f"Created profile {d['profile']}"))


@profile.command("switch")
@click.argument("name")
@json_option
@handle_errors
def switch_cmd(name: str, json_output: bool) -> None:
    """Make NAME the default profile."""
    emit(profile_switch(name), json_output=json_output,
         render=lambda d: stdout.print(f"Default profile is now {d['profile']}"))


@profile.command("delete")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@json_option
@handle_errors
def delete_cmd(name: str, yes: bool, json_output: bool) -> None:
    """Delete a profile and its credentials."""
    confirm_or_fail(f"Delete profile {name!r} and its credentials?", yes=yes,
                    json_output=json_output)
    emit(profile_delete(name), json_output=json_output,
         render=lambda d: stdout.print(f"Deleted profile {d['profile']}"))
```

Create `src/aistudio/cli/main.py`:

```python
"""Root ``aistudio`` command group and global options (spec §6.1)."""

from __future__ import annotations

import logging

import click

from aistudio import __version__
from aistudio.cli import profile_cmd, session_cmd
from aistudio.cli.state import CliState


@click.group()
@click.version_option(__version__, prog_name="aistudio")
@click.option("-p", "--profile", "profile_name",
              help="Profile name (default: $AISTUDIO_PROFILE, config, or 'default').")
@click.option("--backend", type=click.Choice(["auto", "http", "browser"]), default="auto",
              show_default=True, help="Force a transport (debugging).")
@click.option("-v", "--verbose", count=True, help="-v for INFO, -vv for DEBUG.")
@click.option("--quiet", is_flag=True, help="Only print errors.")
@click.pass_context
def cli(ctx: click.Context, profile_name: str | None, backend: str, verbose: int,
        quiet: bool) -> None:
    """Unofficial CLI for the Google AI Studio web app."""
    if verbose and quiet:
        raise click.UsageError("--quiet cannot be combined with -v")
    level = logging.ERROR if quiet else {0: logging.WARNING, 1: logging.INFO}.get(
        verbose, logging.DEBUG
    )
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    ctx.obj = CliState(profile_name=profile_name, backend=backend, verbose=verbose, quiet=quiet)


cli.add_command(session_cmd.auth)
cli.add_command(profile_cmd.profile)


def main() -> None:
    cli()
```

- [ ] **Step 6: Run tests and lint**

Run: `uv run pytest -v && uv run ruff check . && uv run aistudio --help`
Expected: all tests PASS; `All checks passed!`; the help lists the `auth` and `profile` commands.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/aistudio/_app src/aistudio/cli tests
git commit -m "feat: add CLI core with JSON error envelope, auth check/logout, profiles"
```

---

### Task 8: Browser login

**Files:**
- Create: `src/aistudio/_auth/login.py`
- Modify: `src/aistudio/cli/session_cmd.py` (add `login`), `src/aistudio/cli/main.py` (register it)
- Test: `tests/unit/test_login.py`, `tests/cli/test_login_cmd.py`

**Interfaces:**
- Consumes: `Profile` (Task 2); `AccountInfo`, `save_state`, `save_session_info` (Task 3); `API_HOST` (Task 4); `BrowserError`, `WaitTimeoutError` (Task 1); the CLI helpers (Task 7)
- Produces:
  - Constants: `login.APP_URL = "https://aistudio.google.com/"`, `login.API_KEY_RE`, `login.CHROME_ARGS` and `login.IGNORE_DEFAULT_ARGS`.
  - Pure helpers: `is_logged_in(url, cookie_names) -> bool`, `api_key_from_request(url, headers) -> str | None` and `authuser_from_url(url) -> int`.
  - `default_playwright_factory()`, which returns `async_playwright` or raises `BrowserError`.
  - `async launch_persistent(playwright, profile, *, browser, headless)`, which raises `BrowserError` with a hint.
  - `LoginResult(profile: str, account: AccountInfo, api_key_captured: bool)`
  - `async run_login(profile, *, browser="chrome", fresh=False, timeout=300.0, key_wait=20.0, poll=1.0, playwright_factory=None) -> LoginResult`
  - `cli.session_cmd.login`, a click command.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_login.py`:

```python
import pytest

from aistudio._auth.login import api_key_from_request, authuser_from_url, is_logged_in, run_login
from aistudio._auth.storage import load_api_key, load_state
from aistudio.exceptions import BrowserError, WaitTimeoutError
from aistudio.paths import get_profile

KEY = "AIza" + "k" * 35
API_URL = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
)
LOGGED_IN = [
    {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/", "expires": -1},
    {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/", "expires": -1},
]


def test_is_logged_in():
    assert is_logged_in("https://aistudio.google.com/prompts/new_chat", {"SAPISID", "SID"})
    assert not is_logged_in("https://accounts.google.com/signin", {"SAPISID"})
    assert not is_logged_in("https://aistudio.google.com/", {"SID"})


def test_api_key_from_request():
    assert api_key_from_request(API_URL, {"x-goog-api-key": KEY}) == KEY
    assert api_key_from_request("https://www.google.com/x", {"x-goog-api-key": KEY}) is None
    assert api_key_from_request(API_URL, {"x-goog-api-key": "not-a-key"}) is None
    assert api_key_from_request(API_URL, {}) is None


def test_authuser_from_url():
    assert authuser_from_url("https://aistudio.google.com/?authuser=2") == 2
    assert authuser_from_url("https://aistudio.google.com/") == 0
    assert authuser_from_url("https://aistudio.google.com/?authuser=x") == 0


class FakeRequest:
    def __init__(self, url, headers):
        self.url = url
        self.headers = headers


class FakePage:
    def __init__(self, context, landing_url, html):
        self.context = context
        self.url = "about:blank"
        self._landing = landing_url
        self._html = html

    async def goto(self, url, wait_until=None):
        self.url = self._landing
        for request in self.context.emitted_requests:
            for handler in self.context.handlers.get("request", []):
                handler(request)

    async def content(self):
        return self._html


class FakeContext:
    def __init__(self, cookies, *, landing_url="https://aistudio.google.com/?authuser=1",
                 requests=(), html=""):
        self._cookies = cookies
        self.handlers = {}
        self.emitted_requests = list(requests)
        self.pages = [FakePage(self, landing_url, html)]
        self.closed = False

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def cookies(self):
        return self._cookies

    async def storage_state(self):
        foreign = {"name": "YT", "value": "y", "domain": ".youtube.com", "path": "/",
                   "expires": -1}
        return {"cookies": self._cookies + [foreign], "origins": []}

    async def new_page(self):
        return self.pages[0]

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context=None, error=None):
        self.context = context
        self.error = error
        self.launch_kwargs = None

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launch_kwargs = {"user_data_dir": user_data_dir, **kwargs}
        if self.error:
            raise self.error
        return self.context


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def factory(chromium):
    return lambda: FakePlaywright(chromium)


async def test_run_login_saves_filtered_state_and_api_key(aistudio_home):
    context = FakeContext(LOGGED_IN, requests=[FakeRequest(API_URL, {"x-goog-api-key": KEY})])
    chromium = FakeChromium(context)
    profile = get_profile("default")
    result = await run_login(profile, playwright_factory=factory(chromium), poll=0, key_wait=0)
    assert result.api_key_captured is True
    assert result.account.authuser == 1
    assert load_api_key(profile) == KEY
    assert {c["name"] for c in load_state(profile)["cookies"]} == {"SID", "SAPISID"}
    assert context.closed
    assert chromium.launch_kwargs["user_data_dir"] == str(profile.chrome_dir)
    assert chromium.launch_kwargs["channel"] == "chrome"
    assert chromium.launch_kwargs["headless"] is False
    assert chromium.launch_kwargs["ignore_default_args"] == ["--enable-automation"]
    assert "--disable-blink-features=AutomationControlled" in chromium.launch_kwargs["args"]


async def test_run_login_falls_back_to_key_in_html(aistudio_home):
    context = FakeContext(LOGGED_IN, html=f'<script>var k="{KEY}";</script>')
    profile = get_profile("default")
    result = await run_login(profile, playwright_factory=factory(FakeChromium(context)),
                             poll=0, key_wait=0)
    assert result.api_key_captured is True
    assert load_api_key(profile) == KEY


async def test_run_login_without_any_key_still_saves_cookies(aistudio_home):
    profile = get_profile("default")
    chromium = FakeChromium(FakeContext(LOGGED_IN))
    result = await run_login(profile, playwright_factory=factory(chromium), poll=0, key_wait=0)
    assert result.api_key_captured is False
    assert load_api_key(profile) is None
    assert profile.storage_state.exists()


async def test_run_login_times_out_and_closes_browser(aistudio_home):
    nid = {"name": "NID", "value": "n", "domain": ".google.com", "path": "/", "expires": -1}
    context = FakeContext([nid])
    with pytest.raises(WaitTimeoutError):
        await run_login(get_profile("default"), playwright_factory=factory(FakeChromium(context)),
                        timeout=0, poll=0)
    assert context.closed


async def test_missing_chrome_becomes_browser_error_with_hint(aistudio_home):
    chromium = FakeChromium(error=Exception("Chromium distribution 'chrome' is not found"))
    with pytest.raises(BrowserError) as info:
        await run_login(get_profile("default"), playwright_factory=factory(chromium), poll=0)
    assert "--browser chromium" in info.value.hint


async def test_chromium_browser_uses_bundled_channel(aistudio_home):
    chromium = FakeChromium(FakeContext(LOGGED_IN))
    await run_login(get_profile("default"), browser="chromium",
                    playwright_factory=factory(chromium), poll=0, key_wait=0)
    assert chromium.launch_kwargs["channel"] is None


async def test_fresh_deletes_cached_browser_profile(aistudio_home):
    profile = get_profile("default").ensure()
    profile.chrome_dir.mkdir()
    (profile.chrome_dir / "Cookies").write_text("old")
    await run_login(profile, fresh=True,
                    playwright_factory=factory(FakeChromium(FakeContext(LOGGED_IN))),
                    poll=0, key_wait=0)
    assert not (profile.chrome_dir / "Cookies").exists()
```

Create `tests/cli/test_login_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio._auth.login import LoginResult
from aistudio._auth.storage import AccountInfo
from aistudio.cli.main import cli


def test_login_command_passes_options(aistudio_home, monkeypatch):
    calls = {}

    async def fake_run_login(profile, **kwargs):
        calls.update(kwargs)
        return LoginResult(profile=profile.name, account=AccountInfo(), api_key_captured=True)

    monkeypatch.setattr("aistudio.cli.session_cmd.run_login", fake_run_login)
    result = CliRunner().invoke(
        cli, ["login", "--browser", "chromium", "--fresh", "--timeout", "30", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "profile": "default", "account": {"email": None, "authuser": 0}, "api_key_captured": True,
    }
    assert calls == {"browser": "chromium", "fresh": True, "timeout": 30}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_login.py tests/cli/test_login_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._auth.login'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_auth/login.py`:

```python
"""Interactive Google sign-in through a persistent Playwright context (spec §4.2)."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from aistudio._auth.signer import API_HOST
from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio.exceptions import BrowserError, WaitTimeoutError
from aistudio.paths import Profile

APP_URL = "https://aistudio.google.com/"
APP_HOST = "aistudio.google.com"
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
CHROME_ARGS = ["--disable-blink-features=AutomationControlled"]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def is_logged_in(url: str, cookie_names: set[str]) -> bool:
    return urlparse(url).hostname == APP_HOST and "SAPISID" in cookie_names


def api_key_from_request(url: str, headers: Mapping[str, str]) -> str | None:
    if urlparse(url).hostname != API_HOST:
        return None
    key = headers.get("x-goog-api-key")
    return key if key and API_KEY_RE.fullmatch(key) else None


def authuser_from_url(url: str) -> int:
    values = parse_qs(urlparse(url).query).get("authuser")
    try:
        return int(values[0]) if values else 0
    except ValueError:
        return 0


def default_playwright_factory() -> Callable[[], Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserError(
            "playwright is not installed", hint='pip install "aistudio-py[browser]"'
        ) from exc
    return async_playwright


async def launch_persistent(playwright: Any, profile: Profile, *, browser: str,
                            headless: bool) -> Any:
    """Open the profile's persistent Chrome; translate launch failures into BrowserError."""
    try:
        return await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir),
            channel=None if browser == "chromium" else browser,
            headless=headless,
            args=CHROME_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
            no_viewport=True,
        )
    except Exception as exc:  # playwright.async_api.Error, missing executables
        if browser == "chrome":
            hint = (
                'install Google Chrome, or use "--browser chromium" '
                'after "playwright install chromium"'
            )
        else:
            hint = f'run "playwright install {browser}", or use "--browser chrome"'
        raise BrowserError(f"could not launch {browser}: {exc}", hint=hint) from exc


@dataclass(frozen=True)
class LoginResult:
    profile: str
    account: AccountInfo
    api_key_captured: bool


async def run_login(
    profile: Profile,
    *,
    browser: str = "chrome",
    fresh: bool = False,
    timeout: float = 300.0,
    key_wait: float = 20.0,
    poll: float = 1.0,
    playwright_factory: Callable[[], Any] | None = None,
) -> LoginResult:
    factory = playwright_factory or default_playwright_factory()
    profile.ensure()
    if fresh and profile.chrome_dir.exists():
        shutil.rmtree(profile.chrome_dir)
    loop = asyncio.get_running_loop()
    async with factory() as playwright:
        context = await launch_persistent(playwright, profile, browser=browser, headless=False)
        try:
            keys: list[str] = []

            def on_request(request: Any) -> None:
                key = api_key_from_request(request.url, request.headers)
                if key:
                    keys.append(key)

            context.on("request", on_request)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(APP_URL, wait_until="commit")
            deadline = loop.time() + timeout
            while not is_logged_in(page.url, {c["name"] for c in await context.cookies()}):
                if loop.time() > deadline:
                    raise WaitTimeoutError(
                        f"sign-in not completed within {timeout:.0f}s",
                        hint="re-run with a larger --timeout",
                    )
                await asyncio.sleep(poll)
            key_deadline = loop.time() + key_wait
            while not keys and loop.time() < key_deadline:
                await asyncio.sleep(min(poll, 0.5))
            api_key = keys[-1] if keys else _key_from_html(await page.content())
            account = AccountInfo(email=None, authuser=authuser_from_url(page.url))
            save_state(profile, await context.storage_state(), account)
            save_session_info(profile, api_key)
            return LoginResult(profile.name, account, api_key is not None)
        finally:
            await context.close()


def _key_from_html(html: str) -> str | None:
    match = API_KEY_RE.search(html)
    return match.group(0) if match else None
```

Account email is left as `None` on purpose. Where the page exposes the signed-in email is a Task 12 finding, and the M2–M5 plan fills it in.

Append to `src/aistudio/cli/session_cmd.py`. Add `import asyncio` to the top-level imports, and add `from aistudio._auth.login import run_login` and `stderr` to the `cli.output` import:

```python
@click.command()
@click.option("--browser", type=click.Choice(["chrome", "chromium", "msedge"]), default="chrome",
              show_default=True, help="Browser to sign in with.")
@click.option("--fresh", is_flag=True, help="Delete the cached browser profile first.")
@click.option("--timeout", type=click.IntRange(min=1), default=300, show_default=True,
              help="Seconds to wait for sign-in.")
@json_option
@click.pass_obj
@handle_errors
def login(state: CliState, browser: str, fresh: bool, timeout: int, json_output: bool) -> None:
    """Sign in to AI Studio in a browser window and save the session."""
    profile = state.profile()
    if not json_output:
        stderr.print(f"Opening {browser}. Sign in to AI Studio in the window that appears.")
    result = asyncio.run(run_login(profile, browser=browser, fresh=fresh, timeout=timeout))
    data = {
        "profile": result.profile,
        "account": {"email": result.account.email, "authuser": result.account.authuser},
        "api_key_captured": result.api_key_captured,
    }
    emit(data, json_output=json_output,
         render=lambda d: stdout.print(
             f"Logged in (profile {d['profile']}, API key captured: {d['api_key_captured']})"))
```

In `src/aistudio/cli/main.py`, add after `cli.add_command(session_cmd.auth)`:

```python
cli.add_command(session_cmd.login)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Live check (needs the user at the keyboard)**

Run: `uv run aistudio login`
Expected: Chrome opens on AI Studio. After the user signs in, the command prints `Logged in (profile default, API key captured: True)`. `False` is a finding to record for H1 in Task 12.

Run: `uv run aistudio auth check --json`
Expected: `"status": "ok"` and `"cookies_present": true`.

- [ ] **Step 6: Commit**

```bash
git add src/aistudio/_auth/login.py src/aistudio/cli tests/unit/test_login.py tests/cli/test_login_cmd.py
git commit -m "feat: add Playwright browser login that saves cookies and the web API key"
```

---

### Task 9: Capture redaction and fixture files

**Files:**
- Create: `src/aistudio/_dev/__init__.py` (empty), `src/aistudio/_dev/fixtures.py`, `src/aistudio/_dev/redact.py`
- Test: `tests/unit/test_redact.py`, `tests/unit/test_fixtures.py`

**Interfaces:**
- Consumes: `ValidationError` (Task 1)
- Produces:
  - `fixtures.rpc_method_name(url) -> str`
  - `fixtures.FixtureWriter(out_dir)` with `.write(record: dict) -> Path`.
  - `fixtures.load_fixture(path) -> dict` and `fixtures.iter_fixtures(root) -> list[Path]`.
  - `redact.RawExchange(url, http_method, request_headers, request_body: bytes, status, response_headers, response_body: bytes)`
  - `redact.Redactor()` with `.text(str) -> str`, `.headers(dict) -> dict`, `.body(bytes) -> dict` and `.exchange(RawExchange) -> dict`.
  - Constants: `redact.REDACTED_KEY = "AIza_REDACTED"`, `redact.REDACTED_EMAIL = "user@example.com"` and `redact.SENSITIVE_HEADERS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_redact.py`:

```python
import json

from aistudio._dev.redact import REDACTED_KEY, RawExchange, Redactor

KEY = "AIza" + "S" * 35
DRIVE_A = "1" + "a" * 32
DRIVE_B = "1" + "b" * 32
RPC = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)


def exchange(**overrides):
    base = dict(
        url=RPC + "GetPrompt",
        http_method="POST",
        request_headers={"Content-Type": "application/json+protobuf"},
        request_body=b"[]",
        status=200,
        response_headers={"content-type": "application/json+protobuf"},
        response_body=b"[]",
    )
    base.update(overrides)
    return RawExchange(**base)


def test_drops_sensitive_headers_case_insensitively():
    record = Redactor().exchange(exchange(
        request_headers={
            "Cookie": "SID=secret", "AUTHORIZATION": "SAPISIDHASH 1_abc",
            "X-Goog-Api-Key": KEY, "x-goog-authuser": "1",
            "Content-Type": "application/json+protobuf",
        },
        response_headers={"Set-Cookie": "SID=new", "Content-Type": "application/json"},
    ))
    assert record["request"]["headers"] == {"content-type": "application/json+protobuf"}
    assert record["response"]["headers"] == {"content-type": "application/json"}


def test_redacts_api_key_in_url_and_body():
    record = Redactor().exchange(exchange(
        url=RPC + f"ListModels?key={KEY}&alt=json",
        request_body=f'["{KEY}"]'.encode(),
    ))
    assert record["url"] == RPC + f"ListModels?key={REDACTED_KEY}&alt=json"
    assert KEY not in json.dumps(record)


def test_redacts_email_and_authuser():
    record = Redactor().exchange(exchange(
        url=RPC + "GetPrompt?authuser=3",
        response_body=b'[["owner","someone.real@gmail.com"]]',
    ))
    assert record["url"].endswith("authuser=0")
    assert record["response"]["body"] == '[["owner","user@example.com"]]'


def test_drive_ids_map_to_stable_fakes_across_exchanges():
    redactor = Redactor()
    first = redactor.exchange(exchange(request_body=f'["{DRIVE_A}"]'.encode()))
    second = redactor.exchange(exchange(
        url=RPC + f"GetPrompt?id={DRIVE_A}",
        response_body=f'[["{DRIVE_B}","{DRIVE_A}"]]'.encode(),
    ))
    assert first["request"]["body"] == '["prompt_1"]'
    assert second["url"].endswith("id=prompt_1")
    assert second["response"]["body"] == '[["prompt_2","prompt_1"]]'


def test_binary_body_is_base64():
    record = Redactor().exchange(exchange(response_body=b"\xff\xd8\xff\xe0"))
    assert record["response"] == {
        "status": 200, "headers": {"content-type": "application/json+protobuf"},
        "body": "/9j/4A==", "body_encoding": "base64",
    }


def test_record_shape_and_method_name():
    record = Redactor().exchange(exchange())
    assert record["method"] == "GetPrompt"
    assert record["http_method"] == "POST"
    assert record["request"]["body"] == "[]"
    assert record["response"]["status"] == 200


def test_full_record_contains_no_secrets():
    secrets = ["SID=secret", "SAPISIDHASH 1_abc", KEY, "someone.real@gmail.com", DRIVE_A]
    record = Redactor().exchange(exchange(
        url=RPC + f"GetPrompt?key={KEY}",
        request_headers={"cookie": "SID=secret", "authorization": "SAPISIDHASH 1_abc"},
        request_body=f'["{DRIVE_A}","someone.real@gmail.com"]'.encode(),
        response_body=f'["{KEY}"]'.encode(),
    ))
    dumped = json.dumps(record)
    for secret in secrets:
        assert secret not in dumped
```

Create `tests/unit/test_fixtures.py`:

```python
import json

import pytest

from aistudio._dev.fixtures import FixtureWriter, iter_fixtures, load_fixture, rpc_method_name
from aistudio.exceptions import ValidationError

RPC = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)


def record(method):
    return {"method": method, "url": RPC + method, "http_method": "POST",
            "request": {"headers": {}, "body": "[]"},
            "response": {"status": 200, "headers": {}, "body": "[]"}}


def test_rpc_method_name():
    assert rpc_method_name(RPC + "ListPrompts") == "ListPrompts"
    assert rpc_method_name(RPC + "ListPrompts?alt=json") == "ListPrompts"
    assert rpc_method_name("https://content-push.googleapis.com/upload/v1") == (
        "other_content_push_googleapis_com_upload_v1"
    )


def test_writer_numbers_per_method(tmp_path):
    writer = FixtureWriter(tmp_path)
    assert writer.write(record("ListModels")) == tmp_path / "ListModels" / "1.json"
    assert writer.write(record("ListModels")) == tmp_path / "ListModels" / "2.json"
    assert writer.write(record("GetPrompt")) == tmp_path / "GetPrompt" / "1.json"
    assert json.loads((tmp_path / "GetPrompt" / "1.json").read_text())["method"] == "GetPrompt"


def test_writer_continues_after_existing_files(tmp_path):
    (tmp_path / "ListModels").mkdir()
    (tmp_path / "ListModels" / "3.json").write_text("{}")
    assert FixtureWriter(tmp_path).write(record("ListModels")).name == "4.json"


def test_load_fixture_validates(tmp_path):
    good = FixtureWriter(tmp_path).write(record("ListModels"))
    assert load_fixture(good)["method"] == "ListModels"
    bad = tmp_path / "bad.json"
    bad.write_text('{"nope": 1}')
    with pytest.raises(ValidationError):
        load_fixture(bad)


def test_iter_fixtures_sorts_numerically(tmp_path):
    writer = FixtureWriter(tmp_path)
    for _ in range(10):
        writer.write(record("ListModels"))
    writer.write(record("GetPrompt"))
    names = [f"{p.parent.name}/{p.name}" for p in iter_fixtures(tmp_path)]
    assert names[0] == "GetPrompt/1.json"
    assert names[1:3] == ["ListModels/1.json", "ListModels/2.json"]
    assert names[-1] == "ListModels/10.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_redact.py tests/unit/test_fixtures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._dev'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_dev/__init__.py` as an empty file.

Create `src/aistudio/_dev/fixtures.py`:

```python
"""Capture fixture files: ``<out>/<Method>/<n>.json`` (spec §8.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aistudio.exceptions import ValidationError


def rpc_method_name(url: str) -> str:
    parsed = urlparse(url)
    if "/$rpc/" in parsed.path:
        return parsed.path.rsplit("/", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{parsed.hostname}{parsed.path}").strip("_")
    return f"other_{slug}"[:120]


class FixtureWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._next: dict[str, int] = {}

    def write(self, record: dict[str, Any]) -> Path:
        method = record["method"]
        directory = self.out_dir / method
        directory.mkdir(parents=True, exist_ok=True)
        if method not in self._next:
            existing = [int(p.stem) for p in directory.glob("*.json") if p.stem.isdigit()]
            self._next[method] = max(existing, default=0) + 1
        path = directory / f"{self._next[method]}.json"
        self._next[method] += 1
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


def load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "url" not in data or "request" not in data:
        raise ValidationError(f"{path} is not a capture fixture")
    return data


def iter_fixtures(root: Path) -> list[Path]:
    def key(p: Path) -> tuple[str, int]:
        return p.parent.name, int(p.stem) if p.stem.isdigit() else 0

    return sorted(root.glob("*/*.json"), key=key)
```

Create `src/aistudio/_dev/redact.py`:

```python
"""In-memory redaction of captured exchanges before anything touches disk (spec §8.1)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any

from aistudio._dev.fixtures import rpc_method_name

SENSITIVE_HEADERS = frozenset(
    {"cookie", "set-cookie", "authorization", "x-goog-api-key", "x-goog-authuser"}
)
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
AUTHUSER_RE = re.compile(r"(authuser=)\d+")
DRIVE_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{24,43}(?![A-Za-z0-9_-])")
REDACTED_KEY = "AIza_REDACTED"
REDACTED_EMAIL = "user@example.com"


@dataclass
class RawExchange:
    url: str
    http_method: str
    request_headers: dict[str, str]
    request_body: bytes
    status: int
    response_headers: dict[str, str]
    response_body: bytes


@dataclass
class Redactor:
    """One instance per capture session so the same Drive ID maps to the same fake."""

    ids: dict[str, str] = field(default_factory=dict)

    def _fake_id(self, match: re.Match[str]) -> str:
        real = match.group(0)
        if real not in self.ids:
            self.ids[real] = f"prompt_{len(self.ids) + 1}"
        return self.ids[real]

    def text(self, value: str) -> str:
        value = API_KEY_RE.sub(REDACTED_KEY, value)
        value = EMAIL_RE.sub(REDACTED_EMAIL, value)
        value = AUTHUSER_RE.sub(r"\g<1>0", value)
        return DRIVE_ID_RE.sub(self._fake_id, value)

    def headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {
            k.lower(): self.text(v)
            for k, v in headers.items()
            if k.lower() not in SENSITIVE_HEADERS
        }

    def body(self, raw: bytes) -> dict[str, str]:
        try:
            return {"body": self.text(raw.decode("utf-8"))}
        except UnicodeDecodeError:
            return {"body": base64.b64encode(raw).decode("ascii"), "body_encoding": "base64"}

    def exchange(self, ex: RawExchange) -> dict[str, Any]:
        return {
            "method": rpc_method_name(ex.url),
            "url": self.text(ex.url),
            "http_method": ex.http_method,
            "request": {"headers": self.headers(ex.request_headers), **self.body(ex.request_body)},
            "response": {
                "status": ex.status,
                "headers": self.headers(ex.response_headers),
                **self.body(ex.response_body),
            },
        }
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest tests/unit/test_redact.py tests/unit/test_fixtures.py -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_dev tests/unit/test_redact.py tests/unit/test_fixtures.py
git commit -m "feat: redact captured exchanges in memory and write numbered fixtures"
```

---

### Task 10: `aistudio dev capture`

**Files:**
- Create: `src/aistudio/_dev/capture.py`, `src/aistudio/cli/dev_cmd.py`
- Modify: `src/aistudio/cli/main.py` (register `dev`)
- Test: `tests/unit/test_capture.py`, `tests/cli/test_dev_cmd.py`

**Interfaces:**
- Consumes: `APP_URL`, `launch_persistent`, `default_playwright_factory` (Task 8); `FixtureWriter`, `rpc_method_name` (Task 9); `RawExchange`, `Redactor` (Task 9); the CLI helpers (Task 7)
- Produces:
  - Constants: `capture.DEFAULT_HOST_PATTERN` and `capture.CHECKLIST: tuple[str, ...]`.
  - `capture.CaptureRecorder(writer, *, host_pattern=DEFAULT_HOST_PATTERN, on_write=None)` with `.counts: Counter[str]`, `.wants(url, http_method) -> bool` and `async .handle(request) -> Path | None`.
  - `async capture.run_capture(profile, recorder, *, browser="chrome", playwright_factory=None) -> None`
  - `cli.dev_cmd.dev`, a click group with the `capture` subcommand. Task 11 adds `replay`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_capture.py`:

```python
import json

import pytest

from aistudio._dev.capture import CaptureRecorder, run_capture
from aistudio._dev.fixtures import FixtureWriter
from aistudio.exceptions import BrowserError
from aistudio.paths import get_profile

RPC = (
    "https://alkalimakersuite-pa.clients6.google.com/$rpc/"
    "google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
)


class FakeResponse:
    def __init__(self, status=200, body=b"[]", headers=None, body_error=None):
        self.status = status
        self._body = body
        self._headers = headers or {"content-type": "application/json+protobuf"}
        self._body_error = body_error

    async def body(self):
        if self._body_error:
            raise self._body_error
        return self._body

    async def all_headers(self):
        return self._headers


class FakeRequest:
    def __init__(self, url, method="POST", body=b"[]", headers=None, response=None,
                 headers_error=None):
        self.url = url
        self.method = method
        self.post_data_buffer = body
        self._headers = headers or {}
        self._response = response if response is not None else FakeResponse()
        self._headers_error = headers_error

    async def all_headers(self):
        if self._headers_error:
            raise self._headers_error
        return self._headers

    async def response(self):
        return self._response


async def test_records_matching_request_redacted(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    headers = {"cookie": "SID=secret", "content-type": "application/json+protobuf"}
    request = FakeRequest(RPC + "ListPrompts", body=b"[null,20]", headers=headers)
    path = await recorder.handle(request)
    assert path == tmp_path / "ListPrompts" / "1.json"
    data = json.loads(path.read_text())
    assert data["request"]["body"] == "[null,20]"
    assert "cookie" not in data["request"]["headers"]
    assert recorder.counts == {"ListPrompts": 1}


@pytest.mark.parametrize(
    "url,method",
    [("https://www.gstatic.com/app.js", "GET"), (RPC + "ListPrompts", "OPTIONS")],
)
async def test_ignores_other_hosts_and_preflight(tmp_path, url, method):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    assert await recorder.handle(FakeRequest(url, method=method)) is None
    assert not list(tmp_path.iterdir())


async def test_unreadable_body_is_recorded_empty(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    response = FakeResponse(body_error=RuntimeError("body evicted"))
    path = await recorder.handle(FakeRequest(RPC + "GenerateContent", response=response))
    assert json.loads(path.read_text())["response"]["body"] == ""


async def test_handle_swallows_closed_browser_errors(tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    request = FakeRequest(RPC + "ListModels", headers_error=RuntimeError("Target closed"))
    assert await recorder.handle(request) is None
    assert not list(tmp_path.iterdir())


async def test_on_write_callback(tmp_path):
    seen = []
    recorder = CaptureRecorder(FixtureWriter(tmp_path), on_write=lambda p, m: seen.append(m))
    await recorder.handle(FakeRequest(RPC + "ListModels"))
    assert seen == ["ListModels"]


async def test_run_capture_requires_login(aistudio_home, tmp_path):
    recorder = CaptureRecorder(FixtureWriter(tmp_path))
    with pytest.raises(BrowserError) as info:
        await run_capture(get_profile("default"), recorder, playwright_factory=lambda: None)
    assert info.value.hint == 'run "aistudio login" first'
```

Create `tests/cli/test_dev_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def fake_capture(counts):
    async def run_capture(profile, recorder, *, browser):
        for method, n in counts.items():
            recorder.counts[method] += n

    return run_capture


def test_capture_json_summary(aistudio_home, monkeypatch, tmp_path):
    monkeypatch.setattr("aistudio.cli.dev_cmd.run_capture",
                        fake_capture({"ListModels": 2, "ListPrompts": 1}))
    out = tmp_path / "fx"
    result = CliRunner().invoke(cli, ["dev", "capture", "--out", str(out), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "out": str(out), "fixtures": 3, "by_method": {"ListModels": 2, "ListPrompts": 1},
    }


def test_capture_prints_checklist(aistudio_home, monkeypatch, tmp_path):
    monkeypatch.setattr("aistudio.cli.dev_cmd.run_capture", fake_capture({}))
    result = CliRunner().invoke(cli, ["dev", "capture", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "throwaway content" in result.stderr
    assert "model picker" in result.stderr
    assert "0 fixtures" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_capture.py tests/cli/test_dev_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._dev.capture'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_dev/capture.py`:

```python
"""``aistudio dev capture``: record MakerSuite traffic from a signed-in session (spec §8.1)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aistudio._auth.login import APP_URL, default_playwright_factory, launch_persistent
from aistudio._dev.fixtures import FixtureWriter, rpc_method_name
from aistudio._dev.redact import RawExchange, Redactor
from aistudio.exceptions import BrowserError
from aistudio.paths import Profile

log = logging.getLogger(__name__)

DEFAULT_HOST_PATTERN = r"(^|\.)clients6\.google\.com$|(^|\.)googleapis\.com$"

CHECKLIST = (
    "Use throwaway content: prompt text is kept in the fixtures.",
    "1. Open the model picker and click every filter chip. Open one model card, "
    "then star and unstar a model.",
    "2. Open the library, then open a prompt.",
    "3. Chat with a Gemini model four times: plain, with Search, with code execution, "
    "with thinking. Then chat once with a Gemma model.",
    "4. Save, rename, and delete a prompt.",
    "5. Count tokens.",
    "6. Generate an image, then edit it. Generate speech, then transcribe a short audio file.",
    "7. Generate a video, then a song with a music model.",
    "Close the browser window (or press Ctrl-C) when done.",
)


class CaptureRecorder:
    """Turns finished Playwright requests into redacted fixture files."""

    def __init__(
        self,
        writer: FixtureWriter,
        *,
        host_pattern: str = DEFAULT_HOST_PATTERN,
        on_write: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._writer = writer
        self._hosts = re.compile(host_pattern)
        self._redactor = Redactor()
        self._on_write = on_write
        self.counts: Counter[str] = Counter()

    def wants(self, url: str, http_method: str) -> bool:
        return http_method != "OPTIONS" and bool(self._hosts.search(urlparse(url).hostname or ""))

    async def handle(self, request: Any) -> Path | None:
        if not self.wants(request.url, request.method):
            return None
        try:
            exchange = await self._exchange(request)
        except Exception as exc:  # browser closed mid-request, target gone, ...
            log.warning("could not record %s: %s", rpc_method_name(request.url), exc)
            return None
        if exchange is None:
            return None
        record = self._redactor.exchange(exchange)
        path = self._writer.write(record)
        self.counts[record["method"]] += 1
        if self._on_write:
            self._on_write(path, record["method"])
        return path

    async def _exchange(self, request: Any) -> RawExchange | None:
        response = await request.response()
        if response is None:
            return None
        try:
            body = await response.body()
        except Exception:  # redirects and evicted streams have no body
            body = b""
        return RawExchange(
            url=request.url,
            http_method=request.method,
            request_headers=await request.all_headers(),
            request_body=request.post_data_buffer or b"",
            status=response.status,
            response_headers=await response.all_headers(),
            response_body=body,
        )


async def run_capture(
    profile: Profile,
    recorder: CaptureRecorder,
    *,
    browser: str = "chrome",
    playwright_factory: Callable[[], Any] | None = None,
) -> None:
    """Open the profile's Chrome headed and record until the window is closed."""
    if not profile.chrome_dir.exists():
        raise BrowserError(
            f"profile {profile.name!r} has no browser session", hint='run "aistudio login" first'
        )
    factory = playwright_factory or default_playwright_factory()
    async with factory() as playwright:
        context = await launch_persistent(playwright, profile, browser=browser, headless=False)
        closed = asyncio.Event()
        pending: set[asyncio.Task[Path | None]] = set()

        def on_finished(request: Any) -> None:
            task = asyncio.ensure_future(recorder.handle(request))
            pending.add(task)
            task.add_done_callback(pending.discard)

        context.on("close", lambda _context: closed.set())
        context.on("requestfinished", on_finished)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(APP_URL, wait_until="commit")
        try:
            await closed.wait()
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not closed.is_set():
                await context.close()
```

Create `src/aistudio/cli/dev_cmd.py`:

```python
"""``aistudio dev``: discovery tools for AI Studio's internal traffic (spec §8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._dev.capture import CHECKLIST, DEFAULT_HOST_PATTERN, CaptureRecorder, run_capture
from aistudio._dev.fixtures import FixtureWriter
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stderr, stdout
from aistudio.cli.state import CliState


@click.group()
def dev() -> None:
    """Discovery tools for reverse-engineering AI Studio traffic."""


def _render_capture(data: dict[str, Any]) -> None:
    stdout.print(f"{data['fixtures']} fixtures written to {data['out']}")
    for method, count in data["by_method"].items():
        stdout.print(f"  {method}: {count}")


@dev.command("capture")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path),
              default=Path("tests/fixtures"), show_default=True, help="Fixture directory.")
@click.option("--browser", type=click.Choice(["chrome", "chromium", "msedge"]), default="chrome",
              show_default=True)
@click.option("--host-pattern", default=DEFAULT_HOST_PATTERN, show_default=True,
              help="Regex of request hosts to record.")
@json_option
@click.pass_obj
@handle_errors
def capture(state: CliState, out: Path, browser: str, host_pattern: str,
            json_output: bool) -> None:
    """Record AI Studio traffic, redacted, while you use the UI."""
    profile = state.profile()
    if not json_output:
        for line in CHECKLIST:
            stderr.print(line)
    recorder = CaptureRecorder(
        FixtureWriter(out),
        host_pattern=host_pattern,
        on_write=None if json_output else lambda p, m: stderr.print(f"  recorded {m} -> {p}"),
    )
    try:
        asyncio.run(run_capture(profile, recorder, browser=browser))
    except KeyboardInterrupt:
        pass
    data = {
        "out": str(out),
        "fixtures": sum(recorder.counts.values()),
        "by_method": dict(sorted(recorder.counts.items())),
    }
    emit(data, json_output=json_output, render=_render_capture)
```

In `src/aistudio/cli/main.py`, change the import line to `from aistudio.cli import dev_cmd, profile_cmd, session_cmd`, and add after the other `add_command` calls:

```python
cli.add_command(dev_cmd.dev)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_dev/capture.py src/aistudio/cli tests/unit/test_capture.py tests/cli/test_dev_cmd.py
git commit -m "feat: add dev capture to record redacted MakerSuite traffic"
```

---

### Task 11: `aistudio dev replay`

**Files:**
- Create: `src/aistudio/_dev/replay.py`
- Modify: `src/aistudio/cli/dev_cmd.py` (add `replay`), `tests/conftest.py` (add the `write_rpc_fixture` fixture)
- Test: `tests/unit/test_replay.py`, `tests/cli/test_replay_cmd.py`

**Interfaces:**
- Consumes:
  - `HttpTransport`, `error_for_response`, `method_url` (Task 6)
  - `load_fixture`, `iter_fixtures` (Task 9)
  - `REDACTED_KEY` (Task 9)
  - `Session.load` (Task 3)
  - `AIStudioError` (Task 1)
- Produces:
  - Constants: `replay.READ_ONLY_PREFIXES = ("List", "Get", "Count", "Search", "Fetch")`.
  - `is_read_only(method) -> bool` and `clean_url(url) -> str`.
  - `ReplayResult(fixture, method, verdict, http_status=None, error_code=None, message=None)` with `.to_dict()`. The verdict is one of `http-ok`, `http-reached`, `http-rejected`, `error` or `skipped`.
  - `async replay_fixture(path, transport, *, allow_mutating=False) -> ReplayResult`
  - `async replay_all(target: Path, transport, *, allow_mutating=False, delay=0.5) -> list[ReplayResult]`
  - The pytest fixture `write_rpc_fixture(root, method, body="[]", url=None) -> Path`.

- [ ] **Step 1: Add the shared fixture writer to `tests/conftest.py`**

Add `import json` as the first line of `tests/conftest.py`, then append:

```python
@pytest.fixture
def write_rpc_fixture():
    from aistudio._transport.http import method_url

    def write(root, method, body="[]", url=None):
        path = root / method / "1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "method": method,
            "url": url or method_url(method),
            "http_method": "POST",
            "request": {"headers": {}, "body": body},
            "response": {"status": 200, "headers": {}, "body": "[]"},
        }))
        return path

    return write
```


- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_replay.py`:

```python
import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._dev.replay import clean_url, is_read_only, replay_all, replay_fixture
from aistudio._transport.http import HttpTransport, method_url


def session():
    return Session(cookies=[{"name": "SAPISID", "value": "s", "domain": ".google.com",
                             "expires": -1}])


def test_is_read_only():
    for method in ("ListPrompts", "GetPrompt", "CountTokens", "ListModels"):
        assert is_read_only(method)
    for method in ("DeletePrompt", "GenerateContent", "UpdatePrompt", "CreatePrompt"):
        assert not is_read_only(method)


def test_clean_url_drops_redacted_key():
    assert clean_url("https://x.google.com/a?key=AIza_REDACTED&alt=json") == (
        "https://x.google.com/a?alt=json"
    )
    assert clean_url("https://x.google.com/a") == "https://x.google.com/a"


@pytest.mark.parametrize(
    "status,body,verdict,code",
    [
        (200, "[[]]", "http-ok", None),
        (404, '[,[5,"Prompt not found"]]', "http-reached", "NOT_FOUND"),
        (400, '[,[3,"Invalid argument"]]', "http-reached", "VALIDATION_ERROR"),
        (403, '[,[7,"The caller does not have permission"]]', "http-rejected", "AUTH_ERROR"),
        (500, '[,[13,"Internal error encountered."]]', "error", "SERVER_ERROR"),
    ],
)
@respx.mock
async def test_replay_verdicts(tmp_path, write_rpc_fixture, status, body, verdict, code):
    path = write_rpc_fixture(tmp_path, "ListPrompts", body="[null,20]")
    route = respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(status, text=body)
    )
    async with HttpTransport(session()) as transport:
        result = await replay_fixture(path, transport)
    assert (result.verdict, result.error_code, result.http_status) == (verdict, code, status)
    assert route.calls.last.request.content == b"[null,20]"


@respx.mock(assert_all_called=False)
async def test_mutating_and_generating_rpcs_are_not_sent_by_default(tmp_path, write_rpc_fixture):
    routes = {m: respx.post(method_url(m)) for m in ("DeletePrompt", "GenerateContent")}
    async with HttpTransport(session()) as transport:
        results = [await replay_fixture(write_rpc_fixture(tmp_path, m), transport)
                   for m in routes]
    assert [r.verdict for r in results] == ["skipped", "skipped"]
    assert "--allow-mutating" in results[0].message
    assert not any(route.called for route in routes.values())


@respx.mock
async def test_mutating_rpc_sent_with_flag(tmp_path, write_rpc_fixture):
    route = respx.post(method_url("CreatePrompt")).mock(return_value=httpx.Response(200, text="[]"))
    async with HttpTransport(session()) as transport:
        result = await replay_fixture(write_rpc_fixture(tmp_path, "CreatePrompt"), transport,
                                      allow_mutating=True)
    assert result.verdict == "http-ok"
    assert route.called


async def test_non_rpc_fixture_skipped(tmp_path, write_rpc_fixture):
    path = write_rpc_fixture(tmp_path, "other_upload",
                             url="https://content-push.googleapis.com/upload/x")
    async with HttpTransport(session()) as transport:
        assert (await replay_fixture(path, transport)).verdict == "skipped"


@respx.mock
async def test_replay_strips_redacted_key_from_url(tmp_path, write_rpc_fixture):
    path = write_rpc_fixture(tmp_path, "ListModels",
                             url=method_url("ListModels") + "?key=AIza_REDACTED")
    route = respx.post(method_url("ListModels")).mock(return_value=httpx.Response(200, text="[]"))
    async with HttpTransport(session()) as transport:
        await replay_fixture(path, transport)
    assert "key=" not in str(route.calls.last.request.url)


@respx.mock
async def test_replay_all_walks_directory(tmp_path, write_rpc_fixture):
    write_rpc_fixture(tmp_path, "ListModels")
    write_rpc_fixture(tmp_path, "DeletePrompt")
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(200, text="[]"))
    async with HttpTransport(session()) as transport:
        results = await replay_all(tmp_path, transport, delay=0)
    assert [(r.method, r.verdict) for r in results] == [
        ("DeletePrompt", "skipped"), ("ListModels", "http-ok"),
    ]
```

Create `tests/cli/test_replay_cmd.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


@respx.mock
def test_replay_cli_json(logged_in_profile, write_rpc_fixture, tmp_path):
    logged_in_profile()
    write_rpc_fixture(tmp_path, "ListModels")
    respx.post(method_url("ListModels")).mock(return_value=httpx.Response(200, text="[]"))
    result = CliRunner().invoke(cli, ["dev", "replay", str(tmp_path), "--delay", "0", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["count"] == 1
    assert data["results"][0]["verdict"] == "http-ok"


def test_replay_cli_requires_login(aistudio_home, write_rpc_fixture, tmp_path):
    write_rpc_fixture(tmp_path, "ListModels")
    result = CliRunner().invoke(cli, ["dev", "replay", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "AUTH_ERROR"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_replay.py tests/cli/test_replay_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._dev.replay'`

- [ ] **Step 4: Write the implementation**

Create `src/aistudio/_dev/replay.py`:

```python
"""``aistudio dev replay``: re-send captured requests over plain HTTP to learn which RPCs
need BotGuard (spec §8.2).

Only read-only RPCs are sent unless the caller opts in, because replaying a create,
delete or generate call changes the account or spends quota and money.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aistudio._dev.fixtures import iter_fixtures, load_fixture
from aistudio._dev.redact import REDACTED_KEY
from aistudio._transport.http import HttpTransport, error_for_response
from aistudio.exceptions import AIStudioError

READ_ONLY_PREFIXES = ("List", "Get", "Count", "Search", "Fetch")
_REACHED = {"VALIDATION_ERROR", "NOT_FOUND"}
_REJECTED = {"AUTH_ERROR", "BOTGUARD_REJECTED"}


def is_read_only(method: str) -> bool:
    return method.startswith(READ_ONLY_PREFIXES)


def clean_url(url: str) -> str:
    """Drop query parameters that carry a redacted value; the signer adds real credentials."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if v != REDACTED_KEY]
    return urlunsplit(parts._replace(query=urlencode(query)))


@dataclass(frozen=True)
class ReplayResult:
    fixture: str
    method: str
    verdict: str
    http_status: int | None = None
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def replay_fixture(path: Path, transport: HttpTransport, *,
                         allow_mutating: bool = False) -> ReplayResult:
    data = load_fixture(path)
    method = data.get("method", "")
    request = data["request"]
    if "/$rpc/" not in data["url"]:
        return ReplayResult(str(path), method, "skipped", message="not a MakerSuite $rpc call")
    if not allow_mutating and not is_read_only(method):
        return ReplayResult(str(path), method, "skipped",
                            message="changes the account or spends quota; "
                                    "pass --allow-mutating to send it")
    if request.get("body_encoding") == "base64":
        return ReplayResult(str(path), method, "skipped", message="binary request body")
    try:
        response = await transport.post_raw(clean_url(data["url"]), request.get("body", ""))
    except AIStudioError as exc:
        return ReplayResult(str(path), method, "error", error_code=exc.code, message=exc.message)
    if response.status_code < 400:
        return ReplayResult(str(path), method, "http-ok", response.status_code)
    err = error_for_response(response.status_code, response.text)
    if err.code in _REACHED:
        verdict = "http-reached"
    elif err.code in _REJECTED:
        verdict = "http-rejected"
    else:
        verdict = "error"
    return ReplayResult(str(path), method, verdict, response.status_code, err.code, err.message)


async def replay_all(target: Path, transport: HttpTransport, *, allow_mutating: bool = False,
                     delay: float = 0.5) -> list[ReplayResult]:
    paths = iter_fixtures(target) if target.is_dir() else [target]
    results: list[ReplayResult] = []
    for path in paths:
        result = await replay_fixture(path, transport, allow_mutating=allow_mutating)
        results.append(result)
        if result.verdict != "skipped" and delay:
            await asyncio.sleep(delay)
    return results
```

Append to `src/aistudio/cli/dev_cmd.py`. Add these imports at the top of the file: `from rich.table import Table`, `from aistudio._auth.session import Session`, `from aistudio._dev.replay import replay_all` and `from aistudio._transport.http import HttpTransport`.

```python
def _render_replay(data: dict[str, Any]) -> None:
    table = Table("fixture", "method", "verdict", "status", "code")
    for r in data["results"]:
        table.add_row(r["fixture"], r["method"], r["verdict"], str(r["http_status"] or ""),
                      r["error_code"] or "")
    stdout.print(table)


@dev.command("replay")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--allow-mutating", is_flag=True,
              help="Also send RPCs that create, change, delete or generate "
                   "(may spend quota or money).")
@click.option("--delay", type=click.FloatRange(min=0), default=0.5, show_default=True,
              help="Seconds to wait between sent requests.")
@json_option
@click.pass_obj
@handle_errors
def replay(state: CliState, target: Path, allow_mutating: bool, delay: float,
           json_output: bool) -> None:
    """Re-send captured fixtures over plain HTTP and report which ones still work."""
    session = Session.load(state.profile())

    async def run() -> list[Any]:
        async with HttpTransport(session) as transport:
            return await replay_all(target, transport, allow_mutating=allow_mutating, delay=delay)

    results = asyncio.run(run())
    data = {"results": [r.to_dict() for r in results], "count": len(results)}
    emit(data, json_output=json_output, render=_render_replay)
```

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest -v && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/aistudio/_dev/replay.py src/aistudio/cli/dev_cmd.py tests
git commit -m "feat: add dev replay to classify RPCs by plain-HTTP reachability"
```

---

### Task 12: M1 discovery session and gate decision (with the user)

This task needs the user's signed-in browser and their decisions about anything that spends quota.
Its deliverables are fixtures and a findings document, not product code.

**Files:**
- Create: `tests/fixtures/**` (captured), `scripts/spikes/route_rewrite.py` (throwaway), `docs/wire/findings.md`

**Interfaces:**
- Consumes: the `aistudio login`, `auth check`, `dev capture` and `dev replay` commands (Tasks 7–11)
- Produces: `docs/wire/findings.md`, which records:
  - the result of each hypothesis (H1–H10);
  - the transport per RPC;
  - the BotGuard result;
  - where the account email appears;
  - the gate decision.

  The M2–M5 plan reads this file.

- [ ] **Step 1: Sign in and verify**

Run: `uv run aistudio login`, then `uv run aistudio auth check --json`
Expected: `"status": "ok"`. Record `api_key_present` as evidence for H1.

- [ ] **Step 2: Capture**

Run: `uv run aistudio dev capture --out tests/fixtures`
The user follows the printed checklist in the browser window, using throwaway content, then closes the window.
Expected: a summary listing fixtures per method, including `ListModels` and a `GenerateContent`-like method.

- [ ] **Step 3: Check the fixtures for leaks**

Run: `grep -rEl 'AIza[0-9A-Za-z_-]{35}|SAPISID|__Secure-|SAPISIDHASH' tests/fixtures; echo "exit=$?"`
Expected: no file paths printed and `exit=1`.

Run: `grep -rFil "<the user's Google email>" tests/fixtures; echo "exit=$?"`. Ask the user for their email, or have them run it.
Expected: `exit=1`.

If either check prints a file, stop. Delete that file, add the leaking pattern to `Redactor` with a test in `tests/unit/test_redact.py`, then re-capture.

- [ ] **Step 4: Replay the read-only RPCs**

Run: `uv run aistudio dev replay tests/fixtures --json > /tmp/replay-readonly.json && python3 -c "import json;[print(r['method'],r['verdict'],r['error_code']) for r in json.load(open('/tmp/replay-readonly.json'))['results'] if r['verdict']!='skipped']"`
Expected: one line per read-only fixture. `http-ok` or `http-reached` means the RPC works over plain HTTP (evidence for H7 and H8).

- [ ] **Step 5: Replay chosen mutating RPCs (ask the user before each one)**

Tell the user what each replay does, and run only the ones they approve:
- `uv run aistudio dev replay tests/fixtures/CreatePrompt/1.json --allow-mutating --json`. This creates a junk prompt in their library (H2).
- `uv run aistudio dev replay tests/fixtures/GenerateContent/1.json --allow-mutating --json`. This spends one generation; an `http-rejected` verdict suggests BotGuard is enforced.

The capture shows the actual method names; use those paths. Delete any junk prompt in the UI afterwards.

- [ ] **Step 6: Run the route-rewrite spike for H4**

Create `scripts/spikes/route_rewrite.py`:

```python
"""Throwaway spike for spec H4: is the BotGuard token bound to the request content?

Usage: uv run python scripts/spikes/route_rewrite.py [profile]
Then type exactly SPIKE_ORIGINAL into a new chat in the browser window and press Run.
"""

import asyncio
import sys

from aistudio._auth.login import default_playwright_factory, launch_persistent
from aistudio.paths import get_profile, resolve_profile_name

MARKER = "SPIKE_ORIGINAL"
REPLACEMENT = "Reply with exactly one word: REWRITTEN"


async def main(profile_name: str | None) -> None:
    profile = get_profile(resolve_profile_name(profile_name))
    async with default_playwright_factory()() as playwright:
        context = await launch_persistent(playwright, profile, browser="chrome", headless=False)
        done = asyncio.Event()

        async def rewrite(route):
            body = route.request.post_data or ""
            if MARKER not in body:
                await route.continue_()
                return
            print("intercepted the request; rewriting the prompt text", file=sys.stderr)
            await route.continue_(post_data=body.replace(MARKER, REPLACEMENT))

        async def on_response(response):
            if "MakerSuiteService/GenerateContent" in response.url and not done.is_set():
                text = await response.text()
                print(f"status={response.status} contains_REWRITTEN={'REWRITTEN' in text}")
                print(text[:2000])
                done.set()

        await context.route("**/MakerSuiteService/GenerateContent*", rewrite)
        context.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://aistudio.google.com/prompts/new_chat")
        print(f"Type {MARKER} into the chat and press Run.", file=sys.stderr)
        await done.wait()
        await context.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
```

If the capture showed a different method name for chat generation, replace `GenerateContent` in both places.

Run: `uv run python scripts/spikes/route_rewrite.py`, with the user typing the marker and pressing Run.
Expected: one of two results.
- `status=200 contains_REWRITTEN=True`: H4 holds, and `RouteRewriteStrategy` is viable.
- Anything else: H4 fails. The `SnapshotCallStrategy` investigation becomes the first item of the M2–M5 brainstorm.

- [ ] **Step 7: Write the findings**

Create `docs/wire/findings.md`. Fill in every cell from Steps 1–6; a cell with no evidence says `not observed`:

```markdown
# M1 discovery findings

- Date: 2026-MM-DD
- Commit: <git rev-parse --short HEAD>
- Fixtures: tests/fixtures/ (<N> files, <M> methods)

## Hypotheses (spec §12)

| # | Hypothesis | Result | Evidence |
|---|---|---|---|
| H1 | X-Goog-Api-Key is a static web key | | auth check / login output |
| H2 | Prompt-library RPCs do not need BotGuard | | replay of CreatePrompt/… |
| H3 | Errors use `[,[code,"msg"]]` | | fixture with non-2xx status |
| H4 | BotGuard token not bound to content | | route_rewrite.py output |
| H5 | Video returns a pollable operation | | video fixtures |
| H6 | Inline attachments accepted | | image-edit / transcribe fixtures |
| H7 | CountTokens and ListModels work over HTTP | | replay verdicts |
| H8 | Catalog metadata comes from one RPC | | model picker fixtures |
| H9 | Lyria song generation is request/job | | music fixtures |
| H10 | Stars are server-side | | star/unstar fixtures |

## Transport per RPC

| Method | Read-only | Replay verdict | Transport (http/browser) | Fixture |
|---|---|---|---|---|

## BotGuard

- Where the token sits in the generation request body (index path):
- Route-rewrite spike result:
- Chosen default strategy:

## Account email

- Where the signed-in email appears (page HTML key or RPC field):

## Gate decision (spec §11)

- Prompt-library RPCs over HTTP: yes / no
- A BotGuard strategy works: yes / no
- Decision: proceed to the M2–M5 plan, OR revisit the design because …
```

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures scripts/spikes/route_rewrite.py docs/wire/findings.md
git commit -m "docs: record M1 discovery fixtures, findings and gate decision"
```

- [ ] **Step 9: Report the gate decision to the user**

Summarise the findings table and the gate decision for the user. If the decision is "proceed", the next step is brainstorming and writing the M2–M5 plan from `docs/wire/findings.md` and the fixtures. If it is "revisit", reopen the spec at the section the failed hypothesis belongs to.
