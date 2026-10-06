# aistudio-py Quick Wins: `status`, `models star/unstar`, `app export` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three commands, all over plain HTTP with the saved session and no browser:

- `aistudio status` lists AI Studio and Gemini API incidents;
- `aistudio models star|unstar <id>` and `models list --starred` sync with the picker's stars;
- `aistudio app export <app> <dir>` downloads a Build app's code, verified by SHA-256.

**Architecture:** This follows the M2 pattern: registry entries, pure params builders and rows
decoders golden-tested against `tests/fixtures/`, client namespaces, `_app/` use cases and thin
click commands. Export adds `HttpTransport.download()`, a cookie-authenticated GET to
`https://aistudio.google.com/_/upload/<app>/file/<sha256>`.

**Tech Stack:** unchanged.

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md` (§6.2 `models star`, §6.7 `app export`).
**Evidence:** `docs/wire/findings.md` ("Export, chat gate, status, stars"; H10). Fixtures:
`ListIncidentsHistory/6`, `GetUserPreferences/5`, `UpdateUserPreferences/6-7`, `GetApplet/1` and
`GetCodeAssistantSnapshot/1`. The file GET was probed live: 200, octet-stream, SHA-256 matches.

**Scope:**

- **Implements:** the three commands, plus `starred` in `models list`/`models get` output.
- **Not included:**
  - `ask` (needs an API key; the user declined, see memory `no-api-key-features`);
  - an `account` command (AI Studio returns only opaque codes);
  - incident filters by product.

## Global Constraints

- All earlier constraints hold: the `--json` envelope, exit codes, ruff line length 100,
  `require_http()` on these commands, and `SchemaDriftError` only for required data.
- **Incident codes (inferred in findings):**

  | Field | Codes |
  |---|---|
  | Products `[5]` | 1 Gemini API, 2 Live API, 3 AI Studio |
  | Update states `[3][i][0]` | 1 investigating, 2 identified, 3 monitoring, 4 resolved, 5 mitigating |

  Unknown codes appear as `product:N` and `state:N`. Times come from the epoch at `[3][i][2]`,
  as ISO UTC. The display strings are in Pacific time and are ignored.
- **Stars.** `UpdateUserPreferences` replaces the **whole** starred list, using the field mask
  `starred_models`:
  - star = current list + the model, stamped `[now_seconds, 0]`;
  - unstar = current list minus the model;
  - an empty list is sent as `[[], [["starred_models"]]]`.

  Starring requires the model to exist in the catalog. Starring an already-starred model, or
  unstarring a model that isn't starred, sends nothing.
- **Export:**
  - Find the latest snapshot time at `GetApplet[0][6]`, then the file list from
    `GetCodeAssistantSnapshot([app, null, [str(s), ns]])[0][4]`.
  - Download **all** files and verify each SHA-256 **before writing anything**.
  - Reject unsafe paths (absolute, `..`, empty, or a backslash) before downloading.
  - A non-empty destination needs `--force`. Drive-stored apps give `VALIDATION_ERROR`. An app
    without a snapshot gives `NOT_FOUND`.
  - Up to 6 downloads run in parallel.
- **`HttpTransport.download(url)`:**
  - https only, host ending in `.google.com`;
  - a `Cookie` header only (the live probe worked with cookies alone);
  - up to 3 attempts on connect failure, 5xx or 429;
  - one session reload on 401/403;
  - other errors map through `error_from_http`.

## Review Focus

1. **A snapshot path such as `../../.ssh/config` or `/etc/x`.** `VALIDATION_ERROR`, nothing
   downloaded or written. Test in Task 3.
2. **A corrupted download (hash mismatch).** Nothing is written at all, not even the good files,
   and the error names the file. Test in Task 3.
3. **Exporting into a folder with files in it.** Refused without `--force`; with `--force`, only
   the snapshot's files are written or overwritten. Test in Task 3.
4. **Starring twice, unstarring a model that isn't starred, or starring an unknown model.** A
   no-op with no request, or `NOT_FOUND` without writing. Test in Task 2.
5. **`models list` when reading preferences fails.** The list still works, with
   `starred: null`. Test in Task 2.

---

### Task 1: `status`

**Files:**
- Create: `src/aistudio/_types/status.py`, `src/aistudio/_rpc/params/status.py`, `src/aistudio/_rpc/rows/status.py`, `src/aistudio/_app/status.py`, `src/aistudio/cli/status_cmd.py`
- Modify: `src/aistudio/_rpc/registry.py` (`LIST_INCIDENTS`), `src/aistudio/client.py` (`.status`), `src/aistudio/cli/main.py` (register `status`, keeping imports sorted)
- Test: `tests/unit/test_rows_status.py`, `tests/cli/test_status_cmd.py`

**Interfaces:**
- Produces:
  - `Incident(id, title, products, state, started, updated, message, updates)` with `.active` and `.to_dict()`; `IncidentUpdate(state, time, message)`.
  - `params.status.list_incidents() -> []`.
  - `rows.status.decode_incidents(response) -> list[Incident]`.
  - `client.status.incidents()`.
  - `_app.status.list_incidents(client, *, active_only=False, limit=10) -> dict`.
  - The click command `status_cmd.status`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rows_status.py`:

