"""Google Flow's batchexecute framing (docs/wire/findings.md, "Google Flow").

A response is ``)]}'``, a blank line, then length lines each followed by a JSON array. An entry
``["wrb.fr", rpcid, "<json>", …]`` carries one RPC's payload as a JSON string.
"""

from __future__ import annotations

import json
from typing import Any


def decode_batch(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            entries = json.loads(line)
        except ValueError:
            continue
        for entry in entries if isinstance(entries, list) else []:
            if (isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr"
                    and isinstance(entry[2], str)):
                try:
                    out[str(entry[1])] = json.loads(entry[2])
                except ValueError:
                    continue
    return out
