"""Prompt library use cases (spec §6.3)."""

from __future__ import annotations

from typing import Any

from aistudio.client import AIStudioClient

TYPE_SCAN = 1000  # AI Studio's largest page


async def list_prompts(
    client: AIStudioClient, *, limit: int = 100, prompt_type: str | None = None
) -> dict[str, Any]:
    # A type filter looks through up to 1000 prompts, then keeps `limit` of them
    prompts = await client.prompts.list(TYPE_SCAN if prompt_type else limit)
    if prompt_type:
        prompts = [p for p in prompts if p.type == prompt_type][:limit]
    return {"prompts": [p.to_dict() for p in prompts], "count": len(prompts)}