```python
import pytest

from aistudio._rpc import registry
from aistudio._rpc.params.status import list_incidents
from aistudio._rpc.rows.status import decode_incidents
from aistudio._types.status import IncidentUpdate
from aistudio.exceptions import SchemaDriftError


def test_request_and_registry(captured):
    assert list_incidents() == captured("ListIncidentsHistory", 6).request
    assert registry.LIST_INCIDENTS.transport is registry.Transport.HTTP


def test_decodes_captured_incidents(captured):
    incidents = decode_incidents(captured("ListIncidentsHistory", 6).response)
    assert len(incidents) == 63
    first = incidents[0]
    assert (first.id, first.title) == (
        "GeminiAPI-voice-replication-20261001",
        "Issues creating replicated voices in Gemini API and AI Studio")
    assert first.products == ("Gemini API", "AI Studio")
    assert (first.state, first.active) == ("resolved", False)
    assert (first.started, first.updated) == ("2026-10-01T14:31:00Z", "2026-10-01T17:20:00Z")
    assert first.message.startswith("Issue has been resolved.")
    assert [u.state for u in first.updates] == ["investigating", "identified", "resolved"]
    live = [i for i in incidents if "Live API" in i.products]
    assert len(live) == 3


def test_unknown_codes_and_active_incidents():
    raw = [[[["x", "Down", 1, [[1, "t", ["1790865060"], "Looking"], [9, "t", ["1790868480"], "?"]],
              1, [7]]]]]
    (incident,) = decode_incidents(raw)
    assert incident.products == ("product:7",)
    assert (incident.state, incident.active) == ("state:9", True)
    assert incident.updates[-1] == IncidentUpdate("state:9", "2026-10-01T15:28:00Z", "?")


@pytest.mark.parametrize("response,count", [([], 0), (None, 0), ([[[]]], 0),
                                            ([[[["no-updates", "t"]]]], 1)])
def test_sparse_responses(response, count):
    assert len(decode_incidents(response)) == count


def test_incident_without_id_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_incidents([[[[None, "t"]]]])
```

Create `tests/cli/test_status_cmd.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve(text):
    respx.post(method_url("ListIncidentsHistory")).mock(return_value=httpx.Response(200, text=text))


@respx.mock
def test_status_json_limits_and_counts(logged_in_profile, captured):
    logged_in_profile()
    serve(captured("ListIncidentsHistory", 6).raw["response"]["body"])
    data = json.loads(CliRunner().invoke(cli, ["status", "--limit", "3", "--json"]).stdout)
    assert data["count"] == 3 and data["active"] == 0 and data["total"] == 63
    assert data["incidents"][0]["id"] == "GeminiAPI-voice-replication-20261001"


@respx.mock
def test_status_human_says_all_clear_and_shows_titles(logged_in_profile, captured):
    logged_in_profile()
    serve(captured("ListIncidentsHistory", 6).raw["response"]["body"])
    result = CliRunner().invoke(cli, ["status", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "No active incidents" in result.stdout
    assert "Issues with AI Studio Billing" in result.stdout


@respx.mock
def test_status_active_only(logged_in_profile):
    logged_in_profile()
    active = ["x", "AI Studio [down]", 1, [[1, "t", ["1790865060"], "Looking into it"]], 1, [3]]
    serve(wire.encode([[[active]]]))
    result = CliRunner().invoke(cli, ["status", "--active"])
    assert result.exit_code == 0, result.output
    assert "AI Studio [down]" in result.stdout and "investigating" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rows_status.py tests/cli/test_status_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.params.status'`

- [ ] **Step 3: Write the implementation**

Append to `src/aistudio/_rpc/registry.py`:

```python
LIST_INCIDENTS = RpcSpec("ListIncidentsHistory", Transport.HTTP, idempotent=True)
```

Create `src/aistudio/_rpc/params/status.py`:

```python
"""``ListIncidentsHistory`` request builder (fixtures ListIncidentsHistory/1-6)."""

from __future__ import annotations

from typing import Any


def list_incidents() -> list[Any]:
    return []
```

Create `src/aistudio/_types/status.py`:

```python
"""Service status types (AI Studio / Gemini API incidents)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IncidentUpdate:
    state: str
    time: str | None  # ISO-8601 UTC
    message: str | None


@dataclass(frozen=True)
class Incident:
    id: str
    title: str | None
    products: tuple[str, ...]
    state: str  # state of the latest update
    started: str | None
    updated: str | None
    message: str | None  # latest update's message
    updates: tuple[IncidentUpdate, ...] = ()

    @property
    def active(self) -> bool:
        return self.state != "resolved"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["products"] = list(self.products)
        data["updates"] = [asdict(u) for u in self.updates]
        data["active"] = self.active
        return data
```

Create `src/aistudio/_rpc/rows/status.py`:

```python
"""Decode ``ListIncidentsHistory`` (fixtures ListIncidentsHistory/1-6; docs/wire/findings.md).

Response ``[[[incident, ...]]]``; incident ``[id, title, _, updates, _, product_codes]``;
update ``[state_code, "YYYY-MM-DD HH:MM" (Pacific, ignored), [epoch_seconds], message]``.
Codes were inferred from 63 incidents; unknown codes surface as ``state:N`` / ``product:N``.
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_int, as_str, as_timestamp
from aistudio._rpc.wire import opt_index
from aistudio._types.status import Incident, IncidentUpdate
from aistudio.exceptions import SchemaDriftError

METHOD = "ListIncidentsHistory"
STATES = {1: "investigating", 2: "identified", 3: "monitoring", 4: "resolved", 5: "mitigating"}
PRODUCTS = {1: "Gemini API", 2: "Live API", 3: "AI Studio"}


def decode_incidents(response: Any) -> list[Incident]:
    rows = opt_index(response, 0, 0)
    if not isinstance(rows, list):
        return []
    return [_incident(row, i) for i, row in enumerate(rows)]


def _code(table: dict[int, str], value: Any, prefix: str) -> str:
    code = as_int(value)
    return table.get(code, f"{prefix}:{code}") if code is not None else f"{prefix}:?"


def _incident(row: Any, i: int) -> Incident:
    incident_id = as_str(opt_index(row, 0))
    if not incident_id:
        raise SchemaDriftError(METHOD, (0, 0, i, 0), "an incident id")
    raw_updates = opt_index(row, 3)
    updates = tuple(
        IncidentUpdate(
            state=_code(STATES, opt_index(u, 0), "state"),
            time=as_timestamp(opt_index(u, 2)),
            message=as_str(opt_index(u, 3)),
        )
        for u in (raw_updates if isinstance(raw_updates, list) else [])
    )
    products = opt_index(row, 5)
    return Incident(
        id=incident_id,
        title=as_str(opt_index(row, 1)),
        products=tuple(_code(PRODUCTS, p, "product")
                       for p in (products if isinstance(products, list) else [])),
        state=updates[-1].state if updates else "state:?",
        started=updates[0].time if updates else None,
        updated=updates[-1].time if updates else None,
        message=updates[-1].message if updates else None,
        updates=updates,
    )
```

