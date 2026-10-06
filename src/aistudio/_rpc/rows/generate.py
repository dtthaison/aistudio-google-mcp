"""Decode ``CountTokens`` responses (fixtures CountTokens/1-4).

Response: ``[total, [], details, _, _, [[modality, count], ...]]``. JSPB omits zero values, so a
missing total means 0. Modality codes follow the public Gemini API enum (only TEXT=1 seen in M1).
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_int
from aistudio._rpc.wire import opt_index
from aistudio._types.chat import TokenCount
from aistudio.exceptions import SchemaDriftError

METHOD = "CountTokens"
MODALITIES = {1: "TEXT", 2: "IMAGE", 3: "VIDEO", 4: "AUDIO", 5: "DOCUMENT"}


def decode_token_count(model_id: str, response: Any) -> TokenCount:
    total = opt_index(response, 0)
    if total is None:
        total = 0
    elif as_int(total) is None:
        raise SchemaDriftError(METHOD, (0,), "an integer token total")
    by_modality: dict[str, int] = {}
    entries = opt_index(response, 5)
    for entry in entries if isinstance(entries, list) else []:
        code, count = as_int(opt_index(entry, 0)), as_int(opt_index(entry, 1))
        if code is not None and count is not None:
            by_modality[MODALITIES.get(code, f"MODALITY_{code}")] = count
    return TokenCount(model_id, total, by_modality)
