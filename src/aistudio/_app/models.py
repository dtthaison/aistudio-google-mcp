"""Model catalog use cases: 1-hour cache, filters, lookup (spec §6.2)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import tzinfo
from typing import Any

from aistudio._fs import read_json, write_private_json
from aistudio._rpc.rows.models import decode_models
from aistudio._types.models import Model
from aistudio.client import AIStudioClient
from aistudio.exceptions import AIStudioError, NotFoundError, SchemaDriftError, ValidationError
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


STARS_TIMEOUT_S = 5.0


async def _starred_ids(client: AIStudioClient) -> set[str] | None:
    """Starred ids for decorating a listing: one quick attempt, None if unreadable.

    The catalog itself is cached; a slow or offline preferences read must not make a cached
    `models list` slow, so this never retries.
    """
    try:
        starred = await asyncio.wait_for(client.models.starred(attempts=1), STARS_TIMEOUT_S)
    except (AIStudioError, asyncio.TimeoutError):
        return None
    return {model_id for model_id, _ in starred}


def _with_star(model: Model, starred: set[str] | None) -> dict[str, Any]:
    return {**model.to_dict(), "starred": None if starred is None else model.id in starred}


async def list_models(
    client: AIStudioClient,
    profile: Profile,
    *,
    category: str | None = None,
    search: str | None = None,
    refresh: bool = False,
    starred_only: bool = False,
    now: Callable[[], float] = time.time,
    tz: tzinfo | None = None,
) -> dict[str, Any]:
    models = filter_models(
        await load_catalog(client, profile, refresh=refresh, now=now, tz=tz),
        category=category,
        search=search,
    )
    if starred_only:  # the filter's answer depends on the stars: report failures, never "none"
        starred: set[str] | None = {m for m, _ in await client.models.starred()}
        models = [m for m in models if m.id in starred]
    else:
        starred = await _starred_ids(client)
    return {"models": [_with_star(m, starred) for m in models], "count": len(models)}


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
    models = await load_catalog(client, profile, refresh=refresh, now=now, tz=tz)
    for model in models:
        if model.id == wanted:
            return _with_star(model, await _starred_ids(client))
    # Spec §2.1: ids may be abbreviated to a unique prefix.
    matches = [m for m in models if m.id.startswith(wanted)]
    if len(matches) == 1:
        return _with_star(matches[0], await _starred_ids(client))
    if matches:
        names = ", ".join(sorted(m.id for m in matches)[:10])
        raise ValidationError(f"{wanted!r} matches several models: {names}",
                              hint="type more of the id")
    raise NotFoundError(f"no model {wanted!r} in the catalog", hint='see "aistudio models list"')


async def star_model(
    client: AIStudioClient,
    profile: Profile,
    model_id: str,
    *,
    starred: bool = True,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Star or unstar a picker model; the whole list is replaced (spec §6.2, findings H10).

    The list is read, changed and written back: a change made at the same moment elsewhere
    (e.g. an open AI Studio tab toggling a star) can overwrite this one.
    """
    wanted = normalize_model_id(model_id)
    current = await client.models.starred()
    is_starred = any(m == wanted for m, _ in current)
    # A starred model that left the catalog (retired) can still be unstarred.
    if not (is_starred and not starred) and wanted not in {
        m.id for m in await load_catalog(client, profile)
    }:
        raise NotFoundError(f"no model {wanted!r} in the catalog",
                            hint='see "aistudio models list"')
    if is_starred == starred:
        return {"model": wanted, "starred": starred, "changed": False}
    if starred:
        updated = [*current, (wanted, (int(now()), 0))]
    else:
        updated = [entry for entry in current if entry[0] != wanted]
    await client.models.set_starred(updated)
    return {"model": wanted, "starred": starred, "changed": True}