In `src/aistudio/client.py`, add `from aistudio._rpc.params import status as status_params`,
`from aistudio._rpc.rows.status import decode_incidents` and
`from aistudio._types.status import Incident` to the imports. Add `self.status = _Status(self)`
after `self.gallery = _Gallery(self)`, and append:

```python


class _Status:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def incidents(self) -> list[Incident]:
        raw = await self._client.call(registry.LIST_INCIDENTS, status_params.list_incidents())
        return decode_incidents(raw)
```

Create `src/aistudio/_app/status.py`:

```python
"""Service status use case: AI Studio / Gemini API incidents."""

from __future__ import annotations

from typing import Any

from aistudio.client import AIStudioClient


async def list_incidents(
    client: AIStudioClient, *, active_only: bool = False, limit: int = 10
) -> dict[str, Any]:
    incidents = await client.status.incidents()
    active = [i for i in incidents if i.active]
    shown = (active if active_only else incidents)[:limit]
    return {"incidents": [i.to_dict() for i in shown], "count": len(shown),
            "active": len(active), "total": len(incidents)}
```

Create `src/aistudio/cli/status_cmd.py`:

```python
"""``aistudio status``: AI Studio and Gemini API incidents."""

from __future__ import annotations

import asyncio
from typing import Any

import click

from aistudio._app.status import list_incidents
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


def _render(data: dict[str, Any]) -> None:
    if not data["active"]:
        stdout.print("No active incidents.", markup=False)
    else:
        stdout.print(f"{data['active']} active incident(s).", markup=False)
    for incident in data["incidents"]:
        products = ", ".join(incident["products"])
        stdout.print(f"\n{incident['updated'] or '-'}  {incident['state'].upper()}  "
                     f"{incident['title'] or incident['id']}  ({products})", markup=False)
        if incident["message"]:
            stdout.print(f"  {incident['message']}", markup=False)


@click.command()
@click.option("--active", "active_only", is_flag=True, help="Only incidents not yet resolved.")
@click.option("--limit", type=click.IntRange(1, 200), default=10, show_default=True)
@json_option
@click.pass_obj
@handle_errors
def status(state: CliState, active_only: bool, limit: int, json_output: bool) -> None:
    """AI Studio and Gemini API incidents, newest first."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await list_incidents(client, active_only=active_only, limit=limit)

    emit(asyncio.run(run()), json_output=json_output, render=_render)
```

In `src/aistudio/cli/main.py`, add `status_cmd` to the `from aistudio.cli import (...)` list
(sorted) and `cli.add_command(status_cmd.status)` after the other `add_command` calls.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio tests
git commit -m "feat: add status (AI Studio and Gemini API incidents)"
```

---

### Task 2: Starred models

**Files:**
- Create: `src/aistudio/_rpc/params/preferences.py`, `src/aistudio/_rpc/rows/preferences.py`
- Modify:
  - `src/aistudio/_rpc/registry.py` (`GET_PREFERENCES`, `UPDATE_PREFERENCES`);
  - `src/aistudio/client.py` (`client.models.starred()`, `client.models.set_starred()`);
  - `src/aistudio/_app/models.py` (`star_model`; `starred` in `list_models`/`get_model`; `starred_only`);
  - `src/aistudio/cli/models_cmd.py` (`star`, `unstar`, `--starred`, ★ column).
- Test: `tests/unit/test_preferences.py`, `tests/unit/test_app_models_star.py`, `tests/cli/test_models_star_cmd.py`

**Interfaces:**
- Produces:
  - `Starred = tuple[str, tuple[int, int]]`, a model id with its `[seconds, nanos]` stamp.
  - `params.preferences.get_preferences() -> []` and `set_starred(starred: list[Starred]) -> list`.
  - `rows.preferences.decode_starred(response) -> list[Starred]`.
  - `client.models.starred() -> list[Starred]` and `client.models.set_starred(starred) -> list[Starred]`.
  - `_app.models.star_model(client, profile, model_id, *, starred=True, now=time.time) -> dict`.
  - `list_models(..., starred_only=False)` and `get_model(...)`: each model dict gains `starred: bool | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_preferences.py`:

```python
from aistudio._rpc import registry
from aistudio._rpc.params.preferences import get_preferences, set_starred
from aistudio._rpc.rows.preferences import decode_starred

STAR = ("antigravity-preview-09-2026", (1791272458, 217000000))


def test_requests_match_captures(captured):
    assert get_preferences() == captured("GetUserPreferences", 5).request
    assert set_starred([STAR]) == captured("UpdateUserPreferences", 6).request
    assert set_starred([]) == captured("UpdateUserPreferences", 7).request


def test_decode_starred(captured):
    assert decode_starred(captured("UpdateUserPreferences", 6).response) == [STAR]
    assert decode_starred(captured("GetUserPreferences", 5).response) == []
    assert decode_starred([None] * 6 + [[["models/x", ["bad"]], "junk", ["models/y"]]]) == [
        ("x", (0, 0)), ("y", (0, 0))]


def test_registry():
    assert registry.GET_PREFERENCES.idempotent and not registry.UPDATE_PREFERENCES.idempotent
```

Create `tests/unit/test_app_models_star.py`:

```python
import httpx
import pytest
import respx

from aistudio._app.models import get_model, list_models, star_model
from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError
from aistudio.paths import get_profile

T0 = 1_800_000_000.0


def prefs(starred):
    return wire.encode([2, None, None, None, None, None,
                        [[f"models/{m}", [str(s), 0]] for m, s in starred]])


def serve(captured, starred):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"]))
    respx.post(method_url("GetUserPreferences")).mock(
        return_value=httpx.Response(200, text=prefs(starred)))
    return respx.post(method_url("UpdateUserPreferences")).mock(
        side_effect=lambda request: httpx.Response(200, text=prefs([])))


