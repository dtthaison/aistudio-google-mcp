# aistudio-py Build Reads: `app list/get/recent`, `gallery list/get` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read AI Studio Build (`https://aistudio.google.com/apps`) over plain HTTP:

- your apps;
- one app with its builder runs;
- recently opened items;
- the gallery of showcase and template apps.

**Architecture:** This follows the M2 pattern exactly:

- a registry entry, a pure params builder and a rows decoder per RPC, golden-tested against the
  recorded fixtures `ListApplets/4`, `ListRecentApplets/7`, `ListBundledApplets/3` and
  `ListAppletTrajectories/1`;
- the `client.apps` / `client.gallery` namespaces;
- `_app/apps.py` use cases, including unique-prefix ids (spec §2.1);
- `cli/app_cmd.py`.

All four RPCs returned 200 on plain-HTTP replay (`docs/wire/findings.md`).

**Tech Stack:** unchanged.

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` §6.7 (Build, read subset).

**Scope:**

- **Implements:**
  - `app list [--limit]`, `app get <id|prefix>`, `app recent`;
  - `gallery list [--group] [--tag] [--search]`, `gallery get <slug|prefix>`.
- **Deferred:** `app create` (the builder run `CodeAssistantOffline` carries a BotGuard token, so it
  is page-driven), `app export`, `app delete`, `gallery remix`. These come after the TTS work and
  a Build capture.
- **Additions not in spec §6.7:** `app recent`, and the runs shown by `app get`. Both are cheap
  reads, already recorded.

## Global Constraints

- All M0–M2 Global Constraints hold:
  - the `--json` envelope and exit codes;
  - ruff line length 100;
  - HTTP-only commands call `CliState.require_http()`;
  - decoders raise `SchemaDriftError` only for required data.
- **Required fields:**
  - an app's id;
  - the top-level lists (an empty `[]` response means an empty list).

  Gallery apps without a slug and runs without an id are skipped. All other fields are optional.
- **Two app shapes, both decoded:**
  - `storage: "applet"`: Build-store apps, with a uuid id.
  - `storage: "drive"`: older apps saved in Drive, with a Drive id.

  Only `applet` apps have builder runs; `ListAppletTrajectories` is never called for a `drive` app.
- **Recent items** take their kind from the filled reference slot:

  | Slot | Kind |
  |---|---|
  | `[8][0]` | `app` |
  | `[1][0]` | `gallery` |
  | `[2][0]` | `prompt` |
  | none | `unknown` |

- **Timestamps** are `[seconds, nanos]` and become ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`). One
  shared helper `rows.as_timestamp` replaces the private one in `rows/prompts.py`.
- **Ids and slugs** are matched exactly first, then by unique prefix. An ambiguous prefix gives
  `VALIDATION_ERROR` listing the candidates; no match gives `NOT_FOUND`.

## Review Focus

1. **An empty Build account.** `ListApplets` returns `[]` and the result is an empty list, not
   `SCHEMA_DRIFT`. Test in Task 1.
2. **A Drive-stored app.** It is listed with `storage: "drive"`, and `app get` on it never calls
   the runs RPC. Tests in Tasks 1 and 2.
