"""Async client for the AI Studio web app (spec §3)."""

from __future__ import annotations

from datetime import tzinfo
from typing import Any

from aistudio._auth.session import Session
from aistudio._rpc import registry
from aistudio._rpc.params import generate as generate_params
from aistudio._rpc.params import models as models_params
from aistudio._rpc.params import prompts as prompts_params
from aistudio._rpc.registry import RpcSpec, Transport
from aistudio._rpc.rows.generate import decode_token_count
from aistudio._rpc.rows.models import decode_models
from aistudio._rpc.rows.prompts import decode_prompt_list
from aistudio._transport.http import HttpTransport
from aistudio._types.chat import TokenCount
from aistudio._types.models import Model
from aistudio._types.prompts import PromptSummary
from aistudio.exceptions import ValidationError
from aistudio.paths import Profile


class AIStudioClient:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport
        self.models = _Models(self)
        self.prompts = _Prompts(self)
        self.chat = _Chat(self)

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