@respx.mock
async def test_star_appends_with_a_timestamp(aistudio_home, make_client, captured):
    update = serve(captured, [("gemma-4-31b-it", 1700000000)])
    async with make_client() as client:
        result = await star_model(client, get_profile("default"), "LYRIA-3.5", now=lambda: T0)
    assert result == {"model": "lyria-3.5", "starred": True, "changed": True}
    sent = wire.decode(update.calls.last.request.content.decode())
    assert sent == [[None] * 6 + [[["models/gemma-4-31b-it", [1700000000, 0]],
                                   ["models/lyria-3.5", [1800000000, 0]]]],
                    [["starred_models"]]]


@respx.mock(assert_all_called=False)
async def test_noops_and_unknown_models_send_nothing(aistudio_home, make_client, captured):
    update = serve(captured, [("lyria-3.5", 1700000000)])
    profile = get_profile("default")
    async with make_client() as client:
        again = await star_model(client, profile, "lyria-3.5")
        absent = await star_model(client, profile, "gemma-4-31b-it", starred=False)
        with pytest.raises(NotFoundError):
            await star_model(client, profile, "no-such-model")
    assert (again["changed"], absent["changed"]) == (False, False)
    assert not update.called


@respx.mock
async def test_unstar_last_model_sends_an_empty_list(aistudio_home, make_client, captured):
    update = serve(captured, [("lyria-3.5", 1700000000)])
    async with make_client() as client:
        await star_model(client, get_profile("default"), "lyria-3.5", starred=False)
    assert update.calls.last.request.content == b'[[],[["starred_models"]]]'


@respx.mock
async def test_list_marks_and_filters_starred(aistudio_home, make_client, captured):
    serve(captured, [("lyria-3.5", 1700000000)])
    profile = get_profile("default")
    async with make_client() as client:
        starred = await list_models(client, profile, starred_only=True, now=lambda: T0)
        one = await get_model(client, profile, "gemma-4-31b-it", now=lambda: T0)
    assert [m["id"] for m in starred["models"]] == ["lyria-3.5"]
    assert starred["models"][0]["starred"] is True and one["starred"] is False


@respx.mock
async def test_list_survives_a_preferences_failure(aistudio_home, make_client, captured):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"]))
    respx.post(method_url("GetUserPreferences")).mock(return_value=httpx.Response(404))
    async with make_client() as client:
        data = await list_models(client, get_profile("default"), now=lambda: T0)
    assert data["count"] == 42 and data["models"][0]["starred"] is None
