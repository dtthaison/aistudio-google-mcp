"""Async client for the AI Studio web app (spec §3)."""

from __future__ import annotations

from datetime import tzinfo
from typing import Any

from aistudio._auth.session import Session
from aistudio._rpc import registry
from aistudio._rpc.params import apps as apps_params
from aistudio._rpc.params import generate as generate_params
from aistudio._rpc.params import models as models_params
from aistudio._rpc.params import preferences as preferences_params
from aistudio._rpc.params import prompts as prompts_params
from aistudio._rpc.params import status as status_params
from aistudio._rpc.registry import RpcSpec, Transport
from aistudio._rpc.rows.apps import decode_apps, decode_gallery, decode_recent, decode_runs
from aistudio._rpc.rows.generate import decode_token_count
from aistudio._rpc.rows.models import decode_models
from aistudio._rpc.rows.preferences import decode_starred
from aistudio._rpc.rows.prompts import decode_prompt_list
from aistudio._rpc.rows.status import decode_incidents
from aistudio._transport.http import HttpTransport
from aistudio._types.apps import App, AppRun, GalleryApp, RecentItem
from aistudio._types.chat import TokenCount
from aistudio._types.models import Model
from aistudio._types.prompts import PromptSummary
from aistudio._types.status import Incident
from aistudio.exceptions import ValidationError
from aistudio.paths import Profile


class AIStudioClient:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport
        self.models = _Models(self)
        self.prompts = _Prompts(self)
        self.chat = _Chat(self)
        self.apps = _Apps(self)
        self.gallery = _Gallery(self)
        self.status = _Status(self)

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


class _Prompts:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def list(self, page_size: int = 100) -> list[PromptSummary]:
        raw = await self._client.call(registry.LIST_PROMPTS, prompts_params.list_prompts(page_size))
        return decode_prompt_list(raw)


class _Chat:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def count_tokens(self, model_id: str, text: str) -> TokenCount:
        raw = await self._client.call(
            registry.COUNT_TOKENS, generate_params.count_tokens(model_id, text)
        )
        return decode_token_count(model_id, raw)


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


class _Status:
    def __init__(self, client: AIStudioClient) -> None:
        self._client = client

    async def incidents(self) -> list[Incident]:
        raw = await self._client.call(registry.LIST_INCIDENTS, status_params.list_incidents())
        return decode_incidents(raw)
