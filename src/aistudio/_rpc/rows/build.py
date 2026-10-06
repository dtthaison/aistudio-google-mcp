"""Decode Build create/update responses (fixtures CreateApplet/2, CodeAssistantOffline/2-3,
StreamCodeAssistantOfflineGeneration/1; docs/wire/findings.md "Build create/update discovery").

CreateApplet -> ``[app_id, [...]]``. CodeAssistantOffline -> ``[run_id, app_id]``.
StreamCodeAssistantOfflineGeneration -> ``[[snapshot, ...]]``; snapshot ``[_, _, [run], app_id]``
where run ``[11]`` is the step counter, ``[16]`` the run id, and the final snapshot's ``[8]`` is
``["model", summary, _, timestamp, [[path, sha256], ...]]``.
"""

from __future__ import annotations

from typing import Any

from aistudio._rpc.rows import as_int, as_str
from aistudio._rpc.wire import opt_index, safe_index
from aistudio._types.apps import BuildProgress
from aistudio.exceptions import SchemaDriftError


def decode_created_app(response: Any) -> str:
    app_id = safe_index(response, 0, method="CreateApplet", expected="the new app id")
    if not isinstance(app_id, str):
        raise SchemaDriftError("CreateApplet", (0,), "the new app id")
    return app_id


def decode_started_run(response: Any) -> tuple[str, str]:
    run_id = safe_index(response, 0, method="CodeAssistantOffline", expected="a run id")
    app_id = safe_index(response, 1, method="CodeAssistantOffline", expected="an app id")
    if not isinstance(run_id, str) or not isinstance(app_id, str):
        raise SchemaDriftError("CodeAssistantOffline", (0,), "run and app ids")
    return run_id, app_id


def decode_build_stream(response: Any) -> BuildProgress:
    items = opt_index(response, 0)
    run_id = app_id = summary = None
    steps, done, files = 0, False, ()
    for item in items if isinstance(items, list) else []:
        run = opt_index(item, 2, 0)
        run_id = as_str(opt_index(run, 16)) or run_id
        app_id = as_str(opt_index(item, 3)) or app_id
        steps = max(steps, as_int(opt_index(run, 11)) or 0)
        final = opt_index(run, 8)
        if as_str(opt_index(final, 0)) == "model":
            done = True
            summary = as_str(opt_index(final, 1))
            entries = opt_index(final, 4)
            entries = entries if isinstance(entries, list) else []
            files = tuple(p for p in (as_str(opt_index(e, 0)) for e in entries) if p)
    return BuildProgress(run_id, app_id, steps, done, files, summary)
