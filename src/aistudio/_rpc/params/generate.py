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
