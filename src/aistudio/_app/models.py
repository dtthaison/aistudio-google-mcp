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
from aistudio.exceptions import NotFoundError, SchemaDriftError, ValidationError
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
    models = await load_catalog(client, profile, refresh=refresh, now=now, tz=tz)
    for model in models:
        if model.id == wanted:
            return model.to_dict()
    # Spec §2.1: ids may be abbreviated to a unique prefix.
    matches = [m for m in models if m.id.startswith(wanted)]
    if len(matches) == 1:
        return matches[0].to_dict()
    if matches:
        names = ", ".join(sorted(m.id for m in matches)[:10])
        raise ValidationError(f"{wanted!r} matches several models: {names}",
                              hint="type more of the id")
    raise NotFoundError(f"no model {wanted!r} in the catalog", hint='see "aistudio models list"')
