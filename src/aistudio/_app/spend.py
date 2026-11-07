"""The local spend log: one JSON line per run that can spend, and the report over it.

Pure data and a little file IO (no browser). The CLI layer calls ``build_entry`` and
``append_entry`` once per command (see ``cli/error_handler.py``); ``aistudio spend`` reads the
log back with ``spend_report``.

What a line holds: when, which profile and command, the model, the status, the budget
(``--max-credits``), the credits spent as measured by the command, the media ids, output paths
and the job or project link. It never holds prompt text, lyrics, titles, error messages, cookies
or e-mail addresses: the line is built from a short list of known result fields.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from aistudio._fs import lock_for
from aistudio.exceptions import ValidationError
from aistudio.paths import Profile

FLOW = "flow_credits"
FLOW_MUSIC = "flow_music_credits"
USD = "usd_estimate"

# Every command that can spend, and the unit its spend is counted in.
SPENDING_COMMANDS: dict[str, str] = {
    "flow video": FLOW, "flow image": FLOW, "flow extend": FLOW, "flow edit": FLOW,
    "flow character create": FLOW, "flow character body": FLOW, "flow character voice": FLOW,
    "flow tool use": FLOW, "flow storyboard": FLOW, "flow music generate": FLOW_MUSIC,
    "generate speech": USD, "generate image": USD, "generate music": USD, "transcribe": USD,
    "app create": USD, "app prompt": USD,
}

# AI Studio's catalog price per run in US dollars (catalog of 2026-10-07, see README). A model
# missing here is "unpriced": its runs are counted but add nothing to the dollar total.
USD_PRICES: dict[str, float] = {
    "gemini-3.1-flash-lite-image": 0.034,
    "lyria-3-clip-preview": 0.04,
    "lyria-3.5": 0.08,
}

# Errors that mean nothing was sent or spent (the SKILL.md lists the same ones); every other
# error from a spending command may have cost something, so it is logged as failed.
NOTHING_SPENT = frozenset({"VALIDATION_ERROR", "OVER_BUDGET", "PLAN_MISMATCH", "AUTH_ERROR",
                           "NOT_FOUND", "RATE_LIMITED"})
# Commands whose run without --max-credits only quotes: no spend, so nothing to log.
QUOTE_FIRST = frozenset({"flow video", "flow storyboard", "flow music generate"})

_TS = "%Y-%m-%dT%H:%M:%SZ"
_SPAN = re.compile(r"^([1-9]\d*)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _is_attempt(path: str, params: dict[str, Any]) -> bool:
    """Whether this invocation could spend (a character from a photo, a voice without --style
    and a Tool without --allow-spend cannot)."""
    if path in QUOTE_FIRST:
        return params.get("max_credits") is not None
    if path == "flow character create":
        return bool(params.get("description")) and not params.get("photo")
    if path == "flow character voice":
        return bool(params.get("style"))
    if path == "flow tool use":
        return bool(params.get("allow_spend"))
    return True


def _ids(result: dict[str, Any]) -> list[str]:
    found: list[Any] = list(result.get("media_ids") or [])
    found += [f.get("media_id") for f in result.get("files") or [] if isinstance(f, dict)]
    found += [result.get("media_id"), result.get("clip_id")]
    out: list[str] = []
    for item in found:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
    return out


def _paths(result: dict[str, Any]) -> list[str]:
    return [f["path"] for f in result.get("files") or []
            if isinstance(f, dict) and isinstance(f.get("path"), str)]


def _text(result: dict[str, Any], key: str) -> str | None:
    value = result.get(key)
    return value if isinstance(value, str) and value else None


def build_entry(path: str, profile: str, params: dict[str, Any], result: dict[str, Any] | None,
                error_code: str | None, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The log line for one run, or None when the run is not spend (a quote, a refusal, a
    command that cannot spend). ``result`` is what the command printed; ``error_code`` the code
    of the error that ended it. Only known fields are copied."""
    unit = SPENDING_COMMANDS.get(path)
    if unit is None or not _is_attempt(path, params):
        return None
    result = result or {}
    if error_code is not None:
        if error_code in NOTHING_SPENT:
            return None
        status = "timeout" if error_code == "TIMEOUT" else "failed"
    elif not result:
        return None
    else:
        status = str(result.get("status") or "done")
        if status == "quoted" or result.get("may_spend_credits") is False:
            return None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    model = _text(result, "model") or params.get("model") or None
    entry: dict[str, Any] = {"v": 1, "ts": stamp.strftime(_TS), "profile": profile,
                             "command": path, "unit": unit}
    if model:
        entry["model"] = model
    entry["status"] = status
    if params.get("max_credits") is not None:
        entry["max_credits"] = params["max_credits"]
    if unit == USD:
        entry["usd_estimate"] = USD_PRICES.get(str(model)) if status == "done" else None
    else:
        spent = result.get("credits_spent")
        entry["credits_spent"] = spent if isinstance(spent, int) else None
    if error_code is not None:
        entry["error_code"] = error_code
    for key, value in (("files", _paths(result)), ("media_ids", _ids(result))):
        if value:
            entry[key] = value
    for key, source in (("job_id", "job_id"), ("app_id", "app_id"),
                        ("character_id", "character_id"), ("project", "project"),
                        ("url", "project_url")):
        if text := _text(result, source):
            entry[key] = text
    if "url" not in entry and (text := _text(result, "url")):
        entry["url"] = text
    if path == "flow tool use" and (text := _text(result, "id")):
        entry["tool_id"] = text
    return entry


