"""Decode ``ListIncidentsHistory`` (fixtures ListIncidentsHistory/1-6; docs/wire/findings.md).

Response ``[[[incident, ...]]]``; incident ``[id, title, _, updates, _, product_codes]``;
update ``[state_code, "YYYY-MM-DD HH:MM" (Pacific, ignored), [epoch_seconds], message]``.
Codes were inferred from 63 incidents; unknown codes surface as ``state:N`` / ``product:N``.
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_int, as_str, as_timestamp
from aistudio._rpc.wire import opt_index
from aistudio._types.status import Incident, IncidentUpdate
from aistudio.exceptions import SchemaDriftError

METHOD = "ListIncidentsHistory"
STATES = {1: "investigating", 2: "identified", 3: "monitoring", 4: "resolved", 5: "mitigating"}
PRODUCTS = {1: "Gemini API", 2: "Live API", 3: "AI Studio"}


def decode_incidents(response: Any) -> list[Incident]:
    rows = opt_index(response, 0, 0)
    if not isinstance(rows, list):
        return []
    return [_incident(row, i) for i, row in enumerate(rows)]


def _code(table: dict[int, str], value: Any, prefix: str) -> str:
    code = as_int(value)
    return table.get(code, f"{prefix}:{code}") if code is not None else f"{prefix}:?"


def _incident(row: Any, i: int) -> Incident:
    incident_id = as_str(opt_index(row, 0))
    if not incident_id:
        raise SchemaDriftError(METHOD, (0, 0, i, 0), "an incident id")
    raw_updates = opt_index(row, 3)
    updates = tuple(
        IncidentUpdate(
            state=_code(STATES, opt_index(u, 0), "state"),
            time=as_timestamp(opt_index(u, 2)),
            message=as_str(opt_index(u, 3)),
        )
        for u in (raw_updates if isinstance(raw_updates, list) else [])
    )
    products = opt_index(row, 5)
    return Incident(
        id=incident_id,
        title=as_str(opt_index(row, 1)),
        products=tuple(_code(PRODUCTS, p, "product")
                       for p in (products if isinstance(products, list) else [])),
        state=updates[-1].state if updates else "state:?",
        started=updates[0].time if updates else None,
        updated=updates[-1].time if updates else None,
        message=updates[-1].message if updates else None,
        updates=updates,
    )