```

Create `tests/cli/test_models_star_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def test_star_and_unstar_commands(logged_in_profile, monkeypatch):
    logged_in_profile()
    calls = []

    async def fake(client, profile, model_id, *, starred=True):
        calls.append((model_id, starred))
        return {"model": model_id, "starred": starred, "changed": True}

    monkeypatch.setattr("aistudio.cli.models_cmd.star_model", fake)
    star = CliRunner().invoke(cli, ["models", "star", "lyria-3.5", "--json"])
    unstar = CliRunner().invoke(cli, ["models", "unstar", "lyria-3.5"])
    assert star.exit_code == 0 and unstar.exit_code == 0, star.output + unstar.output
    assert json.loads(star.stdout)["starred"] is True
    assert "Unstarred lyria-3.5" in unstar.stdout
    assert calls == [("lyria-3.5", True), ("lyria-3.5", False)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_preferences.py tests/unit/test_app_models_star.py tests/cli/test_models_star_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.params.preferences'`

- [ ] **Step 3: Write the implementation**

Append to `src/aistudio/_rpc/registry.py`:

```python
GET_PREFERENCES = RpcSpec("GetUserPreferences", Transport.HTTP, idempotent=True)
UPDATE_PREFERENCES = RpcSpec("UpdateUserPreferences", Transport.HTTP, idempotent=False)
```

Create `src/aistudio/_rpc/params/preferences.py`:

```python
"""User preference builders (fixtures GetUserPreferences/5, UpdateUserPreferences/6-7).

UpdateUserPreferences is ``[preferences, [field_mask]]``; the starred list is field ``[6]``
(``[["models/<id>", [seconds, nanos]], ...]``) and the mask ``["starred_models"]`` replaces it
whole. An empty list is sent as an empty preferences message.
"""

from __future__ import annotations

from typing import Any

Starred = tuple[str, tuple[int, int]]


def get_preferences() -> list[Any]:
    return []


def set_starred(starred: list[Starred]) -> list[Any]:
    if not starred:
        return [[], [["starred_models"]]]
    entries = [[f"models/{model_id}", [seconds, nanos]] for model_id, (seconds, nanos) in starred]
    return [[None] * 6 + [entries], [["starred_models"]]]
```

Create `src/aistudio/_rpc/rows/preferences.py`:

```python
"""Decode the starred-model list (field [6]) of a user preferences message."""

from __future__ import annotations

from typing import Any

from aistudio._rpc.params.preferences import Starred
from aistudio._rpc.rows import as_str
from aistudio._rpc.wire import opt_index


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def decode_starred(response: Any) -> list[Starred]:
    entries = opt_index(response, 6)
    starred: list[Starred] = []
    for entry in entries if isinstance(entries, list) else []:
        name = as_str(opt_index(entry, 0))
        if name:
            stamp = opt_index(entry, 1)
            starred.append((name.removeprefix("models/"),
                            (_int(opt_index(stamp, 0)), _int(opt_index(stamp, 1)))))
    return starred
```

In `src/aistudio/client.py`:
- add `from aistudio._rpc.params import preferences as preferences_params` and
  `from aistudio._rpc.rows.preferences import decode_starred` to the imports;
- add these methods to `_Models`:

```python
    async def starred(self) -> list[tuple[str, tuple[int, int]]]:
        raw = await self._client.call(registry.GET_PREFERENCES,
                                      preferences_params.get_preferences())
        return decode_starred(raw)

    async def set_starred(
        self, starred: list[tuple[str, tuple[int, int]]]
    ) -> list[tuple[str, tuple[int, int]]]:
        raw = await self._client.call(registry.UPDATE_PREFERENCES,
                                      preferences_params.set_starred(starred))
        return decode_starred(raw)
```

In `src/aistudio/_app/models.py`:
- change `from aistudio.exceptions import NotFoundError, SchemaDriftError, ValidationError` to
  `from aistudio.exceptions import AIStudioError, NotFoundError, SchemaDriftError, ValidationError`;
- add this helper after `filter_models`:

```python
async def _starred_ids(client: AIStudioClient) -> set[str] | None:
    """The starred model ids, or None when preferences cannot be read (lists still work)."""
    try:
        return {model_id for model_id, _ in await client.models.starred()}
    except AIStudioError:
        return None


def _with_star(model: Model, starred: set[str] | None) -> dict[str, Any]:
    return {**model.to_dict(), "starred": None if starred is None else model.id in starred}
```

- In `list_models`, add the parameter `starred_only: bool = False` after `refresh`, and replace its body's
  `return {"models": [m.to_dict() for m in models], "count": len(models)}` with:

```python
    starred = await _starred_ids(client)
    if starred_only:
        models = [m for m in models if starred is not None and m.id in starred]
    return {"models": [_with_star(m, starred) for m in models], "count": len(models)}
```

- In `get_model`, add `starred = await _starred_ids(client)` right after the line
  `models = await load_catalog(...)`, and replace both `return model.to_dict()` /
  `return matches[0].to_dict()` with `return _with_star(model, starred)` /
  `return _with_star(matches[0], starred)`.
- Append:

```python
async def star_model(
    client: AIStudioClient,
    profile: Profile,
    model_id: str,
    *,
    starred: bool = True,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Star or unstar a picker model; the whole list is replaced (spec §6.2, findings H10)."""
    wanted = normalize_model_id(model_id)
    if wanted not in {m.id for m in await load_catalog(client, profile)}:
        raise NotFoundError(f"no model {wanted!r} in the catalog",
                            hint='see "aistudio models list"')
    current = await client.models.starred()
    is_starred = any(m == wanted for m, _ in current)
    if is_starred == starred:
        return {"model": wanted, "starred": starred, "changed": False}
    if starred:
        updated = [*current, (wanted, (int(now()), 0))]
    else:
        updated = [entry for entry in current if entry[0] != wanted]
    await client.models.set_starred(updated)
    return {"model": wanted, "starred": starred, "changed": True}
```

In `src/aistudio/cli/models_cmd.py`:
- change `from aistudio._app.models import get_model, list_models` to
  `from aistudio._app.models import get_model, list_models, star_model`;
- in `_render_list`, replace

```python
    table = Table()
    table.add_column("id", no_wrap=True, min_width=width)
```

with

```python
    table = Table()
    table.add_column("★", no_wrap=True)
    table.add_column("id", no_wrap=True, min_width=width)
```

  and replace

```python
        table.add_row(*cells(m["id"], ", ".join(m["categories"]), m["release_date"] or "",
                             m["command"] or "", _price(m)))
```

  with

```python
        table.add_row(*cells("★" if m.get("starred") else "", m["id"],
                             ", ".join(m["categories"]), m["release_date"] or "",
                             m["command"] or "", _price(m)))
```
- In `list_cmd`, add the option `@click.option("--starred", "starred_only", is_flag=True, help="Only starred models.")`,
  the parameter `starred_only: bool`, and pass `starred_only=starred_only` to `list_models`.
- Append:

```python


def _star_command(name: str, starred: bool, help_text: str) -> None:
    @models.command(name, help=help_text)
    @click.argument("model_id")
    @json_option
    @click.pass_obj
    @handle_errors
    def command(state: CliState, model_id: str, json_output: bool) -> None:
        state.require_http()
        profile = state.profile()

        async def run() -> dict[str, Any]:
            async with AIStudioClient.from_profile(profile) as client:
                return await star_model(client, profile, model_id, starred=starred)

        verb = "Starred" if starred else "Unstarred"
        emit(asyncio.run(run()), json_output=json_output,
             render=lambda d: stdout.print(
                 f"{verb} {d['model']}" + ("" if d["changed"] else " (no change)"), markup=False))


_star_command("star", True, "Star a model, as in the AI Studio picker.")
_star_command("unstar", False, "Remove a model's star.")
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS (existing models tests included); `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio tests
git commit -m "feat: star and unstar models; show and filter starred models"
```

---

### Task 3: `app export`

**Files:**
- Modify:
  - `src/aistudio/_transport/http.py` (`download`);
  - `src/aistudio/_rpc/registry.py` (`GET_APPLET`, `GET_SNAPSHOT`);
  - `src/aistudio/_rpc/params/apps.py` and `src/aistudio/_rpc/rows/apps.py` (snapshot);
  - `src/aistudio/client.py` (`apps.snapshot_files`, `apps.download_file`);
  - `src/aistudio/_app/apps.py` (`export_app`);
  - `src/aistudio/cli/app_cmd.py` (`app export`).
- Test: `tests/unit/test_download.py`, `tests/unit/test_export.py`, `tests/cli/test_app_export_cmd.py`

**Interfaces:**
- Produces:
  - `HttpTransport.download(url) -> bytes`.
  - `params.apps.get_app(app_id)` and `get_snapshot(app_id, stamp: tuple[str, int])`.
  - `rows.apps.decode_snapshot_stamp(response) -> tuple[str, int] | None` and `decode_snapshot_files(response) -> list[tuple[str, str]]`.
  - `client.apps.snapshot_files(app_id) -> list[tuple[str, str]] | None` (None when the app has no snapshot).
  - `client.apps.download_file(app_id, sha256) -> bytes`.
  - `_app.apps.export_app(client, app_ref, dest: Path, *, force=False) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_download.py`:

```python
import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._transport.http import HttpTransport
from aistudio.exceptions import AuthError, ValidationError

URL = "https://aistudio.google.com/_/upload/app/file/abc"


def session(value="sid"):
    return Session(cookies=[{"name": "SID", "value": value, "domain": ".google.com",
                             "expires": -1}])


async def no_sleep(_):
    pass


@respx.mock
async def test_download_sends_cookies_and_retries_server_errors():
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"x")])
    async with HttpTransport(session(), sleep=no_sleep, jitter=lambda: 0.0) as transport:
        assert await transport.download(URL) == b"x"
    assert route.call_count == 2
    assert route.calls.last.request.headers["cookie"] == "SID=sid"
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
async def test_download_reloads_the_session_once_on_auth_errors():
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, content=b"y")])
    async with HttpTransport(session(), reload_session=lambda: session("fresh"),
                             sleep=no_sleep) as transport:
        assert await transport.download(URL) == b"y"
    assert route.calls.last.request.headers["cookie"] == "SID=fresh"
    unchanged = respx.get(URL + "2").mock(return_value=httpx.Response(403))
    async with HttpTransport(session(), reload_session=lambda: session(),
                             sleep=no_sleep) as transport:
        with pytest.raises(AuthError):
            await transport.download(URL + "2")
    assert unchanged.call_count == 1