def append_entry(profile: Profile, entry: dict[str, Any]) -> None:
    """Add one line to the profile's log (owner-only file, one write under a lock)."""
    profile.ensure()
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    with lock_for(profile.spend_log):
        fd = os.open(profile.spend_log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line)


def read_entries(profile: Profile) -> list[dict[str, Any]]:
    """Every readable line of the log, oldest first; a damaged line is skipped."""
    try:
        text = profile.spend_log.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def parse_since(text: str, now: datetime) -> datetime:
    """``30m``, ``1h``, ``7d``, ``2w`` before ``now``, or a date (``2026-11-01``, local
    midnight)."""
    span = _SPAN.match(text.strip())
    if span:
        return now - timedelta(**{_UNITS[span.group(2)]: int(span.group(1))})
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").astimezone()
    except ValueError:
        raise ValidationError(
            f"cannot read --since {text!r}",
            hint="use a span such as 30m, 1h, 7d or 2w, or a date such as 2026-11-01",
        ) from None


def _stamp(entry: dict[str, Any]) -> datetime | None:
    try:
        return datetime.strptime(str(entry.get("ts")), _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _matches(entry: dict[str, Any], command: str) -> bool:
    name = str(entry.get("command") or "")
    return name == command or name.startswith(command + " ")


def _totals(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {
        unit: {"runs": 0, "credits_spent": 0, "unverified_runs": 0} for unit in (FLOW, FLOW_MUSIC)
    }
    totals[USD] = {"runs": 0, "usd": 0.0, "unverified_runs": 0, "unpriced_runs": 0}
    for entry in entries:
        total = totals.get(str(entry.get("unit")))
        if total is None:
            continue
        total["runs"] += 1
        if entry["unit"] == USD:
            if entry.get("status") != "done":
                total["unverified_runs"] += 1
            elif isinstance(entry.get("usd_estimate"), (int, float)):
                total["usd"] = round(total["usd"] + entry["usd_estimate"], 4)
            else:
                total["unpriced_runs"] += 1
        elif isinstance(entry.get("credits_spent"), int):
            total["credits_spent"] += entry["credits_spent"]
        else:
            total["unverified_runs"] += 1
    return totals


def spend_report(profile: Profile, *, since: str | None = None, command: str | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
    """The profile's log, filtered, with totals per unit."""
    now = now or datetime.now(timezone.utc)
    start = parse_since(since, now) if since else None
    entries = read_entries(profile)
    if start is not None:
        entries = [e for e in entries if (t := _stamp(e)) is not None and t >= start]
    if command:
        entries = [e for e in entries if _matches(e, command.strip())]
    return {"profile": profile.name, "log": str(profile.spend_log),
            "since": None if start is None else start.astimezone(timezone.utc).strftime(_TS),
            "command": command or None, "count": len(entries), "totals": _totals(entries),
            "entries": entries}
