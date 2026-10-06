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
