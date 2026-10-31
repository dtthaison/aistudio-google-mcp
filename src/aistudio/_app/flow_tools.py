"""Google Flow Tools ("applets"): list, read, export and build (findings "Flow Tools discovery").
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from aistudio._app.apps import _preflight, _safe_relative, pick
from aistudio._app.flow import _media_kind, _open, _project_id, _save_all
from aistudio._browser.ui import open_browser
from aistudio._flow.download import download_signed
from aistudio._flow.page import CREATE_TOOL_URL, PROJECT_URL, TOOL_URL, FlowPage, ToolBuild
from aistudio._flow.rows import FlowTool, ToolFile
from aistudio._flow.tool_use import Step, ToolSession, parse_steps, ready, resolve
from aistudio.exceptions import BrowserError, NotFoundError, ServerError, ValidationError
from aistudio.paths import Profile

_FLOW_CALL = re.compile(r"\bFlow(?:\.[a-zA-Z]+)+")
_LIST_HINT = 'see "aistudio flow tool list"'
_SAVE_POLLS = 6


def _window(visible: bool) -> str:
    return "visible" if visible else "minimized"


def _flow_calls(files: list[ToolFile]) -> list[str]:
    return sorted({m.group(0) for f in files for m in _FLOW_CALL.finditer(f.text)})


def _report(tool: FlowTool, files: list[ToolFile], project_id: str | None) -> dict[str, Any]:
    calls = _flow_calls(files)
    return {**asdict(tool), "project": project_id,
            "files": [{"path": f.path, "mime_type": f.mime_type, "bytes": len(f.text.encode())}
                      for f in files],
            "flow_calls": calls,
            "may_spend_credits": any(c.startswith("Flow.generate") for c in calls)}


async def flow_tools(profile: Profile, *, kind: str | None = None, search: str | None = None,
                     project: str | None = None, visible: bool = False,
                     browser: Callable[..., Any] = open_browser,
                     page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    project_id = _project_id(project) if project else None
    async with browser(profile, window=_window(visible)) as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        tools = await page.tools()
    if kind:
        tools = [t for t in tools if t.kind == kind]
    if search:
        wanted = search.casefold()
        tools = [t for t in tools if wanted in f"{t.name}\n{t.description or ''}".casefold()]
    return {"tools": [asdict(t) for t in tools], "count": len(tools), "project": project_id}


async def _read(page: Any, ref: str, allow_remix: bool) -> tuple[FlowTool, list[ToolFile]]:
    tool = pick(await page.tools(), lambda t: t.id, ref, noun="tool", hint=_LIST_HINT)
    if tool.kind == "template" and not allow_remix:  # Flow copies a template when it is read
        raise ValidationError(f"reading {tool.name!r} would add \"Remix of {tool.name}\" to your "
                              "tools", hint="pass --allow-remix to accept that copy")
    files = await page.tool_files(tool.id, tool.version_id) if tool.version_id else []
    if not files:
        raise NotFoundError(f"tool {tool.id} has no saved code",
                            hint="its build may have failed; see it in Flow's Tools page")
    return tool, files


def _remix_warnings(tool: FlowTool) -> dict[str, Any]:
    if tool.kind != "template":
        return {}
    return {"warnings": [f"Flow added \"Remix of {tool.name}\" to your tools"]}


async def flow_tool_get(profile: Profile, ref: str, *, allow_remix: bool = False,
                        project: str | None = None,
                        visible: bool = False, browser: Callable[..., Any] = open_browser,
                        page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """One Tool, its files and the Flow calls in its code. ``Flow.generate.*`` is how a Tool
    spends credits, and Flow gives no quote for it, so reading the code is the only warning."""
    project_id = _project_id(project) if project else None
    async with browser(profile, window=_window(visible)) as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        tool, files = await _read(page, ref, allow_remix)
    return {**_report(tool, files, project_id), **_remix_warnings(tool)}


async def flow_tool_export(profile: Profile, ref: str, dest: Path, *, force: bool = False,
                           allow_remix: bool = False, project: str | None = None,
                           visible: bool = False,
                           browser: Callable[..., Any] = open_browser,
                           page_factory: Callable[[Any], Any] = FlowPage) -> dict[str, Any]:
    """Write a Tool's code into ``dest``. Every path is checked before anything is written."""
    if dest.exists() and dest.is_dir() and any(dest.iterdir()) and not force:
        raise ValidationError(f"{dest} is not empty",
                              hint="choose an empty folder or pass --force to overwrite files")
    project_id = _project_id(project) if project else None
    async with browser(profile, window=_window(visible)) as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        tool, files = await _read(page, ref, allow_remix)
    targets = [(_safe_relative(f.path), "") for f in files]
    _preflight(targets, dest)
    for (rel, _), file in zip(targets, files, strict=True):
        target = dest.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.text, encoding="utf-8")
    return {"id": tool.id, "name": tool.name, "path": str(dest), "written": len(files),
            "project": project_id, **_remix_warnings(tool)}


