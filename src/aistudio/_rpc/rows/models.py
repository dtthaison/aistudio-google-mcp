"""Decode ``ListModels`` responses (fixtures ListModels/1-2, docs/wire/findings.md H8).

Response: ``[[model, ...]]``. Model row:

| index | field |
|---|---|
| 0 | "models/<id>" (required) |
| 2 | version |
| 3 | display name |
| 4 | short description |
| 5, 6 | input, output token limit |
| 7 | supported methods, e.g. ["generateContent", "countTokens"] |
| 38 | card: [0] pricing, [9] description, [15] docs URL, [19][0] release epoch s, |
|    | [20][0] knowledge-cutoff epoch s |
| 74 | picker category codes (CATEGORY_CODES) |

Pricing ``card[0]``: ``[input_groups, output_groups, _, summary_text, pricing_url]``; a group
is a list of rows ``[up_to_tokens, open_ended, price, _, label, per_item_price]``. Plain Gemini
rows carry no label (``[null, 1, 1.5]``); tiered rows carry a token threshold
(``[200000, null, 2]`` then ``[null, 1, 4]``).
"""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any

from aistudio._rpc.rows import as_int, as_str
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.models import CATEGORY_ORDER, Model, PricingRow, command_for
from aistudio.exceptions import SchemaDriftError

METHOD = "ListModels"
# Inferred in M1: the nine codes seen map one-to-one onto the nine picker chips.
CATEGORY_CODES = {
    3: "gemma", 10: "featured", 13: "audio", 14: "video", 15: "images",
    16: "music", 17: "gemini", 18: "live", 19: "agents",
}


def decode_models(response: Any, *, tz: tzinfo | None = None) -> list[Model]:
    rows = safe_index(response, 0, method=METHOD, expected="the model list")
    if not isinstance(rows, list):
        raise SchemaDriftError(METHOD, (0,), "the model list")
    return [_model(response, i, tz) for i in range(len(rows))]


def _model(response: Any, i: int, tz: tzinfo | None) -> Model:
    name = safe_index(response, 0, i, 0, method=METHOD, expected="a model name")
    if not isinstance(name, str):
        raise SchemaDriftError(METHOD, (0, i, 0), "a model name")
    row = response[0][i]
    card = opt_index(row, 38)
    model_id = name.removeprefix("models/")
    categories = _categories(opt_index(row, 74))
    pricing, summary, pricing_url = _pricing(opt_index(card, 0))
    methods = opt_index(row, 7)
    return Model(
        id=model_id,
        display_name=as_str(opt_index(row, 3)),
        description=as_str(opt_index(card, 9)) or as_str(opt_index(row, 4)),
        categories=categories,
        command=command_for(model_id, categories),
        version=as_str(opt_index(row, 2)),
        input_token_limit=as_int(opt_index(row, 5)),
        output_token_limit=as_int(opt_index(row, 6)),
        methods=tuple(m for m in methods if isinstance(m, str))
        if isinstance(methods, list) else (),
        pricing=pricing,
        pricing_summary=summary,
        pricing_url=pricing_url,
        docs_url=as_str(opt_index(card, 15)),
        release_date=_local(opt_index(card, 19), tz, "%Y-%m-%d"),
        knowledge_cutoff=_local(opt_index(card, 20), tz, "%Y-%m"),
    )


def _categories(codes: Any) -> tuple[str, ...]:
    if not isinstance(codes, list):
        return ()
    names = {CATEGORY_CODES.get(c, f"code:{c}") for c in codes if as_int(c) is not None}
    known = [c for c in CATEGORY_ORDER if c in names]
    return tuple(known + sorted(names - set(CATEGORY_ORDER)))


def _pricing(raw: Any) -> tuple[tuple[PricingRow, ...], str | None, str | None]:
    if not isinstance(raw, list):
        return (), None, None
    rows: list[PricingRow] = []
    for index, direction in ((0, "input"), (1, "output")):
        groups = opt_index(raw, index)
        for group in groups if isinstance(groups, list) else []:
            for entry in group if isinstance(group, list) else []:
                label = as_str(opt_index(entry, 4))
                price = opt_index(entry, 2)
                if price is None:
                    price = opt_index(entry, 5)
                if label is None and price is None:
                    continue
                rows.append(PricingRow(direction, label, _price(price),
                                       up_to_tokens=as_int(opt_index(entry, 0))))
    return tuple(rows), as_str(opt_index(raw, 3)), as_str(opt_index(raw, 4))


def _price(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except OverflowError:
        return None


def _local(value: Any, tz: tzinfo | None, fmt: str) -> str | None:
    """Epoch seconds (``["1788454800"]``) as a date in ``tz``; None if absent or out of range."""
    raw = opt_index(value, 0) if isinstance(value, list) else value
    try:
        return datetime.fromtimestamp(int(raw), timezone.utc).astimezone(tz).strftime(fmt)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
