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
from aistudio._types.prompts import PROMPT_TYPES, PromptDetail, PromptSummary, PromptTurn
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


# --- GetPrompt -----------------------------------------------------------------------------
#
# Response: one prompt row (probed live on six chats and a Veo prompt):
#
# | index | field |
# |---|---|
# | 0 | "prompts/<id>" (required) |
# | 3 | run settings: [2] model, [3] temperature, [4] top-p, [5] top-k, [6] max output tokens, |
# |   | [7] safety [[, , category, threshold], ...], [15] response modalities; Veo: [21] aspect |
# |   | ratio, [23] ["<seconds>"] |
# | 4 | metadata, as in ListPrompts |
# | 12 | system instruction message: [text] (``[]`` when empty) |
# | 13 | chat body [chunks, pending inputs] |
# | 16 | Veo body [prompt text, , [mime, base64 image], , ...] |
#
# Chunk (a message part): [0] text, [1] [drive file id], [8] role, [12] [mime, base64] inline
# data, [18] token count, [19] 1 for a thinking part, [29] the text split into parts,
# [32] [seconds, nanos].

GET_METHOD = "GetPrompt"

_CATEGORIES = {7: "harassment", 8: "hate_speech", 9: "sexually_explicit",
               10: "dangerous_content", 11: "civic_integrity"}
_THRESHOLDS = {1: "block_low_and_above", 2: "block_medium_and_above", 3: "block_only_high",
               4: "block_none", 5: "off"}
_MODALITIES = {1: "text", 2: "image", 3: "audio"}


def decode_prompt(response: Any) -> PromptDetail:
    name = safe_index(response, 0, method=GET_METHOD, expected="a prompt name")
    if not isinstance(name, str):
        raise SchemaDriftError(GET_METHOD, (0,), "a prompt name")
    meta = opt_index(response, 4)
    props = _properties(opt_index(meta, 11))
    raw_type = props.get("promptType")
    kind = PROMPT_TYPES.get(raw_type, raw_type.lower()) if raw_type else "unknown"
    run = opt_index(response, 3)
    if kind == "video":
        turns = _video_turns(opt_index(response, 16))
        settings: dict[str, Any] = {"aspect_ratio": as_str(opt_index(run, 21)),
                                    "duration_seconds": _seconds(opt_index(run, 23, 0))}
    else:
        turns = _chat_turns(opt_index(response, 13, 0))
        settings = _chat_settings(run)
    model = as_str(opt_index(run, 2))
    return PromptDetail(
        id=name.removeprefix("prompts/"),
        title=as_str(opt_index(meta, 0)),
        description=as_str(opt_index(meta, 1)),
        type=kind,
        model=model.removeprefix("models/") if model else None,
        system_instruction=as_str(opt_index(response, 12, 0)) or None,
        settings=settings,
        turns=turns,
        modified=as_timestamp(opt_index(meta, 4, 0)),
        parent_app_id=props.get("parentAppId"),
    )


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _seconds(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chat_settings(run: Any) -> dict[str, Any]:
    safety = []
    safety_rows = opt_index(run, 7)
    for entry in safety_rows if isinstance(safety_rows, list) else []:
        category, threshold = opt_index(entry, 2), opt_index(entry, 3)
        if isinstance(category, int) and isinstance(threshold, int):
            safety.append({"category": _CATEGORIES.get(category, f"category_{category}"),
                           "threshold": _THRESHOLDS.get(threshold, f"threshold_{threshold}")})
    modalities = opt_index(run, 15)
    return {
        "temperature": _number(opt_index(run, 3)),
        "top_p": _number(opt_index(run, 4)),
        "top_k": _number(opt_index(run, 5)),
        "max_output_tokens": _number(opt_index(run, 6)),
        "safety_settings": safety,
        "response_modalities": [_MODALITIES.get(m, f"modality_{m}") for m in modalities
                                if isinstance(m, int)] if isinstance(modalities, list) else [],
    }


class _Turn:
    def __init__(self, role: str, at: str | None) -> None:
        self.role, self.at = role, at
        self.texts: list[str] = []
        self.thoughts: list[str] = []
        self.attachments: list[dict[str, Any]] = []

    def build(self) -> PromptTurn:
        return PromptTurn(role=self.role, text="\n\n".join(self.texts),
                          thoughts="\n\n".join(self.thoughts) or None,
                          attachments=self.attachments, at=self.at)


def _attachments(chunk: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    drive = as_str(opt_index(chunk, 1, 0))
    if drive:
        found.append({"source": "drive", "id": drive, "mime": None})
    inline = opt_index(chunk, 12)
    if isinstance(inline, list):  # the base64 bytes at [12][1] are deliberately not kept
        found.append({"source": "inline", "id": None, "mime": as_str(opt_index(inline, 0))})
    return found


def _chat_turns(chunks: Any) -> list[PromptTurn]:
    turns: list[_Turn] = []
    for chunk in chunks if isinstance(chunks, list) else []:
        text = as_str(opt_index(chunk, 0)) or ""
        attachments = _attachments(chunk)
        if not text and not attachments:
            continue
        role = as_str(opt_index(chunk, 8)) or "user"
        if not turns or turns[-1].role != role:
            turns.append(_Turn(role, as_timestamp(opt_index(chunk, 32))))
        turn = turns[-1]
        if text:
            (turn.thoughts if opt_index(chunk, 19) == 1 else turn.texts).append(text)
        turn.attachments.extend(attachments)
    return [t.build() for t in turns]


def _video_turns(body: Any) -> list[PromptTurn]:
    turn = _Turn("user", None)
    text = as_str(opt_index(body, 0))
    if text:
        turn.texts.append(text)
    inline = opt_index(body, 2)
    if isinstance(inline, list):
        turn.attachments.append(
            {"source": "inline", "id": None, "mime": as_str(opt_index(inline, 0))})
    return [turn.build()] if turn.texts or turn.attachments else []