def _described(text: str, noun: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise ValidationError(f"describe the {noun}", hint="pass the text or --prompt-file")
    return text


async def _saved(page: Any, build: ToolBuild, project_id: str, *, not_version: str | None,
                 started: float, now: Callable[[], float]) -> dict[str, Any]:
    url = TOOL_URL.format(project=project_id, tool=build.tool_id)
    if build.timed_out:  # left running: a reload or a re-run would lose or repeat the build
        return {"status": "timeout", "id": build.tool_id, "project": project_id, "url": url}
    await page.finish_tool()
    tool = await page.saved_tool(build.tool_id, not_version=not_version)
    files = await page.tool_files(tool.id, tool.version_id) if tool and tool.version_id else []
    if tool is None or not files:
        raise ServerError(f"Flow's tool agent finished but saved nothing for {build.tool_id}",
                          hint=f"check it in Flow before re-running: {url}")
    return {"status": "done", "url": url, "seconds": round(now() - started),
            **_report(tool, files, project_id)}


async def flow_tool_create(profile: Profile, description: str, *, project: str | None = None,
                           timeout_s: float = 600.0, visible: bool = False,
                           browser: Callable[..., Any] = open_browser,
                           page_factory: Callable[[Any], Any] = FlowPage,
                           now: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Have Flow's agent build a Tool. No credits, but each try counts toward a daily limit."""
    text = _described(description, "tool")
    project_id = _project_id(project) if project else None
    started = now()
    async with browser(profile, window=_window(visible)) as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        build = await page.build_tool(CREATE_TOOL_URL.format(id=project_id), text,
                                      timeout_s=timeout_s)
        return await _saved(page, build, project_id, not_version=None, started=started, now=now)


async def flow_tool_edit(profile: Profile, ref: str, change: str, *, project: str | None = None,
                         timeout_s: float = 600.0, visible: bool = False,
                         browser: Callable[..., Any] = open_browser,
                         page_factory: Callable[[Any], Any] = FlowPage,
                         now: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Have Flow's agent change one of your own Tools."""
    text = _described(change, "change")
    project_id = _project_id(project) if project else None
    started = now()
    async with browser(profile, window=_window(visible)) as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        tool = pick(await page.tools(), lambda t: t.id, ref, noun="tool", hint=_LIST_HINT)
        if tool.kind != "mine":  # a template's page reads its code, which makes a remix
            raise ValidationError(f"{tool.name!r} is not your tool",
                                  hint="only your own tools can be edited; remix it in Flow first")
        url = f"{TOOL_URL.format(project=project_id, tool=tool.id)}?mode=EDIT"
        build = await page.build_tool(url, text, timeout_s=timeout_s)
        return await _saved(page, build, project_id, not_version=tool.version_id,
                            started=started, now=now)


_POLL_S = 2.0


async def _wait(session: Any, step: Step, timeout_s: float,
                pause: Callable[[float], Awaitable[Any]]) -> float:
    """Poll the Tool until a wait step's condition holds; seconds waited (in polls)."""
    polls = max(1, math.ceil(timeout_s / _POLL_S))
    text = ""
    for i in range(polls + 1):
        controls = await session.controls()
        text = await session.text()
        if ready(controls, text, step):
            return i * _POLL_S
        if i < polls:
            await pause(_POLL_S)
    what = "the control" if step.wait == "control" else "the text"
    last = " ".join(text.split())[-200:]
    raise BrowserError(f"waited {timeout_s:.0f} s for {what} {str(step.target)!r}",
                       hint=f"raise --step-timeout if the tool is still working; its screen "
                            f"ended with: {last}")


async def flow_tool_use(profile: Profile, ref: str, steps: list[str], out_dir: Path, *,
                        allow_remix: bool = False, allow_spend: bool = False, force: bool = False,
                        project: str | None = None, visible: bool = False,
                        step_timeout_s: float = 60.0, drop: Path | None = None,
                        browser: Callable[..., Any] = open_browser,
                        page_factory: Callable[[Any], Any] = FlowPage,
                        session_factory: Callable[[Any], Any] = ToolSession,
                        downloader: Callable[[str], Any] = download_signed,
                        pause: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                        ) -> dict[str, Any]:
    """Open a Tool in Flow, run ``steps`` on its controls and save what it downloads, and
    what it saves into the Flow project (``Flow.save``). ``drop`` is dropped onto the Tool
    first, for Tools that take a file only by drag and drop. Without steps, it lists the
    controls. Malformed steps and a missing ``drop`` are refused before Chrome opens."""
    parsed = parse_steps(steps)
    if drop is not None and not drop.is_file():
        raise ValidationError(f"{drop} is not a file")
    project_id = _project_id(project) if project else None
    async with browser(profile, window="visible" if visible else "headless") as ui:
        page = page_factory(ui)
        project_id = await _open(page, profile, project_id)
        tool, files = await _read(page, ref, allow_remix)
        report = _report(tool, files, project_id)
        spends = [c for c in report["flow_calls"] if c.startswith("Flow.generate")]
        if spends and not allow_spend:
            hint = ("Flow gives no quote for a Tool: pass --allow-spend only with the user's OK"
                    if parsed or drop else
                    "listing opens the tool, and a tool could generate as soon as it opens: "
                    "check with `aistudio flow tool export` that Flow.generate runs only when a "
                    "button is pressed, tell the user, then list again with --allow-spend")
            raise ValidationError(f"{tool.name!r} can spend Flow credits ({', '.join(spends)})",
                                  hint=hint)
        before = await page.credits() if spends else None
        saves = bool(parsed or drop) and "Flow.save" in report["flow_calls"]
        known = {m.media_id for m in (await page.contents(project_id)).media} if saves else set()
        url = TOOL_URL.format(project=project_id, tool=tool.id)
        session = session_factory(ui)
        await session.open(url)
        if drop is not None:
            await session.drop(drop)
            await session.settle(timeout_s=step_timeout_s)
        done = []
        for raw, step in zip(steps, parsed, strict=True):
            if step.wait:
                waited = await _wait(session, step, step_timeout_s, pause)
                done.append({"step": raw, "wait": step.wait, "waited_s": waited})
                continue
            control = resolve(await session.controls(), step.target)
            kept = await session.act(control, step.value)
            await session.settle(timeout_s=step_timeout_s)
            done.append({"step": raw, "control": control.n, "label": control.label,
                         "kind": control.kind, "value": kept})
        saved = await session.save_downloads(out_dir, force=force) if parsed else []
        controls = [asdict(c) for c in await session.controls()]
        text = await session.text()
        to_flow = (await _saved_to_flow(page, project_id, known, out_dir, downloader, pause)
                   if saves else [])
        after = await page.credits() if spends else None
    result: dict[str, Any] = {
        "id": tool.id, "name": tool.name, "project": project_id, "url": url,
        "may_spend_credits": bool(spends), "steps": done, "files": saved,
        "saved_to_flow": to_flow,
        "controls": controls, "text": text[:4000], **_remix_warnings(tool)}
    if spends:
        result.update(credits_before=before, credits_after=after,
                      credits_spent=None if before is None or after is None else before - after)
    return result


async def _saved_to_flow(page: Any, project_id: str, known: set[str], folder: Path,
                         downloader: Callable[[str], Any],
                         pause: Callable[[float], Awaitable[Any]]) -> list[dict[str, Any]]:
    """Download what the Tool added to the project. Flow can list a save a few seconds late."""
    new: list[Any] = []
    for attempt in range(_SAVE_POLLS):
        new = [m for m in (await page.contents(project_id)).media if m.media_id not in known]
        if new or attempt == _SAVE_POLLS - 1:
            break
        await pause(5.0)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for listed in new:
        found = await page.media_details(listed.media_id) or listed
        kind = "video" if found.video_url else _media_kind(listed)
        files, _ = await _save_all(kind, [found], out=folder, overwrite=False,
                                   downloader=downloader, stamp=datetime.now(),
                                   project_url=PROJECT_URL.format(id=project_id))
        out += [{"media_id": listed.media_id, "kind": kind, "path": f["path"],
                 "bytes": f["bytes"]} for f in files]
    return out
