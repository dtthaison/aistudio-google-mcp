"""``aistudio spend``: what the CLI's paid commands spent, from the local spend log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import click
from rich.table import Table

from aistudio._app.spend import FLOW, FLOW_MUSIC, USD, spend_report
from aistudio.cli.error_handler import handle_errors
from aistudio.cli.output import cells, emit, json_option, stdout
from aistudio.cli.state import CliState


def _when(ts: Any) -> str:
    try:
        when = datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return when.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(ts or "-")


def _spent(entry: dict[str, Any]) -> str:
    if entry.get("unit") == USD:
        usd = entry.get("usd_estimate")
        return "-" if usd is None else f"${usd:g}"
    spent = entry.get("credits_spent")
    if spent is not None:
        return f"{spent} cr"
    budget = entry.get("max_credits")
    return f"unknown (up to {budget})" if budget is not None else "unknown"


def _where(entry: dict[str, Any]) -> str:
    return (", ".join(entry.get("files") or []) or ", ".join(entry.get("media_ids") or [])
            or entry.get("job_id") or entry.get("app_id") or entry.get("url") or "")


def _runs(total: dict[str, Any]) -> str:
    n = total["runs"]
    return f"{n} run{'' if n == 1 else 's'}"


def _render(data: dict[str, Any]) -> None:
    if not data["entries"]:
        stdout.print(f"No spending logged in {data['log']}"
                     + (f" since {data['since']}" if data["since"] else ""), markup=False)
        return
    table = Table()
    for name in ("time", "command", "model", "status", "spent", "output"):
        table.add_column(name, overflow="fold", no_wrap=name in ("time", "model"))
    for e in data["entries"]:
        table.add_row(*cells(_when(e.get("ts")), str(e.get("command") or ""),
                             str(e.get("model") or ""), str(e.get("status") or ""),
                             _spent(e), _where(e)))
    stdout.print(table)
    totals = data["totals"]
    for unit, label in ((FLOW, "Flow credits"), (FLOW_MUSIC, "Flow Music credits")):
        t = totals[unit]
        line = f"{label}: {t['credits_spent']} spent in {_runs(t)}"
        if t["unverified_runs"]:
            line += f", {t['unverified_runs']} unverified (spend unknown)"
        stdout.print(line, markup=False)
    t = totals[USD]
    line = f"AI Studio: about ${t['usd']:g} (catalog estimate) in {_runs(t)}"
    if t["unpriced_runs"]:
        line += f", {t['unpriced_runs']} without a catalog price"
    if t["unverified_runs"]:
        line += f", {t['unverified_runs']} failed or unfinished"
    stdout.print(line, markup=False)


@click.command("spend")
@click.option("--since", help="Only runs since then: a span such as 30m, 1h, 7d or 2w, or a "
                              "date such as 2026-11-01 (local midnight).")
@click.option("--command", "command", help='Only this command and the ones under it, e.g. '
                                           '"flow" or "flow video".')
@json_option
@click.pass_obj
@handle_errors
def spend(state: CliState, since: str | None, command: str | None, json_output: bool) -> None:
    """What paid commands spent, from this profile's local log (reads no website).

    Lists every run that spent or may have spent: finished, timed out and failed. A failed run
    shows its spend as unknown with the budget it was allowed. Quote-only runs are not logged.
    Totals are per unit: Flow credits, Flow Music credits and an AI Studio dollar estimate."""
    emit(spend_report(state.profile(), since=since, command=command),
         json_output=json_output, render=_render)