async def test_download_refuses_other_hosts():
    async with HttpTransport(session()) as transport:
        with pytest.raises(ValidationError):
            await transport.download("https://evil.example.com/x")
```

Create `tests/unit/test_export.py`:

```python
import hashlib

import httpx
import pytest
import respx

from aistudio._app.apps import export_app
from aistudio._rpc import wire
from aistudio._rpc.params.apps import get_app, get_snapshot
from aistudio._rpc.rows.apps import decode_snapshot_files, decode_snapshot_stamp
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError, ServerError, ValidationError

APP = "d6d4717c-30c3-4564-ac77-50cb262d3884"
STAMP = ("1791281606", 723699000)


def test_snapshot_requests_and_decoders(captured):
    assert get_app(APP) == captured("GetApplet").request
    assert get_snapshot(APP, STAMP) == captured("GetCodeAssistantSnapshot").request
    assert decode_snapshot_stamp(captured("GetApplet").response) == STAMP
    files = decode_snapshot_files(captured("GetCodeAssistantSnapshot").response)
    assert len(files) == 21 and files[0] == (
        ".env.example", "b6d12c728de0aac8b0bc6239e53aa8088c1431bcb4f9a3d61e5e8a2df1c7c400")
    assert decode_snapshot_stamp([[APP, None, None, None, None, None, None]]) is None
    assert decode_snapshot_files([]) == []


def sha(data):
    return hashlib.sha256(data).hexdigest()


FILES = {"index.html": b"<html></html>", "src/App.tsx": b"export default 1;\n"}


def serve(files, *, corrupt=None, apps=None):
    applet = [None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]
    respx.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([apps or [applet]])))
    respx.post(method_url("GetApplet")).mock(return_value=httpx.Response(
        200, text=wire.encode([[APP, None, None, None, None, None, list(STAMP)], APP])))
    listing = [[path, sha(data)] for path, data in files.items()]
    respx.post(method_url("GetCodeAssistantSnapshot")).mock(return_value=httpx.Response(
        200, text=wire.encode([["model", "s", None, list(STAMP), listing]])))
    gets = []
    for path, data in files.items():
        body = b"tampered" if path == corrupt else data
        gets.append(respx.get(f"https://aistudio.google.com/_/upload/{APP}/file/{sha(data)}").mock(
            return_value=httpx.Response(200, content=body)))
    return gets


@respx.mock
async def test_export_writes_verified_files(make_client, tmp_path):
    serve(FILES)
    dest = tmp_path / "out"
    async with make_client() as client:
        result = await export_app(client, "d6d4", dest)
    assert (dest / "src/App.tsx").read_bytes() == FILES["src/App.tsx"]
    assert result["count"] == 2 and result["dir"] == str(dest) and result["title"] == "Quotes"
    assert result["bytes"] == sum(len(d) for d in FILES.values())


@respx.mock
async def test_a_corrupted_file_writes_nothing(make_client, tmp_path):
    serve(FILES, corrupt="src/App.tsx")
    dest = tmp_path / "out"
    async with make_client() as client:
        with pytest.raises(ServerError) as info:
            await export_app(client, APP, dest)
    assert "src/App.tsx" in info.value.message
    assert not dest.exists()


