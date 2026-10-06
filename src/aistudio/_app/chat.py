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
