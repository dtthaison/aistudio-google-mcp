"""Google Flow's batchexecute framing (docs/wire/findings.md, "Google Flow").

A response is ``)]}'``, a blank line, then length lines each followed by a JSON array. An entry
``["wrb.fr", rpcid, "<json>", …]`` carries one RPC's payload as a JSON string.
"""

from __future__ import annotations

import json
from typing import Any


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")  # NaN/Infinity: --json output must stay JSON


def payloads(text: str) -> list[tuple[str | None, Any]]:
    """Every ``wrb.fr`` entry as ``(rpcid, payload)``, in order (StreamChat's rpcid is None)."""
    out: list[tuple[str | None, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            entries = json.loads(line, parse_constant=_reject_constant)
        except ValueError:
            continue
        for entry in entries if isinstance(entries, list) else []:
            if (isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr"
                    and isinstance(entry[2], str)):
                try:
                    payload = json.loads(entry[2], parse_constant=_reject_constant)
                except ValueError:
                    continue
                out.append((entry[1] if isinstance(entry[1], str) else None, payload))
    return out


def decode_batch(text: str) -> dict[str, Any]:
    """rpcid -> payload for batchexecute responses (one entry per rpcid)."""
    return {rpc: payload for rpc, payload in payloads(text) if rpc is not None}
