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
