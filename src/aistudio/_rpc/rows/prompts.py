"""Decode ``ListPrompts`` responses (fixtures ListPrompts/1-4).

Response: ``[[prompt, ...]]`` (``[]`` for an empty library). Prompt row:

| index | field |
|---|---|
| 0 | "prompts/<id>" (required) |
| 4 | metadata: [0] title, [1] description, [2] owner, [4][0] modified [seconds, nanos], |
|   | [11] properties [[key, value], ...]: promptType, hasImages, parentAppId, version |
| 13 / 16 | chat / Veo prompt body (empty in list responses) |
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_str, as_timestamp
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.prompts import PROMPT_TYPES, PromptSummary
from aistudio.exceptions import SchemaDriftError

METHOD = "ListPrompts"


def decode_prompt_list(response: Any) -> list[PromptSummary]:
    if response is None or response == []:
        return []
    rows = safe_index(response, 0, method=METHOD, expected="the prompt list")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise SchemaDriftError(METHOD, (0,), "the prompt list")
    return [_prompt(response, i) for i in range(len(rows))]


def _prompt(response: Any, i: int) -> PromptSummary:
    name = safe_index(response, 0, i, 0, method=METHOD, expected="a prompt name")
    if not isinstance(name, str):
        raise SchemaDriftError(METHOD, (0, i, 0), "a prompt name")
    meta = opt_index(response, 0, i, 4)
    props = _properties(opt_index(meta, 11))
    raw_type = props.get("promptType")
    return PromptSummary(
        id=name.removeprefix("prompts/"),
        title=as_str(opt_index(meta, 0)),
        type=PROMPT_TYPES.get(raw_type, raw_type.lower()) if raw_type else "unknown",
        description=as_str(opt_index(meta, 1)),
        modified=as_timestamp(opt_index(meta, 4, 0)),
        has_images={"true": True, "false": False}.get(props.get("hasImages", "")),
        parent_app_id=props.get("parentAppId"),
    )


def _properties(pairs: Any) -> dict[str, str]:
    if not isinstance(pairs, list):
        return {}
    return {
        p[0]: p[1] for p in pairs
        if isinstance(p, list) and len(p) == 2 and isinstance(p[0], str) and isinstance(p[1], str)
    }
