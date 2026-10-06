# aistudio-py M2: HTTP Backend, Model Catalog, Prompt List, Token Count — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the M1 recordings into working read commands over plain HTTP:

- `models list/get`, driven by the live catalog;
- `prompt list`;
- `count-tokens`;
- `auth check --test`.

The HTTP transport also gains the spec's retry and auth-reload behaviour.

**Architecture:**

- **Wire layer.** Each RPC gets:
  - a pure **params** builder (`_rpc/params/`);
  - a **rows** decoder (`_rpc/rows/`) with a documented index table;
  - an `RpcSpec` in `_rpc/registry.py` that records the transport M1 proved.

  The builders and decoders are pinned by golden tests against `tests/fixtures/`.
- **`AIStudioClient`** (`client.py`) exposes the `.models`, `.prompts` and `.chat` namespaces over `HttpTransport`.
- **`_app/`** holds the use cases: catalog cache, filters and probes.
- **The CLI** stays thin.

**Tech Stack:** Python ≥3.10, click, httpx, rich, filelock, pytest, pytest-asyncio, respx, ruff (unchanged).

**Spec:** `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`
**Evidence:** `docs/wire/findings.md`, plus the fixtures under `tests/fixtures/`.

**Scope:**

- **Implements spec:**
  - §5.1 (registry);
  - §5.2 and §9 (retry and auth reload);
  - §4.4 step 1 (reload cookies from disk);
  - §6.2 catalog subset (`models list --category/--search/--refresh`, `models get`, the category → command map, a 1-hour cache);
  - §6.3 `prompt list` and `count-tokens`;
  - §4.2 `auth check --test`.
- **Deferred.** M1 recorded no traffic for these, so writing them now would mean guessing the format:
  - `prompt get/create/rename/delete/export/import`;
  - `use`/`status`;
  - model `badges`/"Paid", `starred`, `--paid/--free/--starred`, `models star/unstar` (findings H8/H10);
  - `count-tokens -f` attachments (H6);
  - `RotateCookies` keepalive and browser re-auth (§4.4 steps 2–3; the account has no `__Secure-1PSIDTS` cookie to rotate);
  - everything in M3–M5.

  The next capture session records them.
- **Spec deviations, decided here:**
  - **Pricing shape.** Model pricing is `pricing: [{direction, label, price_usd}]` plus
    `pricing_summary` and `pricing_url`, instead of `{label, unit, price}`. The unit only exists
    inside the label text ("*per second of output", "*Output per song").
  - **Prompt types.** `prompt list --type` takes `chat|video`, the two types observed
    (`CHUNKED_PROMPT` and `VEO_PROMPT`), instead of `chat|structured`.

## Global Constraints

- Every Global Constraint of `docs/superpowers/plans/2026-10-06-aistudio-foundation-discovery.md` still holds:
  - Python ≥3.10;
  - core dependencies click/httpx/rich/filelock only;
  - 0600 credential files;
  - the `--json` envelope `{"error": true, "code", "message", "hint"?}`;
  - exit codes 0/1/2/130;
  - ruff line length 100;
  - credentials only to `https` `*.google.com`/`*.googleapis.com`.