@pytest.mark.parametrize("bad", ["../../.ssh/config", "/etc/passwd", "a/../../b", "", "a\\b"])
@respx.mock(assert_all_called=False)
async def test_unsafe_paths_are_rejected_before_downloading(make_client, tmp_path, bad):
    gets = serve({**FILES, bad: b"evil"})
    async with make_client() as client:
        with pytest.raises(ValidationError):
            await export_app(client, APP, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not any(route.called for route in gets)


@respx.mock
async def test_non_empty_destination_needs_force(make_client, tmp_path):
    serve(FILES)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    async with make_client() as client:
        with pytest.raises(ValidationError) as info:
            await export_app(client, APP, dest)
        assert "--force" in info.value.hint
        await export_app(client, APP, dest, force=True)
    assert (dest / "keep.txt").read_text() == "mine" and (dest / "index.html").exists()


@respx.mock(assert_all_called=False)
async def test_drive_apps_are_rejected(make_client, tmp_path):
    drive = [[[[["Old", "", "p", None, "default"]], None, None, "1" + "d" * 32]]]
    serve(FILES, apps=drive)
    async with make_client() as client:
        with pytest.raises(ValidationError):
            await export_app(client, "1ddd", tmp_path / "a")


@respx.mock
async def test_an_app_without_saved_code_is_not_found(make_client, tmp_path):
    applet = [[None, [APP, ["1", 0], ["1", 0], ["Quotes", "", "p"]]]]
    respx.post(method_url("ListApplets")).mock(
        return_value=httpx.Response(200, text=wire.encode([applet])))
    respx.post(method_url("GetApplet")).mock(
        return_value=httpx.Response(200, text=wire.encode([[APP]])))
    async with make_client() as client:
        with pytest.raises(NotFoundError):
            await export_app(client, APP, tmp_path / "b")
```

Create `tests/cli/test_app_export_cmd.py`:

```python
import json

from click.testing import CliRunner

from aistudio.cli.main import cli


def test_app_export_command(logged_in_profile, monkeypatch, tmp_path):
    logged_in_profile()
    calls = []

    async def fake(client, app_ref, dest, *, force=False):
        calls.append((app_ref, dest, force))
        return {"app_id": "a1", "title": "Quotes [v2]", "dir": str(dest), "count": 2,
                "bytes": 30, "files": [{"path": "index.html", "bytes": 13},
                                       {"path": "src/App.tsx", "bytes": 17}]}

    monkeypatch.setattr("aistudio.cli.app_cmd.export_app", fake)
    dest = tmp_path / "out"
    res = CliRunner().invoke(cli, ["app", "export", "a1", str(dest), "--force"])
    assert res.exit_code == 0, res.output
    assert "Exported 2 files" in res.stdout and "Quotes [v2]" in res.stdout
    assert calls == [("a1", dest, True)]
    js = CliRunner().invoke(cli, ["app", "export", "a1", str(dest), "--json"])
    assert json.loads(js.stdout)["count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_download.py tests/unit/test_export.py tests/cli/test_app_export_cmd.py -v`
Expected: FAIL with `AttributeError: 'HttpTransport' object has no attribute 'download'` and an `ImportError` for `get_app`.

- [ ] **Step 3: Write the implementation**

In `src/aistudio/_transport/http.py`, add this method to `HttpTransport` after `call`:

```python
    async def download(self, url: str) -> bytes:
        """GET a same-origin file with session cookies (Build app files; findings "Export")."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(".google.com"):
            raise ValidationError(f"refusing to send credentials to {parsed.scheme}://{host}")
        reloaded = False
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.get(
                    url, headers={"Cookie": self._session.cookie_header(host)}
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_attempts:
                    raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
                await self._sleep(self._delay(attempt))
                continue
            if response.status_code < 400:
                return response.content
            err = error_from_http(response.status_code, response.text)
            if isinstance(err, AuthError) and not reloaded and self._reload_session is not None:
                reloaded = True
                fresh = self._reload_session()
                if fresh != self._session:
                    self._session = fresh
                    continue
            if err.retriable and attempt < self._max_attempts:
                await self._sleep(self._delay(attempt))
                continue
            raise err
```

Append to `src/aistudio/_rpc/registry.py`:

```python
GET_APPLET = RpcSpec("GetApplet", Transport.HTTP, idempotent=True)
GET_SNAPSHOT = RpcSpec("GetCodeAssistantSnapshot", Transport.HTTP, idempotent=True)
```

Append to `src/aistudio/_rpc/params/apps.py`:

```python


def get_app(app_id: str) -> list[Any]:
    return [app_id]


def get_snapshot(app_id: str, stamp: tuple[str, int]) -> list[Any]:
    """``[app_id, null, [seconds_str, nanos]]`` (fixture GetCodeAssistantSnapshot/1)."""
    return [app_id, None, [stamp[0], stamp[1]]]
```

Append to `src/aistudio/_rpc/rows/apps.py`:

```python


def decode_snapshot_stamp(response: Any) -> tuple[str, int] | None:
    """GetApplet ``[0][6]``: the latest code snapshot time ``[seconds_str, nanos]``."""
    stamp = opt_index(response, 0, 6)
    seconds = opt_index(stamp, 0)
    if not isinstance(seconds, (str, int)) or isinstance(seconds, bool):
        return None
    nanos = opt_index(stamp, 1)
    return str(seconds), nanos if isinstance(nanos, int) and not isinstance(nanos, bool) else 0


def decode_snapshot_files(response: Any) -> list[tuple[str, str]]:
    """GetCodeAssistantSnapshot ``[["model", summary, _, ts, [[path, sha256], ...]]]``."""
    entries = opt_index(response, 0, 4)
    files: list[tuple[str, str]] = []
    for entry in entries if isinstance(entries, list) else []:
        path, digest = opt_index(entry, 0), opt_index(entry, 1)
        if isinstance(path, str) and isinstance(digest, str):
            files.append((path, digest))
    return files
```

In `src/aistudio/client.py`:
- add `decode_snapshot_files, decode_snapshot_stamp` to the `rows.apps` import;
- add these methods to `_Apps`:

```python
    async def snapshot_files(self, app_id: str) -> list[tuple[str, str]] | None:
        stamp = decode_snapshot_stamp(
            await self._client.call(registry.GET_APPLET, apps_params.get_app(app_id))
        )
        if stamp is None:
            return None
        raw = await self._client.call(registry.GET_SNAPSHOT,
                                      apps_params.get_snapshot(app_id, stamp))
        return decode_snapshot_files(raw)

    async def download_file(self, app_id: str, sha256: str) -> bytes:
        return await self._client.download(
            f"https://aistudio.google.com/_/upload/{app_id}/file/{sha256}"
        )
```

- add this method to `AIStudioClient`, after `call`:

```python
    async def download(self, url: str) -> bytes:
        return await self._transport.download(url)
```

In `src/aistudio/_app/apps.py`:
- add `import asyncio`, `import hashlib`, `from pathlib import Path, PurePosixPath` and
  `from aistudio.exceptions import NotFoundError, ServerError, ValidationError` (merged with the
  existing exceptions import);
- append:

```python
_PARALLEL_DOWNLOADS = 6


def _safe_relative(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if (not path or "\\" in path or rel.is_absolute() or any(p in ("..", "") for p in rel.parts)
            or rel == PurePosixPath(".")):
        raise ValidationError(f"unsafe file path in the app snapshot: {path!r}",
                              hint="nothing was downloaded or written")
    return rel


async def export_app(client: AIStudioClient, app_ref: str, dest: Path, *,
                     force: bool = False) -> dict[str, Any]:
    """Download a Build app's latest code into ``dest``, verified by SHA-256 (findings)."""
    app = pick(await client.apps.list(), lambda a: a.id, app_ref, noun="app",
               hint='see "aistudio app list"')
    if app.storage != "applet":
        raise ValidationError(f"{app.title or app.id!r} is stored in Google Drive (an older Build "
                              "app); aistudio can only export Build-store apps",
                              hint="open it at https://aistudio.google.com/apps")
    files = await client.apps.snapshot_files(app.id)
    if not files:
        raise NotFoundError(f"app {app.id} has no saved code yet",
                            hint=f'build it first: aistudio app prompt {app.id[:8]} "…"')
    targets = [(_safe_relative(path), digest) for path, digest in files]
    if dest.exists() and any(dest.iterdir()) and not force:
        raise ValidationError(f"{dest} is not empty",
                              hint="choose an empty folder or pass --force to overwrite files")
    limit = asyncio.Semaphore(_PARALLEL_DOWNLOADS)

    async def fetch(rel: PurePosixPath, digest: str) -> bytes:
        async with limit:
            data = await client.apps.download_file(app.id, digest)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ServerError(f"{rel}: downloaded content does not match its SHA-256",
                              retriable=True, hint="re-run the export; nothing was written")
        return data

    contents = await asyncio.gather(*(fetch(rel, digest) for rel, digest in targets))
    for (rel, _), data in zip(targets, contents):
        target = dest.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {
        "app_id": app.id,
        "title": app.title,
        "dir": str(dest),
        "count": len(targets),
        "bytes": sum(len(d) for d in contents),
        "files": [{"path": str(rel), "bytes": len(d)} for (rel, _), d in zip(targets, contents)],
    }
```

In `src/aistudio/cli/app_cmd.py`:
- add `from pathlib import Path` to the imports;
- change `from aistudio._app.apps import gallery_get, gallery_list, get_app, list_apps, recent_apps`
  to also import `export_app` (sorted);
- append:

```python


@app.command("export")
@click.argument("app_id")
@click.argument("dest", type=click.Path(file_okay=False, path_type=Path))
@click.option("--force", is_flag=True, help="Write into a non-empty folder (overwrites files).")
@json_option
@click.pass_obj
@handle_errors
def app_export(state: CliState, app_id: str, dest: Path, force: bool, json_output: bool) -> None:
    """Download an app's latest code into DEST (verified; no browser)."""
    data = _run(state, lambda c: export_app(c, app_id, dest, force=force))
    emit(data, json_output=json_output, render=lambda d: stdout.print(
        f"Exported {d['count']} files ({d['bytes']} bytes) of {d['title'] or d['app_id']} "
        f"to {d['dir']}", markup=False))
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio tests
git commit -m "feat: app export downloads a Build app's code, verified by SHA-256"
```

---

### Task 4: Live checks, README, skill

**Files:**
- Create: `tests/live/test_live_quick_wins.py`
- Modify: `README.md`, `integrations/claude-code/aistudio/SKILL.md` (then copy it to `~/.claude/skills/aistudio/SKILL.md`)

- [ ] **Step 1: Write the live test**

Create `tests/live/test_live_quick_wins.py`:

```python
"""Opt-in: `uv run pytest -m live tests/live/test_live_quick_wins.py` (no generation, no quota).

Stars then unstars a model it finds unstarred (restoring the original state) and exports the
first Build app that has code into a temporary folder.
"""

import os

import pytest

from aistudio._app.apps import export_app
from aistudio._app.models import star_model
from aistudio.client import AIStudioClient
from aistudio.paths import get_profile, resolve_profile_name

pytestmark = pytest.mark.live


@pytest.fixture
def profile():
    p = get_profile(resolve_profile_name(os.environ.get("AISTUDIO_PROFILE")))
    if not p.storage_state.exists():
        pytest.skip('no logged-in profile; run "aistudio login"')
    return p


async def test_live_status_star_roundtrip_and_export(profile, tmp_path):
    async with AIStudioClient.from_profile(profile) as client:
        assert await client.status.incidents()
        before = {m for m, _ in await client.models.starred()}
        target = next(m.id for m in await client.models.list() if m.id not in before)
        assert (await star_model(client, profile, target))["changed"]
        assert target in {m for m, _ in await client.models.starred()}
        assert (await star_model(client, profile, target, starred=False))["changed"]
        assert {m for m, _ in await client.models.starred()} == before
        apps = [a for a in await client.apps.list() if a.storage == "applet"]
        for app in apps:
            if await client.apps.snapshot_files(app.id):
                result = await export_app(client, app.id, tmp_path / "export")
                assert result["count"] > 0
                break
```

- [ ] **Step 2: Run it and try the commands**

Run: `uv run pytest -q 2>&1 | tail -1`, then `uv run pytest -m live tests/live/test_live_quick_wins.py -v 2>&1 | tail -3`
Expected: the default suite passes; the live test gives `1 passed`. It uses no quota and puts the
stars back as they were.

Then run `aistudio status`, `aistudio models list --starred` and
`aistudio app export d6d4 /tmp/aistudio-export-demo --json`.

- [ ] **Step 3: Update the README and the skill**

In `README.md`, add these lines to the Quick start block, before the `dev capture` line:

```bash
uv run aistudio status                      # AI Studio / Gemini API incidents
uv run aistudio models star lyria-3.5       # stars sync with the AI Studio picker
uv run aistudio app export <id-prefix> ./my-app   # download an app's code (verified)
```

In `integrations/claude-code/aistudio/SKILL.md`, change "Exporting Build apps' code." under
"Not available yet" to "Chatting with a model requires a Gemini API key; not supported.", and
delete the separate `- Chatting with a model (\`ask\`).` bullet. Then add these three rows to the
commands table:

```markdown
| Service incidents | `aistudio status --json [--active]` |
| Star / unstar a model | `aistudio models star <id> --json`, `aistudio models unstar <id> --json`; `models list --starred` |
| Download an app's code | `aistudio app export <app-id-or-prefix> <dir> --json` (refuses a non-empty dir unless `--force`) |
```

Copy the result: `cp integrations/claude-code/aistudio/SKILL.md ~/.claude/skills/aistudio/SKILL.md`

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_live_quick_wins.py README.md integrations/claude-code/aistudio/SKILL.md
git commit -m "docs: live checks and docs for status, stars and app export"
```
