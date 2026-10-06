"""User preference builders (fixtures GetUserPreferences/5, UpdateUserPreferences/6-7).

UpdateUserPreferences is ``[preferences, [field_mask]]``; the starred list is field ``[6]``
(``[["models/<id>", [seconds, nanos]], ...]``) and the mask ``["starred_models"]`` replaces it
whole. An empty list is sent as an empty preferences message.
"""

from __future__ import annotations

from typing import Any

Starred = tuple[str, tuple[int, int]]


def get_preferences() -> list[Any]:
    return []


def set_starred(starred: list[Starred]) -> list[Any]:
    if not starred:
        return [[], [["starred_models"]]]
    entries = [[f"models/{model_id}", [seconds, nanos]] for model_id, (seconds, nanos) in starred]
    return [[None] * 6 + [entries], [["starred_models"]]]