- **Decoder errors.** Decoders raise `SchemaDriftError` only for **required** fields (a model's name,
  a prompt's name, the top-level lists, a non-integer token total). Every other field is read with
  `opt_index` and becomes `None`, `()` or `[]` when absent or malformed.
- **Retries (spec §9):**
  - A connect failure (no request sent) is retried for any RPC.
  - Other retriable errors (429, 503, gRPC 8 without "quota", 13, 14) are retried only for
    `RpcSpec.idempotent` RPCs.
  - At most **3 sends** in total, with backoff `0.5 s × 2^(attempt−1)` plus jitter `0–0.5 s`.
- **Auth reload (spec §4.4 step 1):** one reload of the session from disk per call. The request
  is resent only if the reloaded session differs from the current one.
- **Category codes** (inferred in M1; nine codes, one per picker chip):

  | Code | 3 | 10 | 13 | 14 | 15 | 16 | 17 | 18 | 19 |
  |---|---|---|---|---|---|---|---|---|---|
  | Category | gemma | featured | audio | video | images | music | gemini | live | agents |

  - Unknown codes surface as `"code:<n>"`.
  - Category display order: featured, gemini, gemma, images, video, audio, music, live, agents.
- **Category → command,** first match wins:

  | Category present | Command |
  |---|---|
  | agents | `agent run` |
  | live | `live` |
  | video | `generate video` |
  | music | `generate music` |
  | images | `generate image` |
  | audio | `transcribe` if the id contains "transcribe", else `generate speech` |
  | gemini or gemma | `ask` |
  | none of the above | `None` (`unsupported_category: true`) |

- **Dates:**
  - Model release dates (`YYYY-MM-DD`) and knowledge cutoffs (`YYYY-MM`) are rendered in the
    **local timezone**, as the picker shows them. Decoders take a `tz` argument so tests can pin UTC.
  - Prompt `modified` is ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`.
- **Model ids** are normalised by stripping a leading `models/` and lower-casing.
- **Default model for `count-tokens`:** `gemini-flash-latest`, an alias present in the catalog.
- **The catalog cache** lives in `profiles/<p>/models.json` as `{"fetched_at": <epoch s>, "response": <decoded ListModels>}` with a TTL of 3600 s.
- **These commands are HTTP-only.** `--backend browser` gives `VALIDATION_ERROR`.

## Review Focus

1. **A catalog row missing optional blocks.** No card `[38]`, a non-list pricing block, an epoch
   that isn't a number, an unknown category code: the model is still listed with nulls, never a
   crash or `SCHEMA_DRIFT`. Test in Task 2.
2. **A stale or corrupt `models.json`.** Invalid JSON, wrong shape, a future `fetched_at`, or a
   cached response that no longer decodes: treated as a cache miss and refetched. Test in Task 3.
3. **Auth failure after the user re-ran `aistudio login` in another terminal.** One reload from
   disk and a resend succeeds. If the cookies are unchanged: one request, then `AUTH_ERROR` with
   the login hint, never a loop. Test in Task 1.
4. **Retry storms.** A non-idempotent call that gets a 503 is sent exactly once. An idempotent
   call is sent at most 3 times. Quota errors are never retried. Test in Task 1.
5. **User-typed model ids** like `models/Gemini-3.1-Flash-Lite-Image` or `LYRIA-3.5` resolve to
   the catalog id. An empty `count-tokens` text is a `VALIDATION_ERROR`, not a server round-trip.
   Tests in Tasks 3 and 5.

---

## File Structure

```
src/aistudio/
  exceptions.py              + ConnectFailedError
  _transport/http.py         retry + auth reload in call(); ConnectFailedError from post_raw
  _rpc/wire.py               + opt_index
  _rpc/registry.py           Transport, RpcSpec, LIST_MODELS, LIST_PROMPTS, COUNT_TOKENS
  _rpc/params/{__init__,models,prompts,generate}.py
  _rpc/rows/{__init__ (as_str/as_int), models, prompts, generate}.py
  _types/{__init__,models,prompts,chat}.py
  client.py                  AIStudioClient (.models, .prompts, .chat)
  _app/models.py             catalog cache, filters, normalize_model_id, list/get
  _app/prompts.py            list with type filter
  _app/chat.py               count_tokens use case
  _app/auth.py               + http_probe
  cli/state.py               + CliState.require_http()
  cli/models_cmd.py, cli/prompt_cmd.py, cli/chat_cmd.py
  cli/session_cmd.py         auth check --test
  cli/main.py                register models, prompt, count-tokens
tests/conftest.py            + captured, make_client fixtures
tests/unit/*, tests/cli/*, tests/live/test_live_http.py
```

---

### Task 1: Transport retries and auth reload

**Files:**
- Modify: `src/aistudio/exceptions.py`, `src/aistudio/_transport/http.py`
- Test: `tests/unit/test_http_retry.py`

**Interfaces:**
- Consumes:
  - `Session` (dataclass equality compares cookies, `api_key` and `authuser`);
  - `error_for_response`, `method_url`, `wire.encode/decode`;
  - `AuthError`, `NetworkError`.
- Produces:
  - `exceptions.ConnectFailedError(NetworkError)`, with code `NETWORK_ERROR` and retriable.
  - `HttpTransport(session, *, client=None, timeout=60.0, reload_session=None, max_attempts=3, backoff=0.5, sleep=asyncio.sleep, jitter=random.random)`.
  - `async HttpTransport.call(method, body, *, idempotent=False) -> Any`.
  - `post_raw` raises `ConnectFailedError` for `httpx.ConnectError`/`ConnectTimeout`, and `NetworkError` for other transport errors.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_http_retry.py`:

```python
import httpx
import pytest
import respx

from aistudio._auth.session import Session
from aistudio._transport.http import HttpTransport, method_url
from aistudio.exceptions import (
    AuthError,
    NetworkError,
    QuotaExceededError,
    ServerError,
)

URL = method_url("ListModels")


def session(value="s"):
    return Session(cookies=[{"name": "SAPISID", "value": value, "domain": ".google.com",
                             "expires": -1}])


def transport(**kwargs):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    t = HttpTransport(kwargs.pop("session", session()), sleep=fake_sleep, jitter=lambda: 0.0,
                      **kwargs)
    return t, delays


@respx.mock
async def test_idempotent_call_retries_retriable_errors_then_succeeds():
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(503), httpx.Response(503), httpx.Response(200, text="[1]"),
    ])
    t, delays = transport()
    async with t:
        assert await t.call("ListModels", [], idempotent=True) == [1]
    assert route.call_count == 3
    assert delays == [0.5, 1.0]


@respx.mock
async def test_idempotent_call_gives_up_after_three_sends():
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    t, _ = transport()
    async with t:
        with pytest.raises(ServerError):
            await t.call("ListModels", [], idempotent=True)
    assert route.call_count == 3


@respx.mock
async def test_non_idempotent_call_is_sent_once_on_server_error():
    route = respx.post(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200)])
    t, _ = transport()
    async with t:
        with pytest.raises(ServerError):
            await t.call("ListModels", [])
    assert route.call_count == 1


@respx.mock
async def test_quota_errors_are_never_retried():
    route = respx.post(URL).mock(
        return_value=httpx.Response(429, text='[,[8,"Quota exceeded for model"]]')
    )
    t, _ = transport()
    async with t:
        with pytest.raises(QuotaExceededError):
            await t.call("ListModels", [], idempotent=True)
    assert route.call_count == 1


@respx.mock
async def test_connect_failure_is_retried_even_when_not_idempotent():
    route = respx.post(URL).mock(side_effect=[httpx.ConnectError("down"),
                                              httpx.Response(200, text="[]")])
    t, _ = transport()
    async with t:
        assert await t.call("CreatePrompt", []) == []
    assert route.call_count == 2


@respx.mock
async def test_read_error_is_not_retried_when_not_idempotent():
    route = respx.post(URL).mock(side_effect=httpx.ReadError("cut"))
    t, _ = transport()
    async with t:
        with pytest.raises(NetworkError):
            await t.call("ListModels", [])
    assert route.call_count == 1


@respx.mock
async def test_auth_error_reloads_session_once_and_resends():
    route = respx.post(URL).mock(side_effect=[httpx.Response(401), httpx.Response(200, text="[]")])
    t, _ = transport(reload_session=lambda: session("fresh"))
    async with t:
        assert await t.call("ListModels", []) == []
    assert route.call_count == 2
    assert "SAPISID=fresh" in route.calls[1].request.headers["cookie"]


@respx.mock
async def test_auth_error_with_unchanged_session_raises_after_one_request():
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    t, _ = transport(reload_session=lambda: session())
    async with t:
        with pytest.raises(AuthError) as info:
            await t.call("ListModels", [], idempotent=True)
    assert route.call_count == 1
    assert info.value.hint == 'run "aistudio login"'


@respx.mock
async def test_auth_reload_happens_only_once_per_call():
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    versions = iter(["a", "b", "c"])
    t, _ = transport(reload_session=lambda: session(next(versions)))
    async with t:
        with pytest.raises(AuthError):
            await t.call("ListModels", [])
    assert route.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_http_retry.py -v`
Expected: FAIL with `TypeError: HttpTransport.__init__() got an unexpected keyword argument 'sleep'`

- [ ] **Step 3: Write the implementation**

In `src/aistudio/exceptions.py`, add after `class NetworkError`:

```python
class ConnectFailedError(NetworkError):
    """The connection failed before the request was sent; always safe to retry."""
```

Replace `src/aistudio/_transport/http.py` with:

```python
"""Plain-HTTP backend for MakerSuite RPCs that BotGuard does not protect (spec §5.2)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from aistudio._auth.session import Session
from aistudio._auth.signer import API_HOST, build_headers
from aistudio._rpc import wire
from aistudio.exceptions import (
    AIStudioError,
    AuthError,
    ConnectFailedError,
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
        reload_session: Callable[[], Session] | None = None,
        max_attempts: int = 3,
        backoff: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._session = session
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._reload_session = reload_session
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._sleep = sleep
        self._jitter = jitter

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
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ConnectFailedError(f"{type(exc).__name__}: {exc}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

    async def call(self, method: str, body: Any, *, idempotent: bool = False) -> Any:
        """Send one RPC (spec §9 retries, §4.4 step 1 auth reload)."""
        text = wire.encode(body)
        reloaded = False
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self.post_raw(method_url(method), text)
            except ConnectFailedError:
                if attempt >= self._max_attempts:
                    raise
                await self._sleep(self._delay(attempt))
                continue
            except NetworkError:
                if not idempotent or attempt >= self._max_attempts:
                    raise
                await self._sleep(self._delay(attempt))
                continue
            if response.status_code < 400:
                return wire.decode(response.text)
            err = error_for_response(response.status_code, response.text)
            if isinstance(err, AuthError) and not reloaded and self._reload_session is not None:
                reloaded = True
                fresh = self._reload_session()
                if fresh != self._session:
                    # Another process (e.g. `aistudio login`) refreshed the cookies on disk.
                    self._session = fresh
                    continue
            if idempotent and err.retriable and attempt < self._max_attempts:
                await self._sleep(self._delay(attempt))
                continue
            raise err

    def _delay(self, attempt: int) -> float:
        return self._backoff * 2 ** (attempt - 1) + self._jitter() * self._backoff
```

In `tests/unit/test_http_transport.py`, `test_transport_failures_become_network_error` now goes
through real connect-failure retries, sleeping about 2 s. Keep it fast by changing its
`HttpTransport(make_session())` to `HttpTransport(make_session(), max_attempts=1)`. The test still
checks that transport errors become `NetworkError`.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -v 2>&1 | tail -3 && uv run ruff check .`
Expected: every test PASSES, including the existing `tests/unit/test_http_transport.py`; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/exceptions.py src/aistudio/_transport/http.py tests/unit/test_http_retry.py tests/unit/test_http_transport.py
git commit -m "feat: retry idempotent RPCs and reload the session once on auth errors"
```

---

### Task 2: Model catalog decoding

**Files:**
- Create: `src/aistudio/_rpc/registry.py`
- Create: `src/aistudio/_rpc/params/__init__.py` (empty), `src/aistudio/_rpc/params/models.py`
- Create: `src/aistudio/_rpc/rows/__init__.py`, `src/aistudio/_rpc/rows/models.py`
- Create: `src/aistudio/_types/__init__.py` (empty), `src/aistudio/_types/models.py`
- Modify: `src/aistudio/_rpc/wire.py` (add `opt_index`), `tests/conftest.py` (add `captured`)
- Test: `tests/unit/test_rows_models.py`, `tests/unit/test_registry.py`

**Interfaces:**
- Consumes: `wire.safe_index`, `SchemaDriftError`
- Produces:
  - `wire.opt_index(value, *path) -> Any`
  - `rows.as_str(v) -> str | None` and `rows.as_int(v) -> int | None`
  - `registry.Transport` (`HTTP`, `BROWSER`), `registry.RpcSpec(method, transport, idempotent)`, `registry.LIST_MODELS`, `LIST_PROMPTS`, `COUNT_TOKENS`
  - `params.models.list_models() -> list`
  - `_types.models.CATEGORY_ORDER`, `PricingRow(direction, label, price_usd)`, and `command_for(model_id, categories) -> str | None`
  - `Model(id, display_name, description, categories, command, version=None, input_token_limit=None, output_token_limit=None, methods=(), pricing=(), pricing_summary=None, pricing_url=None, docs_url=None, release_date=None, knowledge_cutoff=None)`, with `.unsupported_category` and `.to_dict()`
  - `rows.models.CATEGORY_CODES` and `rows.models.decode_models(response, *, tz=None) -> list[Model]`
  - The pytest fixture `captured(method, n=1)`, which returns an object with `.request` and `.response` (decoded) and `.raw` (the fixture dict).

- [ ] **Step 1: Add the fixture loader to `tests/conftest.py`**

Add `import pathlib` and `from types import SimpleNamespace` to the imports at the top, and add
`from aistudio._rpc import wire` next to the other `aistudio` imports. Then append:

```python
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def captured():
    """Load an M1 capture fixture: decoded request/response plus the raw record."""

    def load(method, n=1):
        record = json.loads((FIXTURES / method / f"{n}.json").read_text(encoding="utf-8"))
        return SimpleNamespace(
            request=wire.decode(record["request"]["body"]),
            response=wire.decode(record["response"]["body"]),
            raw=record,
        )

    return load
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_registry.py`:

```python
from aistudio._rpc import registry


def test_m1_proved_these_rpcs_work_over_plain_http():
    for spec in (registry.LIST_MODELS, registry.LIST_PROMPTS, registry.COUNT_TOKENS):
        assert spec.transport is registry.Transport.HTTP
        assert spec.idempotent is True
    assert registry.LIST_MODELS.method == "ListModels"
    assert registry.LIST_PROMPTS.method == "ListPrompts"
    assert registry.COUNT_TOKENS.method == "CountTokens"
```

Create `tests/unit/test_rows_models.py`:

```python
from datetime import timezone

import pytest

from aistudio._rpc import wire
from aistudio._rpc.params.models import list_models
from aistudio._rpc.rows import as_int, as_str
from aistudio._rpc.rows.models import decode_models
from aistudio._types.models import Model, PricingRow
from aistudio.exceptions import SchemaDriftError


def catalog(captured):
    return {m.id: m for m in decode_models(captured("ListModels").response, tz=timezone.utc)}


def test_list_models_request_matches_capture(captured):
    assert list_models() == captured("ListModels").request


def test_decodes_every_captured_model_with_known_categories(captured):
    models = decode_models(captured("ListModels").response, tz=timezone.utc)
    assert len(models) == 42
    assert all(m.categories for m in models)
    assert not [c for m in models for c in m.categories if c.startswith("code:")]


def test_lyria_matches_the_picker_card(captured):
    m = catalog(captured)["lyria-3.5"]
    assert m.display_name == "Lyria 3.5"
    assert m.categories == ("audio", "music")
    assert m.command == "generate music"
    assert m.pricing == (PricingRow("output", "Song generation (*Output per song)", 0.08),)
    assert m.release_date == "2026-09-03"
    assert m.knowledge_cutoff == "2025-01"
    assert m.description.startswith("Our advanced, full-song generative model")
    assert m.docs_url == "https://aistudio.google.com/docs/music-generation"
    assert m.methods == ("generateContent", "countTokens")
    assert (m.input_token_limit, m.output_token_limit) == (1048576, 65536)


def test_per_modality_pricing_rows(captured):
    m = catalog(captured)["gemini-3.1-flash-lite"]
    assert m.categories == ("featured", "gemini")
    assert m.command == "ask"
    assert m.pricing == (
        PricingRow("input", "Text, image and video", 0.25),
        PricingRow("input", "Audio", 0.5),
        PricingRow("output", "Text, image and video", 1.5),
        PricingRow("output", "Audio", 1.5),
    )
    assert m.release_date == "2026-05-07"


def test_summary_pricing(captured):
    m = catalog(captured)["gemini-3.8-flash"]
    assert m.pricing == ()
    assert m.pricing_summary == (
        "All context lengths * input: $0.75 / output: $3.75 through December 31, 2026"
    )
    assert m.pricing_url == "https://aistudio.google.com/docs/pricing#gemini-3.8-flash"
    assert m.knowledge_cutoff is None


@pytest.mark.parametrize(
    "model_id,categories,command",
    [
        ("veo-3.1-generate-preview", ("video",), "generate video"),
        ("antigravity-preview-09-2026", ("agents",), "agent run"),
        ("gemma-4-31b-it", ("gemma",), "ask"),
        ("gemini-3.5-transcribe", ("featured", "gemini", "audio"), "transcribe"),
        ("gemini-3.5-transcribe-live", ("featured", "audio", "live"), "live"),
        ("gemini-3.8-flash-tts", ("featured", "audio"), "generate speech"),
        ("gemini-3.1-flash-image", ("featured", "gemini", "images"), "generate image"),
    ],
)
def test_categories_map_to_commands(captured, model_id, categories, command):
    m = catalog(captured)[model_id]
    assert (m.categories, m.command) == (categories, command)


def test_sparse_and_malformed_rows_decode_with_nulls():
    card = [None] * 21
    card[0], card[19], card[20] = "not-a-pricing-block", ["not-a-number"], "x"
    odd = [None] * 75
    odd[0], odd[3], odd[5], odd[7], odd[38], odd[74] = (
        "models/odd", "Odd", "big", "not-a-list", card, [999],
    )
    plain, weird = decode_models([[["models/plain"], odd]], tz=timezone.utc)
    assert plain == Model(id="plain", display_name=None, description=None, categories=(),
                          command=None)
    assert weird.display_name == "Odd"
    assert weird.input_token_limit is None
    assert weird.methods == ()
    assert weird.pricing == ()
    assert weird.release_date is None and weird.knowledge_cutoff is None
    assert weird.categories == ("code:999",)
    assert weird.command is None and weird.unsupported_category


@pytest.mark.parametrize("response", [[], [["oops"]], [[[None]]], [[[42]]]])
def test_missing_required_fields_raise_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_models(response)


def test_to_dict_is_json_ready(captured):
    data = catalog(captured)["lyria-3.5"].to_dict()
    assert data["categories"] == ["audio", "music"]
    assert data["pricing"] == [
        {"direction": "output", "label": "Song generation (*Output per song)", "price_usd": 0.08}
    ]
    assert data["unsupported_category"] is False


def test_opt_index_and_coercions():
    assert wire.opt_index([1, [2, 3]], 1, 0) == 2
    assert wire.opt_index([1], 5) is None
    assert wire.opt_index("x", 0) is None
    assert (as_str("a"), as_str(1)) == ("a", None)
    assert (as_int(3), as_int(True), as_int("3")) == (3, None, None)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rows_models.py tests/unit/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.registry'` (or `…params`)

- [ ] **Step 4: Write the implementation**

Append to `src/aistudio/_rpc/wire.py`:

```python


def opt_index(value: Any, *path: int) -> Any:
    """Like ``safe_index`` for optional fields: None when the path does not exist."""
    current = value
    for index in path:
        if not isinstance(current, list) or not -len(current) <= index < len(current):
            return None
        current = current[index]
    return current
```

Create `src/aistudio/_rpc/registry.py`:

```python
"""Every MakerSuite RPC the client sends, with the transport proved in M1 (spec §5.1).

Transports come from `aistudio dev replay` results recorded in docs/wire/findings.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Transport(enum.Enum):
    HTTP = "http"
    BROWSER = "browser"


@dataclass(frozen=True)
class RpcSpec:
    method: str
    transport: Transport
    idempotent: bool


LIST_MODELS = RpcSpec("ListModels", Transport.HTTP, idempotent=True)
LIST_PROMPTS = RpcSpec("ListPrompts", Transport.HTTP, idempotent=True)
COUNT_TOKENS = RpcSpec("CountTokens", Transport.HTTP, idempotent=True)
```

Create `src/aistudio/_rpc/params/__init__.py` as an empty file.

Create `src/aistudio/_rpc/params/models.py`:

```python
"""``ListModels`` request builder (fixtures ListModels/1-2)."""

from __future__ import annotations

from typing import Any


def list_models() -> list[Any]:
    """An empty request message."""
    return []
```

Create `src/aistudio/_rpc/rows/__init__.py`:

```python
"""Positional response decoders. Shared coercions for optional fields."""

from __future__ import annotations

from typing import Any


def as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
```

Create `src/aistudio/_types/__init__.py` as an empty file.

Create `src/aistudio/_types/models.py`:

```python
"""Model catalog types (spec §6.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# The picker's filter chips, in display order ("All" is no filter).
CATEGORY_ORDER = (
    "featured", "gemini", "gemma", "images", "video", "audio", "music", "live", "agents",
)


@dataclass(frozen=True)
class PricingRow:
    direction: str  # "input" | "output"
    label: str  # the unit is part of the label, e.g. "(*per second of output)"
    price_usd: float | None


@dataclass(frozen=True)
class Model:
    id: str  # without the "models/" prefix
    display_name: str | None
    description: str | None
    categories: tuple[str, ...]
    command: str | None
    version: str | None = None
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    methods: tuple[str, ...] = ()
    pricing: tuple[PricingRow, ...] = ()
    pricing_summary: str | None = None
    pricing_url: str | None = None
    docs_url: str | None = None
    release_date: str | None = None  # YYYY-MM-DD in the local timezone, as the picker shows
    knowledge_cutoff: str | None = None  # YYYY-MM

    @property
    def unsupported_category(self) -> bool:
        return self.command is None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["categories"] = list(self.categories)
        data["methods"] = list(self.methods)
        data["pricing"] = [asdict(p) for p in self.pricing]
        data["unsupported_category"] = self.unsupported_category
        return data


def command_for(model_id: str, categories: tuple[str, ...]) -> str | None:
    """The CLI command that runs a model of these categories (first match wins)."""
    cats = set(categories)
    if "agents" in cats:
        return "agent run"
    if "live" in cats:
        return "live"
    if "video" in cats:
        return "generate video"
    if "music" in cats:
        return "generate music"
    if "images" in cats:
        return "generate image"
    if "audio" in cats:
        return "transcribe" if "transcribe" in model_id else "generate speech"
    if cats & {"gemini", "gemma"}:
        return "ask"
    return None
```

Create `src/aistudio/_rpc/rows/models.py`:

```python
"""Decode ``ListModels`` responses (fixtures ListModels/1-2, docs/wire/findings.md H8).

Response: ``[[model, ...]]``. Model row:

| index | field |
|---|---|
| 0 | "models/<id>" (required) |
| 2 | version |
| 3 | display name |
| 4 | short description |
| 5, 6 | input, output token limit |
| 7 | supported methods, e.g. ["generateContent", "countTokens"] |
| 38 | card: [0] pricing, [9] description, [15] docs URL, [19][0] release epoch s, |
|    | [20][0] knowledge-cutoff epoch s |
| 74 | picker category codes (CATEGORY_CODES) |

Pricing ``card[0]``: ``[input_groups, output_groups, _, summary_text, pricing_url]``; a group
is a list of rows ``[_, _, price, _, label, per_item_price]``.
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

from aistudio._rpc.rows import as_int, as_str
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.models import CATEGORY_ORDER, Model, PricingRow, command_for
from aistudio.exceptions import SchemaDriftError

METHOD = "ListModels"
# Inferred in M1: the nine codes seen map one-to-one onto the nine picker chips.
CATEGORY_CODES = {
    3: "gemma", 10: "featured", 13: "audio", 14: "video", 15: "images",
    16: "music", 17: "gemini", 18: "live", 19: "agents",
}


def decode_models(response: Any, *, tz: tzinfo | None = None) -> list[Model]:
    rows = safe_index(response, 0, method=METHOD, expected="the model list")
    if not isinstance(rows, list):
        raise SchemaDriftError(METHOD, (0,), "the model list")
    return [_model(response, i, tz) for i in range(len(rows))]


def _model(response: Any, i: int, tz: tzinfo | None) -> Model:
    name = safe_index(response, 0, i, 0, method=METHOD, expected="a model name")
    if not isinstance(name, str):
        raise SchemaDriftError(METHOD, (0, i, 0), "a model name")
    row = response[0][i]
    card = opt_index(row, 38)
    model_id = name.removeprefix("models/")
    categories = _categories(opt_index(row, 74))
    pricing, summary, pricing_url = _pricing(opt_index(card, 0))
    release = _epoch(opt_index(card, 19))
    cutoff = _epoch(opt_index(card, 20))
    methods = opt_index(row, 7)
    return Model(
        id=model_id,
        display_name=as_str(opt_index(row, 3)),
        description=as_str(opt_index(card, 9)) or as_str(opt_index(row, 4)),
        categories=categories,
        command=command_for(model_id, categories),
        version=as_str(opt_index(row, 2)),
        input_token_limit=as_int(opt_index(row, 5)),
        output_token_limit=as_int(opt_index(row, 6)),
        methods=tuple(m for m in methods if isinstance(m, str))
        if isinstance(methods, list) else (),
        pricing=pricing,
        pricing_summary=summary,
        pricing_url=pricing_url,
        docs_url=as_str(opt_index(card, 15)),
        release_date=release.astimezone(tz).date().isoformat() if release else None,
        knowledge_cutoff=cutoff.astimezone(tz).strftime("%Y-%m") if cutoff else None,
    )


def _categories(codes: Any) -> tuple[str, ...]:
    if not isinstance(codes, list):
        return ()
    names = {CATEGORY_CODES.get(c, f"code:{c}") for c in codes if as_int(c) is not None}
    known = [c for c in CATEGORY_ORDER if c in names]
    return tuple(known + sorted(names - set(CATEGORY_ORDER)))


def _pricing(raw: Any) -> tuple[tuple[PricingRow, ...], str | None, str | None]:
    if not isinstance(raw, list):
        return (), None, None
    rows: list[PricingRow] = []
    for index, direction in ((0, "input"), (1, "output")):
        groups = opt_index(raw, index)
        for group in groups if isinstance(groups, list) else []:
            for entry in group if isinstance(group, list) else []:
                label = as_str(opt_index(entry, 4))
                if label is None:
                    continue
                price = opt_index(entry, 2)
                if price is None:
                    price = opt_index(entry, 5)
                is_number = isinstance(price, (int, float)) and not isinstance(price, bool)
                rows.append(PricingRow(direction, label, float(price) if is_number else None))
    return tuple(rows), as_str(opt_index(raw, 3)), as_str(opt_index(raw, 4))


def _epoch(value: Any) -> datetime | None:
    raw = opt_index(value, 0) if isinstance(value, list) else value
    try:
        return datetime.fromtimestamp(int(raw), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
```

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest tests/unit/test_rows_models.py tests/unit/test_registry.py -v 2>&1 | tail -3 && uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/aistudio/_rpc src/aistudio/_types tests/conftest.py tests/unit/test_rows_models.py tests/unit/test_registry.py
git commit -m "feat: decode the ListModels catalog into typed models with categories and pricing"
```

---

### Task 3: Client, catalog cache, `models list/get`

**Files:**
- Create: `src/aistudio/client.py`, `src/aistudio/_app/models.py`, `src/aistudio/cli/models_cmd.py`
- Modify: `src/aistudio/cli/state.py` (add `require_http`), `src/aistudio/cli/main.py` (register `models`), `tests/conftest.py` (add `make_client`)
- Test: `tests/unit/test_client.py`, `tests/unit/test_app_models.py`, `tests/cli/test_models_cmd.py`

**Interfaces:**
- Consumes:
  - Task 1: `HttpTransport.call(method, body, *, idempotent)`.
  - Task 2: `registry`, `params.models.list_models`, `rows.models.decode_models`, `Model`, `CATEGORY_ORDER`.
  - `Session.load`, `write_private_json`, `read_json`, `NotFoundError`, `ValidationError`, `SchemaDriftError`.
- Produces:
  - `client.AIStudioClient(transport)` and `AIStudioClient.from_profile(profile)`, an async context manager.
  - `async AIStudioClient.call(spec, body) -> Any`, which raises `ValidationError` for non-HTTP specs.
  - `client.models.fetch_raw() -> Any` and `client.models.list(*, tz=None) -> list[Model]`.
  - `_app.models`:
    - `CACHE_TTL = 3600`;
    - `normalize_model_id(value) -> str`;
    - `async load_catalog(client, profile, *, refresh=False, now=time.time, tz=None) -> list[Model]`;
    - `filter_models(models, *, category=None, search=None) -> list[Model]`;
    - `async list_models(client, profile, *, category=None, search=None, refresh=False, now=time.time, tz=None) -> dict`;
    - `async get_model(client, profile, model_id, *, refresh=False, now=time.time, tz=None) -> dict`.
  - `CliState.require_http()`.
  - The click group `cli.models_cmd.models`.
  - The pytest fixture `make_client()`, which returns an `AIStudioClient` over a test `Session`.

- [ ] **Step 1: Add the client fixture to `tests/conftest.py`**

Append:

```python
@pytest.fixture
def make_client():
    from aistudio._auth.session import Session
    from aistudio._transport.http import HttpTransport
    from aistudio.client import AIStudioClient

    def make():
        return AIStudioClient(HttpTransport(Session(cookies=list(TEST_COOKIES))))

    return make
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_client.py`:

```python
import httpx
import pytest
import respx

from aistudio._rpc.registry import RpcSpec, Transport
from aistudio._transport.http import method_url
from aistudio.exceptions import ValidationError


@respx.mock
async def test_models_list_decodes_the_catalog(make_client, captured):
    route = respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )
    async with make_client() as client:
        models = await client.models.list()
    assert len(models) == 42
    assert route.calls.last.request.content == b"[]"


async def test_browser_only_rpcs_are_refused_until_the_browser_backend_exists(make_client):
    async with make_client() as client:
        with pytest.raises(ValidationError):
            await client.call(RpcSpec("GenerateContent", Transport.BROWSER, False), [])
```

Create `tests/unit/test_app_models.py`:

```python
import json
from datetime import timezone

import httpx
import pytest
import respx

from aistudio._app.models import (
    CACHE_TTL,
    filter_models,
    get_model,
    list_models,
    load_catalog,
    normalize_model_id,
)
from aistudio._transport.http import method_url
from aistudio.exceptions import NotFoundError
from aistudio.paths import get_profile

T0 = 1_800_000_000.0


def serve_catalog(captured):
    return respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )


@respx.mock
async def test_catalog_is_cached_for_an_hour(aistudio_home, make_client, captured):
    route = serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        await load_catalog(client, profile, now=lambda: T0)
        await load_catalog(client, profile, now=lambda: T0 + CACHE_TTL - 1)
        assert route.call_count == 1
        await load_catalog(client, profile, now=lambda: T0 + CACHE_TTL + 1)
        assert route.call_count == 2
        await load_catalog(client, profile, refresh=True, now=lambda: T0 + CACHE_TTL + 2)
        assert route.call_count == 3
    cached = json.loads(profile.models.read_text())
    assert cached["fetched_at"] == T0 + CACHE_TTL + 2


@pytest.mark.parametrize(
    "content",
    [
        "{garbage",
        '["not", "a", "dict"]',
        '{"fetched_at": "x", "response": []}',
        json.dumps({"fetched_at": T0 + 10_000, "response": [[["models/a"]]]}),
        json.dumps({"fetched_at": T0, "response": "no longer decodes"}),
    ],
)
@respx.mock
async def test_corrupt_or_stale_cache_is_a_miss(aistudio_home, make_client, captured, content):
    route = serve_catalog(captured)
    profile = get_profile("default").ensure()
    profile.models.write_text(content)
    async with make_client() as client:
        models = await load_catalog(client, profile, now=lambda: T0)
    assert route.call_count == 1
    assert len(models) == 42


@respx.mock
async def test_list_models_filters_by_category_and_search(aistudio_home, make_client, captured):
    serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        music = await list_models(client, profile, category="music", now=lambda: T0)
        veo = await list_models(client, profile, search="VEO 3.1", now=lambda: T0)
    assert sorted(m["id"] for m in music["models"]) == [
        "lyria-3-clip-preview", "lyria-3-pro-preview", "lyria-3.5",
    ]
    assert music["count"] == 3
    assert {m["id"] for m in veo["models"]} == {
        "veo-3.1-generate-preview", "veo-3.1-fast-generate-preview",
        "veo-3.1-lite-generate-preview",
    }


@respx.mock
async def test_get_model_normalizes_the_id(aistudio_home, make_client, captured):
    serve_catalog(captured)
    profile = get_profile("default")
    async with make_client() as client:
        model = await get_model(client, profile, "models/LYRIA-3.5", now=lambda: T0,
                                tz=timezone.utc)
        with pytest.raises(NotFoundError) as info:
            await get_model(client, profile, "no-such-model", now=lambda: T0)
    assert model["id"] == "lyria-3.5"
    assert model["release_date"] == "2026-09-03"
    assert info.value.hint == 'see "aistudio models list"'


def test_normalize_model_id():
    assert normalize_model_id(" models/Gemini-3.1-Flash-Lite-Image ") == (
        "gemini-3.1-flash-lite-image"
    )
    assert normalize_model_id("lyria-3.5") == "lyria-3.5"


def test_filter_models_without_filters_is_identity(captured):
    from aistudio._rpc.rows.models import decode_models

    models = decode_models(captured("ListModels").response)
    assert filter_models(models) == models
```

Create `tests/cli/test_models_cmd.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve_catalog(captured):
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )


@respx.mock
def test_models_list_by_category_json(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    result = CliRunner().invoke(cli, ["models", "list", "--category", "agents", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert {m["id"] for m in data["models"]} == {
        "antigravity-preview-09-2026", "antigravity-preview-05-2026",
        "deep-research-preview-04-2026", "deep-research-max-preview-04-2026",
    }
    assert all(m["command"] == "agent run" for m in data["models"])


@respx.mock
def test_models_list_human_output(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    result = CliRunner().invoke(cli, ["models", "list", "--category", "music"])
    assert result.exit_code == 0, result.output
    assert "lyria-3.5" in result.stdout


@respx.mock
def test_models_get_and_not_found(logged_in_profile, captured):
    logged_in_profile()
    serve_catalog(captured)
    ok = CliRunner().invoke(cli, ["models", "get", "models/LYRIA-3.5", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["display_name"] == "Lyria 3.5"
    missing = CliRunner().invoke(cli, ["models", "get", "nope", "--json"])
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["code"] == "NOT_FOUND"


def test_browser_backend_is_refused(logged_in_profile):
    logged_in_profile()
    result = CliRunner().invoke(cli, ["--backend", "browser", "models", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"


def test_models_list_requires_login(aistudio_home):
    result = CliRunner().invoke(cli, ["models", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "AUTH_ERROR"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_client.py tests/unit/test_app_models.py tests/cli/test_models_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio.client'` (and `aistudio._app.models`)

- [ ] **Step 4: Write the implementation**

Create `src/aistudio/client.py`:

```python
"""Async client for the AI Studio web app (spec §3)."""

from __future__ import annotations

from datetime import tzinfo
from typing import Any

from aistudio._auth.session import Session
from aistudio._rpc import registry
from aistudio._rpc.params import models as models_params
from aistudio._rpc.registry import RpcSpec, Transport
from aistudio._rpc.rows.models import decode_models
from aistudio._transport.http import HttpTransport
from aistudio._types.models import Model
from aistudio.exceptions import ValidationError
from aistudio.paths import Profile


class AIStudioClient:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport
        self.models = _Models(self)

    @classmethod
    def from_profile(cls, profile: Profile) -> AIStudioClient:
        session = Session.load(profile)
        return cls(HttpTransport(session, reload_session=lambda: Session.load(profile)))

    async def __aenter__(self) -> AIStudioClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._transport.aclose()

    async def call(self, spec: RpcSpec, body: Any) -> Any:
        if spec.transport is not Transport.HTTP:
            raise ValidationError(
                f"{spec.method} needs the browser backend, which is not available yet"
            )
        return await self._transport.call(spec.method, body, idempotent=spec.idempotent)


class _Models:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def fetch_raw(self) -> Any:
        return await self._client.call(registry.LIST_MODELS, models_params.list_models())

    async def list(self, *, tz: tzinfo | None = None) -> list[Model]:
        return decode_models(await self.fetch_raw(), tz=tz)
```

Create `src/aistudio/_app/models.py`:

```python
"""Model catalog use cases: 1-hour cache, filters, lookup (spec §6.2)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import tzinfo
from typing import Any

from aistudio._fs import read_json, write_private_json
from aistudio._rpc.rows.models import decode_models
from aistudio._types.models import Model
from aistudio.client import AIStudioClient
from aistudio.exceptions import NotFoundError, SchemaDriftError
from aistudio.paths import Profile

CACHE_TTL = 3600


def normalize_model_id(value: str) -> str:
    return value.strip().removeprefix("models/").lower()


def _read_cache(profile: Profile, now: float) -> Any:
    if not profile.models.exists():
        return None
    try:
        data = read_json(profile.models)
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    fetched_at = data.get("fetched_at")
    if not isinstance(fetched_at, (int, float)) or not 0 <= now - fetched_at <= CACHE_TTL:
        return None
    return data.get("response")


async def load_catalog(
    client: AIStudioClient,
    profile: Profile,
    *,
    refresh: bool = False,
    now: Callable[[], float] = time.time,
    tz: tzinfo | None = None,
) -> list[Model]:
    if not refresh:
        cached = _read_cache(profile, now())
        if cached is not None:
            try:
                return decode_models(cached, tz=tz)
            except SchemaDriftError:
                pass  # written by an older decoder or damaged: refetch
    raw = await client.models.fetch_raw()
    models = decode_models(raw, tz=tz)
    profile.ensure()
    write_private_json(profile.models, {"fetched_at": now(), "response": raw})
    return models


def filter_models(
    models: list[Model], *, category: str | None = None, search: str | None = None
) -> list[Model]:
    out = models
    if category:
        out = [m for m in out if category in m.categories]
    if search:
        needle = search.lower()
        out = [
            m for m in out
            if needle in m.id
            or needle in (m.display_name or "").lower()
            or needle in (m.description or "").lower()
        ]
    return out


async def list_models(
    client: AIStudioClient,
    profile: Profile,
    *,
    category: str | None = None,
    search: str | None = None,
    refresh: bool = False,
    now: Callable[[], float] = time.time,
    tz: tzinfo | None = None,
) -> dict[str, Any]:
    models = filter_models(
        await load_catalog(client, profile, refresh=refresh, now=now, tz=tz),
        category=category,
        search=search,
    )
    return {"models": [m.to_dict() for m in models], "count": len(models)}


async def get_model(
    client: AIStudioClient,
    profile: Profile,
    model_id: str,
    *,
    refresh: bool = False,
    now: Callable[[], float] = time.time,
    tz: tzinfo | None = None,
) -> dict[str, Any]:
    wanted = normalize_model_id(model_id)
    for model in await load_catalog(client, profile, refresh=refresh, now=now, tz=tz):
        if model.id == wanted:
            return model.to_dict()
    raise NotFoundError(f"no model {wanted!r} in the catalog", hint='see "aistudio models list"')
```

In `src/aistudio/cli/state.py`, add `from aistudio.exceptions import ValidationError` to the imports
and this method to `CliState`:

```python
    def require_http(self) -> None:
        """These commands run over plain HTTP; the browser backend is not available yet."""
        if self.backend == "browser":
            raise ValidationError(
                "--backend browser is not available for this command yet",
                hint="drop --backend or use --backend http",
            )
```

Create `src/aistudio/cli/models_cmd.py`:

```python
"""``aistudio models``: the AI Studio model picker as a catalog (spec §6.2)."""

from __future__ import annotations

import asyncio
from typing import Any

import click
from rich.table import Table

from aistudio._app.models import get_model, list_models
from aistudio._types.models import CATEGORY_ORDER
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


@click.group()
def models() -> None:
    """Browse the model catalog (the AI Studio model picker)."""


def _price(model: dict[str, Any]) -> str:
    if model["pricing"]:
        row = model["pricing"][0]
        price = "" if row["price_usd"] is None else f" ${row['price_usd']:g}"
        return f"{row['direction']}: {row['label']}{price}"
    return model["pricing_summary"] or ""


def _render_list(data: dict[str, Any]) -> None:
    table = Table("id", "categories", "released", "command", "price")
    for m in data["models"]:
        table.add_row(m["id"], ", ".join(m["categories"]), m["release_date"] or "",
                      m["command"] or "", _price(m))
    stdout.print(table)


@models.command("list")
@click.option("--category", type=click.Choice(CATEGORY_ORDER), help="A picker filter chip.")
@click.option("--search", help="Text to find in the id, name or description.")
@click.option("--refresh", is_flag=True, help="Ignore the 1-hour catalog cache.")
@json_option
@click.pass_obj
@handle_errors
def list_cmd(state: CliState, category: str | None, search: str | None, refresh: bool,
             json_output: bool) -> None:
    """List models, optionally filtered like the picker."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await list_models(client, profile, category=category, search=search,
                                     refresh=refresh)

    emit(asyncio.run(run()), json_output=json_output, render=_render_list)


@models.command("get")
@click.argument("model_id")
@click.option("--refresh", is_flag=True, help="Ignore the 1-hour catalog cache.")
@json_option
@click.pass_obj
@handle_errors
def get_cmd(state: CliState, model_id: str, refresh: bool, json_output: bool) -> None:
    """Show one model's card."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await get_model(client, profile, model_id, refresh=refresh)

    emit(asyncio.run(run()), json_output=json_output,
         render=lambda m: _render_list({"models": [m]}))
```

In `src/aistudio/cli/main.py`, change the import to
`from aistudio.cli import dev_cmd, models_cmd, profile_cmd, session_cmd` and add
`cli.add_command(models_cmd.models)` after the other `add_command` calls. Keep the imported names
sorted alphabetically in this and later tasks; ruff's isort rule checks it.

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/aistudio/client.py src/aistudio/_app/models.py src/aistudio/cli tests
git commit -m "feat: add AIStudioClient and cached models list/get"
```

---

### Task 4: `prompt list`

**Files:**
- Create: `src/aistudio/_rpc/params/prompts.py`, `src/aistudio/_rpc/rows/prompts.py`, `src/aistudio/_types/prompts.py`, `src/aistudio/_app/prompts.py`, `src/aistudio/cli/prompt_cmd.py`
- Modify: `src/aistudio/client.py` (add `.prompts`), `src/aistudio/cli/main.py` (register `prompt`)
- Test: `tests/unit/test_rows_prompts.py`, `tests/cli/test_prompt_cmd.py`

**Interfaces:**
- Consumes: `registry.LIST_PROMPTS`, `wire.opt_index/safe_index`, `rows.as_str`, `AIStudioClient.call`
- Produces:
  - `params.prompts.list_prompts(page_size=100) -> list`
  - `_types.prompts.PROMPT_TYPES` and `PromptSummary(id, title, type, description, modified, has_images, parent_app_id)` with `.to_dict()`
  - `rows.prompts.decode_prompt_list(response) -> list[PromptSummary]`
  - `client.prompts.list(page_size=100) -> list[PromptSummary]`
  - `_app.prompts.list_prompts(client, *, limit=100, prompt_type=None) -> dict`
  - The click group `cli.prompt_cmd.prompt`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rows_prompts.py`:

```python
import pytest

from aistudio._rpc.params.prompts import list_prompts
from aistudio._rpc.rows.prompts import decode_prompt_list
from aistudio._types.prompts import PromptSummary
from aistudio.exceptions import SchemaDriftError


def test_list_prompts_request_matches_capture(captured):
    assert list_prompts() == captured("ListPrompts").request
    assert list_prompts(25) == [25]


def test_decodes_captured_library(captured):
    prompts = decode_prompt_list(captured("ListPrompts", 4).response)
    assert len(prompts) == 12
    assert prompts[0] == PromptSummary(
        id="prompt_391", title="Generating A Cute House.", type="chat", description=None,
        modified="2026-10-06T05:10:02Z", has_images=False, parent_app_id=None,
    )
    by_id = {p.id: p for p in prompts}
    assert by_id["prompt_5"].description == (
        ":::::::: Code Assistant history - Saved from AI Studio ::::::::"
    )
    assert by_id["prompt_5"].parent_app_id == "prompt_6"
    assert by_id["prompt_5"].modified == "2026-01-09T17:02:46Z"
    assert by_id["prompt_7"].type == "video"
    assert by_id["prompt_7"].modified == "2025-10-03T13:15:21Z"
    assert sum(p.type == "video" for p in prompts) == 7


def test_empty_library_decodes_to_no_prompts():
    assert decode_prompt_list([]) == []
    assert decode_prompt_list(None) == []


def test_unknown_prompt_type_and_sparse_metadata():
    meta = [None] * 12
    meta[11] = [["promptType", "STRUCTURED_PROMPT"], ["hasImages", "true"], "junk"]
    sparse, bare = decode_prompt_list([[["prompts/a", None, None, None, meta], ["prompts/b"]]])
    assert (sparse.type, sparse.has_images, sparse.title, sparse.modified) == (
        "structured_prompt", True, None, None,
    )
    assert (bare.type, bare.has_images) == ("unknown", None)


@pytest.mark.parametrize("response", [["oops"], [["oops"]], [[[None]]]])
def test_missing_names_raise_schema_drift(response):
    with pytest.raises(SchemaDriftError):
        decode_prompt_list(response)
```

Create `tests/cli/test_prompt_cmd.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


@respx.mock
def test_prompt_list_filters_by_type(logged_in_profile, captured):
    logged_in_profile()
    route = respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(200, text=captured("ListPrompts", 4).raw["response"]["body"])
    )
    result = CliRunner().invoke(cli, ["prompt", "list", "--type", "video", "--limit", "50",
                                      "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["count"] == 7
    assert {p["type"] for p in data["prompts"]} == {"video"}
    assert route.calls.last.request.content == b"[50]"


@respx.mock
def test_prompt_list_human_output(logged_in_profile, captured):
    logged_in_profile()
    respx.post(method_url("ListPrompts")).mock(
        return_value=httpx.Response(200, text=captured("ListPrompts", 4).raw["response"]["body"])
    )
    result = CliRunner().invoke(cli, ["prompt", "list", "--type", "chat"])
    assert result.exit_code == 0, result.output
    assert "prompt_391" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rows_prompts.py tests/cli/test_prompt_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.params.prompts'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_rpc/params/prompts.py`:

```python
"""``ListPrompts`` request builder (fixtures ListPrompts/1-4)."""

from __future__ import annotations

from typing import Any


def list_prompts(page_size: int = 100) -> list[Any]:
    """``[page_size]``. No page token was observed; M1 libraries fit in one page."""
    return [page_size]
```

Create `src/aistudio/_types/prompts.py`:

```python
"""Prompt library types (spec §6.3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# promptType values observed in M1 -> CLI names.
PROMPT_TYPES = {"CHUNKED_PROMPT": "chat", "VEO_PROMPT": "video"}


@dataclass(frozen=True)
class PromptSummary:
    id: str  # without the "prompts/" prefix
    title: str | None
    type: str  # "chat", "video", or the raw promptType lower-cased, or "unknown"
    description: str | None
    modified: str | None  # ISO-8601 UTC, e.g. "2026-10-06T05:10:02Z"
    has_images: bool | None
    parent_app_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Create `src/aistudio/_rpc/rows/prompts.py`:

```python
"""Decode ``ListPrompts`` responses (fixtures ListPrompts/1-4).

Response: ``[[prompt, ...]]`` (``[]`` for an empty library). Prompt row:

| index | field |
|---|---|
| 0 | "prompts/<id>" (required) |
| 4 | metadata: [0] title, [1] description, [2] owner, [4][0] modified [seconds, nanos], |
|   | [11] properties [[key, value], ...]: promptType, hasImages, parentAppId, version |
| 13 / 16 | chat / Veo prompt body (empty in list responses) |
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aistudio._rpc.rows import as_str
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.prompts import PROMPT_TYPES, PromptSummary
from aistudio.exceptions import SchemaDriftError

METHOD = "ListPrompts"


def decode_prompt_list(response: Any) -> list[PromptSummary]:
    if response is None or response == []:
        return []
    rows = safe_index(response, 0, method=METHOD, expected="the prompt list")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise SchemaDriftError(METHOD, (0,), "the prompt list")
    return [_prompt(response, i) for i in range(len(rows))]


def _prompt(response: Any, i: int) -> PromptSummary:
    name = safe_index(response, 0, i, 0, method=METHOD, expected="a prompt name")
    if not isinstance(name, str):
        raise SchemaDriftError(METHOD, (0, i, 0), "a prompt name")
    meta = opt_index(response, 0, i, 4)
    props = _properties(opt_index(meta, 11))
    raw_type = props.get("promptType")
    return PromptSummary(
        id=name.removeprefix("prompts/"),
        title=as_str(opt_index(meta, 0)),
        type=PROMPT_TYPES.get(raw_type, raw_type.lower()) if raw_type else "unknown",
        description=as_str(opt_index(meta, 1)),
        modified=_timestamp(opt_index(meta, 4, 0)),
        has_images={"true": True, "false": False}.get(props.get("hasImages", "")),
        parent_app_id=props.get("parentAppId"),
    )


def _properties(pairs: Any) -> dict[str, str]:
    if not isinstance(pairs, list):
        return {}
    return {
        p[0]: p[1] for p in pairs
        if isinstance(p, list) and len(p) == 2 and isinstance(p[0], str) and isinstance(p[1], str)
    }


def _timestamp(value: Any) -> str | None:
    try:
        moment = datetime.fromtimestamp(int(opt_index(value, 0)), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
```

In `src/aistudio/client.py`:

- Add these imports:
  ```python
  from aistudio._rpc.params import prompts as prompts_params
  from aistudio._rpc.rows.prompts import decode_prompt_list
  from aistudio._types.prompts import PromptSummary
  ```
- Add `self.prompts = _Prompts(self)` after `self.models = _Models(self)`.
- Append:

```python


class _Prompts:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def list(self, page_size: int = 100) -> list[PromptSummary]:
        raw = await self._client.call(registry.LIST_PROMPTS, prompts_params.list_prompts(page_size))
        return decode_prompt_list(raw)
```

Create `src/aistudio/_app/prompts.py`:

```python
"""Prompt library use cases (spec §6.3)."""

from __future__ import annotations

from typing import Any

from aistudio.client import AIStudioClient


async def list_prompts(
    client: AIStudioClient, *, limit: int = 100, prompt_type: str | None = None
) -> dict[str, Any]:
    prompts = await client.prompts.list(limit)
    if prompt_type:
        prompts = [p for p in prompts if p.type == prompt_type]
    return {"prompts": [p.to_dict() for p in prompts], "count": len(prompts)}
```

Create `src/aistudio/cli/prompt_cmd.py`:

```python
"""``aistudio prompt``: the AI Studio prompt library (spec §6.3)."""

from __future__ import annotations

import asyncio
from typing import Any

import click
from rich.table import Table

from aistudio._app.prompts import list_prompts
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient


@click.group()
def prompt() -> None:
    """Your saved prompts and chats."""


def _render_list(data: dict[str, Any]) -> None:
    table = Table("id", "type", "modified", "title")
    for p in data["prompts"]:
        table.add_row(p["id"], p["type"], p["modified"] or "", p["title"] or "")
    stdout.print(table)


@prompt.command("list")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True,
              help="Page size requested from AI Studio.")
@click.option("--type", "prompt_type", type=click.Choice(["chat", "video"]),
              help="Only chat prompts or only Veo (video) prompts.")
@json_option
@click.pass_obj
@handle_errors
def list_cmd(state: CliState, limit: int, prompt_type: str | None, json_output: bool) -> None:
    """List saved prompts, newest first."""
    state.require_http()
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await list_prompts(client, limit=limit, prompt_type=prompt_type)

    emit(asyncio.run(run()), json_output=json_output, render=_render_list)
```

In `src/aistudio/cli/main.py`, add `prompt_cmd` to the `from aistudio.cli import …` line and
`cli.add_command(prompt_cmd.prompt)` after the other `add_command` calls.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio tests
git commit -m "feat: add prompt list over plain HTTP"
```

---

### Task 5: `count-tokens`

**Files:**
- Create: `src/aistudio/_rpc/params/generate.py`, `src/aistudio/_rpc/rows/generate.py`, `src/aistudio/_types/chat.py`, `src/aistudio/_app/chat.py`, `src/aistudio/cli/chat_cmd.py`
- Modify: `src/aistudio/client.py` (add `.chat`), `src/aistudio/cli/main.py` (register `count-tokens`)
- Test: `tests/unit/test_rows_generate.py`, `tests/cli/test_chat_cmd.py`

**Interfaces:**
- Consumes: `registry.COUNT_TOKENS`, `normalize_model_id` (Task 3), `opt_index/safe_index`, `rows.as_int`
- Produces:
  - `params.generate.model_name(model_id) -> str`, `text_contents(text, role="user") -> list` and `count_tokens(model_id, text) -> list`. `text_contents` is reused by `GenerateContent` in M3.
  - `rows.generate.MODALITIES` and `decode_token_count(model_id, response) -> TokenCount`.
  - `_types.chat.TokenCount(model, total_tokens, by_modality)` with `.to_dict()`.
  - `client.chat.count_tokens(model_id, text) -> TokenCount`.
  - `_app.chat.DEFAULT_MODEL = "gemini-flash-latest"` and `async _app.chat.count_tokens(client, text, *, model=None) -> dict`.
  - The click command `cli.chat_cmd.count_tokens` (`count-tokens`).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rows_generate.py`:

```python
import pytest

from aistudio._rpc.params.generate import count_tokens, text_contents
from aistudio._rpc.rows.generate import decode_token_count
from aistudio._types.chat import TokenCount
from aistudio.exceptions import SchemaDriftError


def test_count_tokens_request_matches_capture(captured):
    assert count_tokens("gemini-3.1-flash-lite-image", "generate a cute house") == (
        captured("CountTokens", 3).request
    )


def test_text_contents_shape_matches_generate_content_capture(captured):
    generate = captured("GenerateContent", 2).request
    assert text_contents("generate a cute house") == generate[1]


@pytest.mark.parametrize("n,total", [(1, 4), (3, 5)])
def test_decodes_captured_counts(captured, n, total):
    count = decode_token_count("m", captured("CountTokens", n).response)
    assert count == TokenCount("m", total, {"TEXT": total})


def test_zero_is_omitted_on_the_wire():
    assert decode_token_count("m", []) == TokenCount("m", 0, {})
    assert decode_token_count("m", [None, [], None, None, None, [[9, 2], "junk"]]) == (
        TokenCount("m", 0, {"MODALITY_9": 2})
    )


def test_non_integer_total_is_schema_drift():
    with pytest.raises(SchemaDriftError):
        decode_token_count("m", ["five"])
```

Create `tests/cli/test_chat_cmd.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._rpc import wire
from aistudio._transport.http import method_url
from aistudio.cli.main import cli


def serve_count(captured):
    return respx.post(method_url("CountTokens")).mock(
        return_value=httpx.Response(200, text=captured("CountTokens", 3).raw["response"]["body"])
    )


@respx.mock
def test_count_tokens_normalizes_model_and_matches_capture(logged_in_profile, captured):
    logged_in_profile()
    route = serve_count(captured)
    result = CliRunner().invoke(cli, [
        "count-tokens", "generate a cute house", "-m", "models/Gemini-3.1-Flash-Lite-Image",
        "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "model": "gemini-3.1-flash-lite-image", "total_tokens": 5, "by_modality": {"TEXT": 5},
    }
    sent = wire.decode(route.calls.last.request.content.decode())
    assert sent == captured("CountTokens", 3).request


@respx.mock
def test_count_tokens_reads_a_prompt_file_and_defaults_the_model(logged_in_profile, captured,
                                                                tmp_path):
    logged_in_profile()
    route = serve_count(captured)
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("hello")
    result = CliRunner().invoke(cli, ["count-tokens", "--prompt-file", str(prompt_file), "--json"])
    assert result.exit_code == 0, result.output
    assert wire.decode(route.calls.last.request.content.decode())[0] == "models/gemini-flash-latest"


def test_count_tokens_rejects_empty_or_double_input(logged_in_profile, tmp_path):
    logged_in_profile()
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("x")
    for args in (["count-tokens", "   "], ["count-tokens"],
                 ["count-tokens", "a", "--prompt-file", str(prompt_file)]):
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 1, args
        assert json.loads(result.stdout)["code"] == "VALIDATION_ERROR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rows_generate.py tests/cli/test_chat_cmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aistudio._rpc.params.generate'`

- [ ] **Step 3: Write the implementation**

Create `src/aistudio/_rpc/params/generate.py`:

```python
"""Request builders shared by ``CountTokens`` and (from M3) ``GenerateContent``.

Contents (fixtures CountTokens/1-4, GenerateContent/1-2): ``[[parts, role], ...]`` where a text
part is ``[null, text]``.
"""

from __future__ import annotations

from typing import Any


def model_name(model_id: str) -> str:
    return f"models/{model_id}"


def text_contents(text: str, role: str = "user") -> list[Any]:
    return [[[[None, text]], role]]


def count_tokens(model_id: str, text: str) -> list[Any]:
    return [model_name(model_id), text_contents(text)]
```

Create `src/aistudio/_types/chat.py`:

```python
"""Chat-area result types (spec §6.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenCount:
    model: str
    total_tokens: int
    by_modality: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "total_tokens": self.total_tokens,
                "by_modality": dict(self.by_modality)}
```

Create `src/aistudio/_rpc/rows/generate.py`:

```python
"""Decode ``CountTokens`` responses (fixtures CountTokens/1-4).

Response: ``[total, [], details, _, _, [[modality, count], ...]]``. JSPB omits zero values, so a
missing total means 0. Modality codes follow the public Gemini API enum (only TEXT=1 seen in M1).
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_int
from aistudio._rpc.wire import opt_index
from aistudio._types.chat import TokenCount
from aistudio.exceptions import SchemaDriftError

METHOD = "CountTokens"
MODALITIES = {1: "TEXT", 2: "IMAGE", 3: "VIDEO", 4: "AUDIO", 5: "DOCUMENT"}


def decode_token_count(model_id: str, response: Any) -> TokenCount:
    total = opt_index(response, 0)
    if total is None:
        total = 0
    elif as_int(total) is None:
        raise SchemaDriftError(METHOD, (0,), "an integer token total")
    by_modality: dict[str, int] = {}
    entries = opt_index(response, 5)
    for entry in entries if isinstance(entries, list) else []:
        code, count = as_int(opt_index(entry, 0)), as_int(opt_index(entry, 1))
        if code is not None and count is not None:
            by_modality[MODALITIES.get(code, f"MODALITY_{code}")] = count
    return TokenCount(model_id, total, by_modality)
```

In `src/aistudio/client.py`:

- Add these imports:
  ```python
  from aistudio._rpc.params import generate as generate_params
  from aistudio._rpc.rows.generate import decode_token_count
  from aistudio._types.chat import TokenCount
  ```
- Add `self.chat = _Chat(self)` after `self.prompts = _Prompts(self)`.
- Append:

```python


class _Chat:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def count_tokens(self, model_id: str, text: str) -> TokenCount:
        raw = await self._client.call(
            registry.COUNT_TOKENS, generate_params.count_tokens(model_id, text)
        )
        return decode_token_count(model_id, raw)
```

Create `src/aistudio/_app/chat.py`:

```python
"""Chat-area use cases. M2: token counting (spec §6.3)."""

from __future__ import annotations

from typing import Any

from aistudio._app.models import normalize_model_id
from aistudio.client import AIStudioClient
from aistudio.exceptions import ValidationError

DEFAULT_MODEL = "gemini-flash-latest"


async def count_tokens(
    client: AIStudioClient, text: str, *, model: str | None = None
) -> dict[str, Any]:
    if not text.strip():
        raise ValidationError("nothing to count: the text is empty")
    model_id = normalize_model_id(model or DEFAULT_MODEL)
    return (await client.chat.count_tokens(model_id, text)).to_dict()
```

Create `src/aistudio/cli/chat_cmd.py`:

```python
"""Chat-area commands. M2: ``aistudio count-tokens`` (spec §6.3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from aistudio._app.chat import DEFAULT_MODEL
from aistudio._app.chat import count_tokens as count_tokens_use_case
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import emit, json_option, stdout
from aistudio.cli.state import CliState
from aistudio.client import AIStudioClient
from aistudio.exceptions import ValidationError


@click.command("count-tokens")
@click.argument("text", required=False)
@click.option("-m", "--model", default=DEFAULT_MODEL, show_default=True, help="Model id.")
@click.option("--prompt-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Read the text from a file instead.")
@json_option
@click.pass_obj
@handle_errors
def count_tokens(state: CliState, text: str | None, model: str, prompt_file: Path | None,
                 json_output: bool) -> None:
    """Count the tokens TEXT uses with a model."""
    state.require_http()
    if (text is None) == (prompt_file is None):
        raise ValidationError("give either TEXT or --prompt-file", hint="not both, not neither")
    content = text if prompt_file is None else prompt_file.read_text(encoding="utf-8")
    profile = state.profile()

    async def run() -> dict[str, Any]:
        async with AIStudioClient.from_profile(profile) as client:
            return await count_tokens_use_case(client, content, model=model)

    emit(asyncio.run(run()), json_output=json_output,
         render=lambda d: stdout.print(f"{d['total_tokens']} tokens ({d['model']})"))
```

In `src/aistudio/cli/main.py`, add `chat_cmd` to the `from aistudio.cli import …` line and
`cli.add_command(chat_cmd.count_tokens)` after the other `add_command` calls.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio tests
git commit -m "feat: add count-tokens over plain HTTP"
```

---

### Task 6: `auth check --test`

**Files:**
- Modify: `src/aistudio/_app/auth.py` (add `http_probe`), `src/aistudio/cli/session_cmd.py` (add `--test`)
- Test: `tests/cli/test_auth_probe.py`

**Interfaces:**
- Consumes: `AIStudioClient.from_profile`, `client.models.list()`, `AIStudioError`
- Produces:
  - `async _app.auth.http_probe(client) -> dict`, returning `{"ok": True, "models": n}` or `{"ok": False, "code", "message"}`.
  - `auth check --test` adds `checks.http_probe: bool` and a `probe` object. A failed probe sets `status: "error"` and exits 1.

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_auth_probe.py`:

```python
import json

import httpx
import respx
from click.testing import CliRunner

from aistudio._transport.http import method_url
from aistudio.cli.main import cli


@respx.mock
def test_check_test_probes_listmodels(logged_in_profile, captured):
    logged_in_profile()
    respx.post(method_url("ListModels")).mock(
        return_value=httpx.Response(200, text=captured("ListModels").raw["response"]["body"])
    )
    result = CliRunner().invoke(cli, ["auth", "check", "--test", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["checks"]["http_probe"] is True
    assert data["probe"] == {"ok": True, "models": 42}


@respx.mock
def test_check_test_reports_rejected_cookies(logged_in_profile):
    logged_in_profile()
    route = respx.post(method_url("ListModels")).mock(return_value=httpx.Response(401))
    result = CliRunner().invoke(cli, ["auth", "check", "--test", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["checks"]["http_probe"] is False
    assert data["probe"]["code"] == "AUTH_ERROR"
    assert data["hint"] == 'run "aistudio login"'
    assert route.call_count == 1


def test_check_without_test_makes_no_request(logged_in_profile):
    logged_in_profile()
    data = json.loads(CliRunner().invoke(cli, ["auth", "check", "--json"]).stdout)
    assert "http_probe" not in data["checks"]


def test_check_test_skips_the_probe_when_not_logged_in(aistudio_home):
    result = CliRunner().invoke(cli, ["auth", "check", "--test", "--json"])
    data = json.loads(result.stdout)
    assert result.exit_code == 1
    assert data["checks"]["http_probe"] is False
    assert "probe" not in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_auth_probe.py -v`
Expected: FAIL with `Error: No such option: --test`

- [ ] **Step 3: Write the implementation**

Append to `src/aistudio/_app/auth.py`. Add `from aistudio.client import AIStudioClient` and
`from aistudio.exceptions import AIStudioError` to its imports; it already imports `AuthError`
from there, so combine them into one line:

```python


async def http_probe(client: AIStudioClient) -> dict[str, Any]:
    """One real ListModels call over plain HTTP: proves the cookies and signer work."""
    try:
        models = await client.models.list()
    except AIStudioError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}
    return {"ok": True, "models": len(models)}
```

In `src/aistudio/cli/session_cmd.py`:

- Add `from aistudio._app.auth import auth_status, http_probe`, replacing the existing `auth_status` import.
- Add `from aistudio.client import AIStudioClient`.
- Replace the `check` command with:

```python
@auth.command("check")
@click.option("--test", "probe", is_flag=True,
              help="Also make one real ListModels call over plain HTTP.")
@json_option
@click.pass_obj
@handle_errors
def check(state: CliState, probe: bool, json_output: bool) -> None:
    """Check the stored credentials of the active profile."""
    profile = state.profile()
    data = auth_status(profile)
    if probe:
        data["checks"]["http_probe"] = False
        if data["status"] == "ok":

            async def run() -> dict[str, Any]:
                async with AIStudioClient.from_profile(profile) as client:
                    return await http_probe(client)

            data["probe"] = asyncio.run(run())
            data["checks"]["http_probe"] = data["probe"]["ok"]
            if not data["probe"]["ok"]:
                data["status"] = "error"
                if data["probe"]["code"] == "AUTH_ERROR":
                    data["hint"] = 'run "aistudio login"'
    emit(data, json_output=json_output, render=_render_check)
    if data["status"] != "ok":
        raise SystemExit(1)
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q 2>&1 | tail -1 && uv run ruff check .`
Expected: all PASS; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aistudio/_app/auth.py src/aistudio/cli/session_cmd.py tests/cli/test_auth_probe.py
git commit -m "feat: auth check --test probes ListModels over plain HTTP"
```

---

### Task 7: Live smoke tests and README

**Files:**
- Create: `tests/live/test_live_http.py`
- Modify: `README.md` (quick start)

**Interfaces:**
- Consumes: `AIStudioClient.from_profile`, `client.models.list()`, `client.prompts.list()`, `client.chat.count_tokens()`
- Produces: the opt-in `pytest -m live` canary for schema drift (spec §10). Its failures name the drifting field.

- [ ] **Step 1: Write the live tests**

Create `tests/live/test_live_http.py`:

```python
"""Opt-in checks against the real AI Studio service: `uv run pytest -m live`.

Read-only calls only, using the logged-in profile ($AISTUDIO_PROFILE or the default).
"""

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


async def test_live_catalog_decodes_with_known_categories(profile):
    async with AIStudioClient.from_profile(profile) as client:
        models = await client.models.list()
    assert models
    assert not [(m.id, c) for m in models for c in m.categories if c.startswith("code:")]


async def test_live_prompt_list_decodes(profile):
    async with AIStudioClient.from_profile(profile) as client:
        prompts = await client.prompts.list()
    assert all(p.id for p in prompts)


async def test_live_count_tokens(profile):
    async with AIStudioClient.from_profile(profile) as client:
        count = await client.chat.count_tokens("gemini-flash-latest", "hello world")
    assert count.total_tokens > 0
```

- [ ] **Step 2: Confirm they are excluded by default and run them live**

Run: `uv run pytest -q 2>&1 | tail -1`
Expected: the default suite passes and reports the live tests as deselected.

Run: `uv run pytest -m live -v 2>&1 | tail -5`
Expected: `3 passed`, using the profile saved by `aistudio login` in M1; no browser or user action needed.

If the catalog test fails on a `code:<n>` category, AI Studio has added a picker chip. Record it
for the next capture instead of guessing its name.

- [ ] **Step 3: Update the README quick start**

In `README.md`, replace the Quick start code block with:

```bash
uv run aistudio login                       # sign in once in a browser window
uv run aistudio auth check --test           # verify the stored session with one real call
uv run aistudio models list --category music
uv run aistudio models get lyria-3.5
uv run aistudio prompt list --type chat
uv run aistudio count-tokens "hello world" -m gemini-flash-latest
uv run aistudio dev capture                 # record AI Studio traffic into tests/fixtures
```

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_live_http.py README.md
git commit -m "test: add opt-in live smoke tests; document M2 commands"
```