3. **A recent item whose kind code is null.** The kind comes from the filled slot ("FitNexus
   Admin" → `prompt`). Test in Task 1.
4. **An ambiguous gallery prefix** (`genai_sdk`). The result is `VALIDATION_ERROR` naming the
   matches, never the first one silently. Test in Task 2.
5. **`--backend browser` on these commands.** The result is `VALIDATION_ERROR`, as for every
   HTTP-only command. Test in Task 2.

---

### Task 1: Build read decoders

**Files:**
- Create: `src/aistudio/_types/apps.py`, `src/aistudio/_rpc/params/apps.py`, `src/aistudio/_rpc/rows/apps.py`
- Modify: `src/aistudio/_rpc/rows/__init__.py` (add `as_timestamp`, `as_strs`), `src/aistudio/_rpc/rows/prompts.py` (use `as_timestamp`), `src/aistudio/_rpc/registry.py` (4 specs)
- Test: `tests/unit/test_rows_apps.py`

**Interfaces:**
- Consumes: `wire.opt_index`, `wire.safe_index`, `rows.as_str`, `SchemaDriftError`; the `captured` fixture
- Produces:
  - `rows.as_timestamp(value) -> str | None` and `rows.as_strs(value) -> tuple[str, ...]`.
  - `registry.LIST_APPLETS`, `LIST_RECENT_APPLETS`, `LIST_BUNDLED_APPLETS`, `LIST_APPLET_TRAJECTORIES`, all HTTP and idempotent.
  - `params.apps.list_apps(page_size=1000)`, `list_recent_apps()`, `list_gallery()` and `list_app_runs(app_id)`.
  - Types, each with `.to_dict()`:
    - `_types.apps.App(id, title, description, prompt, created, updated, storage)`;
    - `AppRun(id, prompt, started, updated)`;
    - `RecentItem(kind, id, title, description, opened)`;
    - `GalleryApp(slug, title, description, group, apis=(), tags=(), video_url=None, image_url=None)`.
  - `rows.apps.decode_apps(response) -> list[App]`, `decode_recent(response) -> list[RecentItem]`, `decode_gallery(response) -> list[GalleryApp]` and `decode_runs(response) -> list[AppRun]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rows_apps.py`:

```python
import pytest

from aistudio._rpc import registry
from aistudio._rpc.params.apps import list_app_runs, list_apps, list_gallery, list_recent_apps
from aistudio._rpc.rows import as_timestamp
from aistudio._rpc.rows.apps import decode_apps, decode_gallery, decode_recent, decode_runs
from aistudio._types.apps import App, AppRun, GalleryApp
from aistudio.exceptions import SchemaDriftError

FLUENT = "28a7fe28-01ab-4c19-952d-4cc266207ec4"


def test_requests_match_captures(captured):
    assert list_apps() == captured("ListApplets", 4).request
    assert list_recent_apps() == captured("ListRecentApplets", 7).request
    assert list_gallery() == captured("ListBundledApplets", 3).request
    assert list_app_runs(FLUENT) == captured("ListAppletTrajectories").request


def test_build_reads_are_plain_http():
    for spec in (registry.LIST_APPLETS, registry.LIST_RECENT_APPLETS,
                 registry.LIST_BUNDLED_APPLETS, registry.LIST_APPLET_TRAJECTORIES):
        assert spec.transport is registry.Transport.HTTP and spec.idempotent


def test_decodes_both_app_shapes(captured):
    apps = decode_apps(captured("ListApplets", 4).response)
    assert len(apps) == 4
    assert apps[0] == App(
        id=FLUENT, title="FluentFlow",
        description="Master English through interactive lessons, personalized practice, and "
                    "real-world conversation exercises designed for all skill levels.",
        prompt="leaning english", created="2026-10-06T05:05:29Z",
        updated="2026-10-06T05:05:36Z", storage="applet",
    )
    assert apps[1].description is None
    drive = apps[3]
    assert (drive.id, drive.title, drive.storage) == ("prompt_6", "FitNexus Admin", "drive")
    assert drive.prompt == "build admin page for fitness gym \n"
    assert (drive.created, drive.updated) == (None, "2026-01-09T17:02:44Z")
    assert drive.description.startswith("A modern, AI-powered administration dash")


def test_empty_account_and_missing_ids():
    assert decode_apps([]) == []
    assert decode_apps(None) == []
    with pytest.raises(SchemaDriftError):
        decode_apps([[[None, [None, None, None, ["no id"]]]]])
    with pytest.raises(SchemaDriftError):
        decode_apps(["not a list"])


def test_recent_kind_comes_from_the_filled_slot(captured):
    items = decode_recent(captured("ListRecentApplets", 7).response)
    assert [(i.kind, i.id) for i in items] == [
        ("app", FLUENT), ("gallery", "relocation_matcher"),
        ("gallery", "pet-grooming-route-optimizer"),
        ("app", "b46fa0cd-d34f-49c8-92e4-76f74ca6e74a"), ("prompt", "prompt_2"),
        ("app", "fb02c7b2-7b75-4ef4-a9cc-f320b5a6bb72"), ("gallery", "pixshop"),
        ("prompt", "prompt_3"), ("gallery", "genai_videos_with_veo"),
    ]
    assert (items[0].title, items[0].opened) == ("Untitled", "2026-10-06T05:05:31Z")
    assert decode_recent([[[["x"], "junk"]]])[0].kind == "unknown"


def test_decodes_the_gallery(captured):
    apps = decode_gallery(captured("ListBundledApplets", 3).response)
    assert len(apps) == 94
    assert apps[0] == GalleryApp(
        slug="store-locator", title="Find Near Me – Store Locator",
        description="Interactive Google Maps vector store locator with Places API (New), "
                    "distance filtering, live product inventory check, and branch details.",
        group="showcase", apis=("Google Maps API", "Places API"),
        tags=("showcase", "google-maps"),
        video_url="https://www.gstatic.com/aistudio-static/applets/store-locator/video.mp4",
        image_url="https://www.gstatic.com/aistudio-static/applets/store-locator/image.png",
    )
    templates = [a for a in apps if a.group == "template"]
    assert len(templates) == 14
    assert (templates[0].slug, templates[0].title, templates[0].apis) == ("blank", "Empty", ())
    assert decode_gallery([[["g", [[None, "no slug"], ["ok"]]]]]) == [
        GalleryApp(slug="ok", title=None, description=None, group="g")
    ]


def test_decodes_runs(captured):
    assert decode_runs(captured("ListAppletTrajectories").response) == [
        AppRun(id=FLUENT, prompt="leaning english", started="2026-10-06T05:05:30Z",
               updated="2026-10-06T05:05:30Z")
    ]
    assert decode_runs([]) == []


def test_as_timestamp():
    assert as_timestamp(["1791263129", 926446000]) == "2026-10-06T05:05:29Z"
    assert as_timestamp([1791263129]) == "2026-10-06T05:05:29Z"
    assert as_timestamp(["x"]) is None
    assert as_timestamp(None) is None


def test_to_dict_lists_tuples(captured):
    data = decode_gallery(captured("ListBundledApplets", 3).response)[0].to_dict()
    assert data["apis"] == ["Google Maps API", "Places API"]
    assert data["tags"] == ["showcase", "google-maps"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rows_apps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.params.apps'`

- [ ] **Step 3: Write the implementation**

Replace `src/aistudio/_rpc/rows/__init__.py` with:

```python
"""Positional response decoders. Shared coercions for optional fields."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_strs(value: Any) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()


def as_timestamp(value: Any) -> str | None:
    """``[seconds, nanos]`` (seconds as str or int) as ISO-8601 UTC, or None."""
    seconds = value[0] if isinstance(value, list) and value else None
    try:
        moment = datetime.fromtimestamp(int(seconds), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
```

In `src/aistudio/_rpc/rows/prompts.py`:

- change `from aistudio._rpc.rows import as_str` to `from aistudio._rpc.rows import as_str, as_timestamp`;
- change `modified=_timestamp(opt_index(meta, 4, 0)),` to `modified=as_timestamp(opt_index(meta, 4, 0)),`;
- delete the `_timestamp` function and the now-unused `from datetime import datetime, timezone` line.

Append to `src/aistudio/_rpc/registry.py`:

```python
LIST_APPLETS = RpcSpec("ListApplets", Transport.HTTP, idempotent=True)
LIST_RECENT_APPLETS = RpcSpec("ListRecentApplets", Transport.HTTP, idempotent=True)
LIST_BUNDLED_APPLETS = RpcSpec("ListBundledApplets", Transport.HTTP, idempotent=True)
LIST_APPLET_TRAJECTORIES = RpcSpec("ListAppletTrajectories", Transport.HTTP, idempotent=True)
```

Create `src/aistudio/_rpc/params/apps.py`:

```python
"""Build (apps) request builders (fixtures ListApplets/2-4, ListRecentApplets,
ListBundledApplets, ListAppletTrajectories)."""

from __future__ import annotations

from typing import Any


def list_apps(page_size: int = 1000) -> list[Any]:
    return [[page_size], [[]], 0]


def list_recent_apps() -> list[Any]:
    return []


def list_gallery() -> list[Any]:
    return []


def list_app_runs(app_id: str) -> list[Any]:
    return [app_id]
```

Create `src/aistudio/_types/apps.py`:

```python
"""Build (apps) types (spec §6.7)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class App:
    id: str
    title: str | None
    description: str | None
    prompt: str | None  # the idea the app was built from
    created: str | None  # ISO-8601 UTC
    updated: str | None
    storage: str  # "applet" (Build app store) or "drive" (older apps saved in Drive)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AppRun:
    id: str
    prompt: str | None
    started: str | None
    updated: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecentItem:
    kind: str  # "app" | "gallery" | "prompt" | "unknown"
    id: str | None
    title: str | None
    description: str | None
    opened: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GalleryApp:
    slug: str
    title: str | None
    description: str | None
    group: str | None  # "showcase" | "template"
    apis: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    video_url: str | None = None
    image_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["apis"] = list(self.apis)
        data["tags"] = list(self.tags)
        return data
```

Create `src/aistudio/_rpc/rows/apps.py`:

```python
"""Decode Build (apps) responses.

ListApplets (fixtures ListApplets/2-4) ``[[entry, ...]]``; two entry shapes:

| shape | layout |
|---|---|
| applet (Build store) | ``[null, [id, updated, created, [title, description, prompt, ...]]]`` |
| drive (older apps) | ``[[[[title, description, prompt, ...]], null, [owner, flags, null, |
|   | [modified, modifier]], drive_id]]`` |

ListRecentApplets ``[[[item, ...]]]``; item ``[opened, _, ref, title, description]`` where ref
has one filled slot: ``[8][0]`` app id, ``[1][0]`` gallery slug, ``[2][0]`` prompt/Drive id.

ListBundledApplets ``[[[group, [app, ...]], ...]]``; app
``[slug, title, description, apis, _, _, tags, video_url, image_url]``.

ListAppletTrajectories ``[[[id, started, updated, prompt], ...]]``. Timestamps ``[seconds, nanos]``.
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_str, as_strs, as_timestamp
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.apps import App, AppRun, GalleryApp, RecentItem
from aistudio.exceptions import SchemaDriftError


def _list_at(response: Any, *path: int, method: str, expected: str) -> list[Any]:
    if response is None or response == []:
        return []
    value = safe_index(response, *path, method=method, expected=expected)
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaDriftError(method, path, expected)
    return value


def decode_apps(response: Any) -> list[App]:
    entries = _list_at(response, 0, method="ListApplets", expected="the app list")
    return [_app(entry, i) for i, entry in enumerate(entries)]


def _app(entry: Any, i: int) -> App:
    applet = opt_index(entry, 1)
    applet_id = as_str(opt_index(applet, 0))
    if applet_id:
        meta = opt_index(applet, 3)
        return App(
            id=applet_id,
            title=as_str(opt_index(meta, 0)),
            description=as_str(opt_index(meta, 1)) or None,
            prompt=as_str(opt_index(meta, 2)),
            created=as_timestamp(opt_index(applet, 2)),
            updated=as_timestamp(opt_index(applet, 1)),
            storage="applet",
        )
    drive = opt_index(entry, 0)
    drive_id = as_str(opt_index(drive, 3))
    if drive_id:
        meta = opt_index(drive, 0, 0)
        return App(
            id=drive_id,
            title=as_str(opt_index(meta, 0)),
            description=as_str(opt_index(meta, 1)) or None,
            prompt=as_str(opt_index(meta, 2)),
            created=None,
            updated=as_timestamp(opt_index(drive, 2, 3, 0)),
            storage="drive",
        )
    raise SchemaDriftError("ListApplets", (0, i), "an app id")


def decode_recent(response: Any) -> list[RecentItem]:
    items = _list_at(response, 0, 0, method="ListRecentApplets", expected="the recent list")
    return [_recent(item) for item in items]


def _recent(item: Any) -> RecentItem:
    ref = opt_index(item, 2)
    for kind, path in (("app", (8, 0)), ("gallery", (1, 0)), ("prompt", (2, 0))):
        ref_id = as_str(opt_index(ref, *path))
        if ref_id:
            break
    else:
        kind, ref_id = "unknown", None
    return RecentItem(
        kind=kind,
        id=ref_id,
        title=as_str(opt_index(item, 3)),
        description=as_str(opt_index(item, 4)) or None,
        opened=as_timestamp(opt_index(item, 0)),
    )


def decode_gallery(response: Any) -> list[GalleryApp]:
    groups = _list_at(response, 0, method="ListBundledApplets", expected="the gallery groups")
    apps: list[GalleryApp] = []
    for group in groups:
        name = as_str(opt_index(group, 0))
        entries = opt_index(group, 1)
        for entry in entries if isinstance(entries, list) else []:
            slug = as_str(opt_index(entry, 0))
            if not slug:
                continue
            apps.append(GalleryApp(
                slug=slug,
                title=as_str(opt_index(entry, 1)),
                description=as_str(opt_index(entry, 2)) or None,
                group=name,
                apis=as_strs(opt_index(entry, 3)),
                tags=as_strs(opt_index(entry, 6)),
                video_url=as_str(opt_index(entry, 7)),
                image_url=as_str(opt_index(entry, 8)),
            ))
    return apps


def decode_runs(response: Any) -> list[AppRun]:
    runs = _list_at(response, 0, method="ListAppletTrajectories", expected="the run list")
    out: list[AppRun] = []
    for run in runs:
        run_id = as_str(opt_index(run, 0))
        if run_id:
            out.append(AppRun(
                id=run_id,
                prompt=as_str(opt_index(run, 3)),
                started=as_timestamp(opt_index(run, 1)),
                updated=as_timestamp(opt_index(run, 2)),
            ))
    return out
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS, including the existing prompt-list tests after the `as_timestamp` refactor; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_rpc src/aistudio/_types/apps.py tests/unit/test_rows_apps.py
git commit -m "feat: decode Build apps, recent items, gallery and builder runs"
```

---

### Task 2: Client, use cases and CLI

**Files:**
- Create: `src/aistudio/_app/apps.py`, `src/aistudio/cli/app_cmd.py`
- Modify: `src/aistudio/client.py` (add `.apps` and `.gallery`), `src/aistudio/cli/main.py` (register `app` and `gallery`, with import names sorted)
- Test: `tests/unit/test_app_apps.py`, `tests/cli/test_app_cmd.py`

**Interfaces:**
- Consumes: Task 1 decoders, params and registry; `AIStudioClient.call` (M2); `NotFoundError`, `ValidationError`; the CLI helpers
- Produces:
  - `client.apps.list(page_size=1000) -> list[App]`, `client.apps.recent() -> list[RecentItem]` and `client.apps.runs(app_id) -> list[AppRun]`.
  - `client.gallery.list() -> list[GalleryApp]`.
  - `_app.apps.pick(items, key, wanted, *, noun, hint)`, which matches exactly first, then by unique prefix.
  - `list_apps(client, *, limit=1000)`, `get_app(client, app_id)`, `recent_apps(client)`, `gallery_list(client, *, group=None, tag=None, search=None)` and `gallery_get(client, slug)`, all async and returning dicts.
  - The click groups `app_cmd.app` and `app_cmd.gallery`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_app_apps.py`:

```python
import httpx
import pytest
import respx

from aistudio._app.apps import gallery_get, gallery_list, get_app, list_apps, recent_apps
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ValidationError

FLUENT = "28a7fe28-01ab-4c19-952d-4cc266207ec4"


def serve(captured, method, n=1):
    return respx.post(method_url(method)).mock(
        return_value=httpx.Response(200, text=captured(method, n).raw["response"]["body"])
    )


@respx.mock
async def test_list_and_get_an_applet_with_its_runs(make_client, captured):
    route_list = serve(captured, "ListApplets", 4)
    route_runs = serve(captured, "ListAppletTrajectories")
    async with make_client() as client:
        listed = await list_apps(client, limit=50)
        app = await get_app(client, "28a7")
    assert listed["count"] == 4
    assert route_list.calls.last.request.content == b"[[50],[[]],0]"
    assert app["id"] == FLUENT and app["title"] == "FluentFlow"
    assert app["runs"] == [{"id": FLUENT, "prompt": "leaning english",
                            "started": "2026-10-06T05:05:30Z",
                            "updated": "2026-10-06T05:05:30Z"}]
    assert route_runs.calls.last.request.content == f'["{FLUENT}"]'.encode()


@respx.mock(assert_all_called=False)
async def test_drive_app_never_asks_for_runs(make_client, captured):
    serve(captured, "ListApplets", 4)
    route_runs = serve(captured, "ListAppletTrajectories")
    async with make_client() as client:
        app = await get_app(client, "prompt_6")
    assert (app["storage"], app["runs"]) == ("drive", [])
    assert not route_runs.called


@respx.mock
async def test_unknown_app_is_not_found(make_client, captured):
    serve(captured, "ListApplets", 4)
    async with make_client() as client:
        with pytest.raises(NotFoundError) as info:
            await get_app(client, "zzz")
    assert info.value.hint == 'see "aistudio app list"'


@respx.mock
async def test_recent(make_client, captured):
    serve(captured, "ListRecentApplets", 7)
    async with make_client() as client:
        data = await recent_apps(client)
    assert data["count"] == 9
    assert data["recent"][0]["kind"] == "app"


@respx.mock
async def test_gallery_filters_and_prefix_lookup(make_client, captured):
    serve(captured, "ListBundledApplets", 3)
    async with make_client() as client:
        templates = await gallery_list(client, group="template")
        maps = await gallery_list(client, tag="google-maps")
        lyria = await gallery_list(client, search="LYRIA")
        store = await gallery_get(client, "store")
        with pytest.raises(ValidationError) as info:
            await gallery_get(client, "genai_sdk")
    assert templates["count"] == 14
    assert maps["count"] > 0 and all("google-maps" in a["tags"] for a in maps["apps"])
    assert {"lyria-studio", "lyria-rhythm", "lyria_camera"} <= {a["slug"] for a in lyria["apps"]}
    assert store["slug"] == "store-locator"
    assert "genai_sdk_chats" in info.value.message
```

Create `tests/cli/test_app_cmd.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve(captured, method, n=1):
    respx.post(method_url(method)).mock(
        return_value=httpx.Response(200, text=captured(method, n).raw["response"]["body"])
    )


@respx.mock
def test_app_list_and_get(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListApplets", 4)
    serve(captured, "ListAppletTrajectories")
    listed = CliRunner().invoke(cli, ["app", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["count"] == 4
    shown = CliRunner().invoke(cli, ["app", "get", "28a7"])
    assert shown.exit_code == 0, shown.output
    assert "FluentFlow" in shown.stdout and "leaning english" in shown.stdout


@respx.mock
def test_app_recent_human(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListRecentApplets", 7)
    result = CliRunner().invoke(cli, ["app", "recent"])
    assert result.exit_code == 0, result.output
    assert "pixshop" in result.stdout


@respx.mock
def test_gallery_list_and_get(logged_in_profile, captured):
    logged_in_profile()
    serve(captured, "ListBundledApplets", 3)
    listed = CliRunner().invoke(cli, ["gallery", "list", "--group", "template", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["count"] == 14
    missing = CliRunner().invoke(cli, ["gallery", "get", "no-such-app", "--json"])
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["code"] == "NOT_FOUND"


def test_build_reads_refuse_the_browser_backend(logged_in_profile):
    logged_in_profile()
    result = CliRunner().invoke(cli, ["--backend", "browser", "app", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_app_apps.py tests/cli/test_app_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._app.apps'`

- [ ] **Step 3: Write the implementation**

In `src/aistudio/client.py`:

- Add these imports:
  ```python
  from aistudio._rpc.params import apps as apps_params
  from aistudio._rpc.rows.apps import decode_apps, decode_gallery, decode_recent, decode_runs
  from aistudio._types.apps import App, AppRun, GalleryApp, RecentItem
  ```
- Add `self.apps = _Apps(self)` and `self.gallery = _Gallery(self)` after `self.chat = _Chat(self)`.
- Append:

```python


class _Apps:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def list(self, page_size: int = 1000) -> list[App]:
        raw = await self._client.call(registry.LIST_APPLETS, apps_params.list_apps(page_size))
        return decode_apps(raw)

    async def recent(self) -> list[RecentItem]:
        raw = await self._client.call(registry.LIST_RECENT_APPLETS,
                                      apps_params.list_recent_apps())
        return decode_recent(raw)

    async def runs(self, app_id: str) -> list[AppRun]:
        raw = await self._client.call(registry.LIST_APPLET_TRAJECTORIES,
                                      apps_params.list_app_runs(app_id))
        return decode_runs(raw)


class _Gallery:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def list(self) -> list[GalleryApp]:
        raw = await self._client.call(registry.LIST_BUNDLED_APPLETS, apps_params.list_gallery())
        return decode_gallery(raw)
```

Create `src/aistudio/_app/apps.py`:

```python
"""Build (apps) read use cases (spec §6.7)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from aistudio.client import AIStudioClient
from aistudio.exceptions import NotFoundError, ValidationError

T = TypeVar("T")


def pick(items: Sequence[T], key: Callable[[T], str], wanted: str, *, noun: str,
         hint: str) -> T:
    """Exact match first, then a unique prefix (spec §2.1)."""
    wanted = wanted.strip()
    for item in items:
        if key(item) == wanted:
            return item
    matches = [item for item in items if key(item).startswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(sorted(key(m) for m in matches)[:10])
        raise ValidationError(f"{wanted!r} matches several {noun}s: {names}",
                              hint="type more of the id")
    raise NotFoundError(f"no {noun} {wanted!r}", hint=hint)


async def list_apps(client: AIStudioClient, *, limit: int = 1000) -> dict[str, Any]:
    apps = await client.apps.list(limit)
    return {"apps": [a.to_dict() for a in apps], "count": len(apps)}


async def get_app(client: AIStudioClient, app_id: str) -> dict[str, Any]:
    app = pick(await client.apps.list(), lambda a: a.id, app_id, noun="app",
               hint='see "aistudio app list"')
    runs = await client.apps.runs(app.id) if app.storage == "applet" else []
    return {**app.to_dict(), "runs": [r.to_dict() for r in runs]}


async def recent_apps(client: AIStudioClient) -> dict[str, Any]:
    items = await client.apps.recent()
    return {"recent": [i.to_dict() for i in items], "count": len(items)}


async def gallery_list(
    client: AIStudioClient,
    *,
    group: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    apps = await client.gallery.list()
    if group:
        apps = [a for a in apps if a.group == group]
    if tag:
        apps = [a for a in apps if tag in a.tags]
    if search:
        needle = search.lower()
        apps = [a for a in apps if needle in a.slug.lower()
                or needle in (a.title or "").lower() or needle in (a.description or "").lower()]
    return {"apps": [a.to_dict() for a in apps], "count": len(apps)}


async def gallery_get(client: AIStudioClient, slug: str) -> dict[str, Any]:
    app = pick(await client.gallery.list(), lambda a: a.slug, slug, noun="gallery app",
               hint='see "aistudio gallery list"')
    return app.to_dict()
```

Create `src/aistudio/cli/app_cmd.py`:

```python
"""``aistudio app`` and ``aistudio gallery``: AI Studio Build, read-only (spec §6.7)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import click
from rich.table import Table

from aistudio._app.apps import gallery_get, gallery_list, get_app, list_apps, recent_apps
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


def _run(state: CliState, use_case: Callable[[AIStudioClient], Awaitable[dict[str, Any]]]
         ) -> dict[str, Any]:
    state.require_http()
    profile = state.profile()

    async def go() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await use_case(client)

    return asyncio.run(go())


def _table(columns: list[str], rows: list[list[str]], *, id_column: bool = True) -> None:
    table = Table()
    for i, name in enumerate(columns):
        if i == 0 and id_column:
            table.add_column(name, no_wrap=True)
        else:
            table.add_column(name, overflow="fold")
    for row in rows:
        table.add_row(*row)
    stdout.print(table)


@click.group()
def app() -> None:
    """Your AI Studio Build apps (read-only)."""


@app.command("list")
@click.option("--limit", type=click.IntRange(1, 1000), default=1000, show_default=True)
@json_option
@click.pass_obj
@handle_errors
def app_list(state: CliState, limit: int, json_output: bool) -> None:
    """List your Build apps."""
    data = _run(state, lambda c: list_apps(c, limit=limit))
    emit(data, json_output=json_output, render=lambda d: _table(
        ["id", "title", "updated", "storage"],
        [[a["id"], a["title"] or "", a["updated"] or "", a["storage"]] for a in d["apps"]]))


@app.command("get")
@click.argument("app_id")
@json_option
@click.pass_obj
@handle_errors
def app_get(state: CliState, app_id: str, json_output: bool) -> None:
    """Show one app (id or unique prefix) and its builder runs."""
    data = _run(state, lambda c: get_app(c, app_id))

    def render(d: dict[str, Any]) -> None:
        stdout.print(f"{d['title'] or '(untitled)'}  [{d['id']}]  ({d['storage']})")
        if d["description"]:
            stdout.print(d["description"])
        stdout.print(f"Prompt: {d['prompt'] or ''}", markup=False)
        stdout.print(f"Created: {d['created'] or '-'}  Updated: {d['updated'] or '-'}")
        for run in d["runs"]:
            stdout.print(f"  run {run['started'] or '-'}: {run['prompt'] or ''}", markup=False)

    emit(data, json_output=json_output, render=render)


@app.command("recent")
@json_option
@click.pass_obj
@handle_errors
def app_recent(state: CliState, json_output: bool) -> None:
    """Recently opened apps, gallery apps and prompts."""
    data = _run(state, recent_apps)
    emit(data, json_output=json_output, render=lambda d: _table(
        ["id", "kind", "opened", "title"],
        [[i["id"] or "", i["kind"], i["opened"] or "", i["title"] or ""] for i in d["recent"]]))


@click.group()
def gallery() -> None:
    """AI Studio Build gallery: showcase apps and templates."""


@gallery.command("list")
@click.option("--group", type=click.Choice(["showcase", "template"]))
@click.option("--tag", help="e.g. google-maps")
@click.option("--search", help="Text in the slug, title or description.")
@json_option
@click.pass_obj
@handle_errors
def gallery_list_cmd(state: CliState, group: str | None, tag: str | None,
                     search: str | None, json_output: bool) -> None:
    """List gallery apps."""
    data = _run(state, lambda c: gallery_list(c, group=group, tag=tag, search=search))
    emit(data, json_output=json_output, render=lambda d: _table(
        ["slug", "group", "title", "apis"],
        [[a["slug"], a["group"] or "", a["title"] or "", ", ".join(a["apis"])]
         for a in d["apps"]]))


@gallery.command("get")
@click.argument("slug")
@json_option
@click.pass_obj
@handle_errors
def gallery_get_cmd(state: CliState, slug: str, json_output: bool) -> None:
    """Show one gallery app (slug or unique prefix)."""
    data = _run(state, lambda c: gallery_get(c, slug))

    def render(d: dict[str, Any]) -> None:
        stdout.print(f"{d['title'] or d['slug']}  [{d['slug']}]  ({d['group'] or '-'})")
        if d["description"]:
            stdout.print(d["description"])
        if d["apis"]:
            stdout.print(f"APIs: {', '.join(d['apis'])}")
        for label, url in (("Video", d["video_url"]), ("Image", d["image_url"])):
            if url:
                stdout.print(f"{label}: {url}", markup=False)

    emit(data, json_output=json_output, render=render)
```

In `src/aistudio/cli/main.py`, add `app_cmd` to the `from aistudio.cli import …` line (sorted:
`app_cmd, chat_cmd, …`) and add these after the other `add_command` calls:

```python
cli.add_command(app_cmd.app)
cli.add_command(app_cmd.gallery)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio tests
git commit -m "feat: add app list/get/recent and gallery list/get over plain HTTP"
```

---

### Task 3: Live checks and README

**Files:**
- Create: `tests/live/test_live_build.py`
- Modify: `README.md`

- [ ] **Step 1: Write the live tests**

Create `tests/live/test_live_build.py`:

```python
"""Opt-in read-only checks against real AI Studio Build: `uv run pytest -m live`."""

import os

import pytest

from aistudio.client import AIStudioClient
from aistudio.paths import get_profile, resolve_profile_name

pytestmark = pytest.mark.live


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.storage_state.exists():
        pytest.skip('no logged-in profile; run "aistudio login"')
    return p


async def test_live_apps_recent_and_gallery_decode(profile):
    async with AIStudioClient.from_profile(profile) as client:
        apps = await client.apps.list()
        recent = await client.apps.recent()
        gallery = await client.gallery.list()
        runs = [await client.apps.runs(a.id) for a in apps if a.storage == "applet"][:1]
    assert all(a.id for a in apps)
    assert all(i.kind in {"app", "gallery", "prompt", "unknown"} for i in recent)
    assert {a.group for a in gallery} >= {"showcase", "template"}
    assert all(isinstance(r, list) for r in runs)
```

- [ ] **Step 2: Run them**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: the default suite passes, and the live tests are deselected.

Run: `uv run pytest -m live tests/live/test_live_build.py -v 2>&1 | tail -3`
Expected: `1 passed` (read-only, plain HTTP).

- [ ] **Step 3: Update the README quick start**

Add these lines to the Quick start block in `README.md`, before the `dev capture` line:

```bash
uv run aistudio app list                    # your Build apps
uv run aistudio app get <id-prefix>         # one app and its builder runs
uv run aistudio gallery list --tag google-maps
```

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_live_build.py README.md
git commit -m "test: live Build read checks; document app and gallery commands"
```
