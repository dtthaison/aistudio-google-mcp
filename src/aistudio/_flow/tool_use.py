"""Using a Flow Tool from the CLI: its controls, filling and pressing them, and saving the files
it downloads (findings "Flow Tools discovery").

A Tool runs in a sandboxed frame on *.usercontent.goog that Flow's page compiles with esbuild;
``Flow.download`` reaches Chrome as an ordinary download (live, 2026-10-31). Controls are
numbered in page order and labelled by their <label>, aria-label, own text, or the first line
of text above them; icon ligatures (Material fonts) are left out.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aistudio.exceptions import AuthError, BrowserError, ValidationError

_ATTR = "data-aistudio-n"
_SANDBOX = "usercontent.goog"

_CONTROLS = r"""() => {
  const icon = el => /Material/i.test(getComputedStyle(el).fontFamily || '');
  const own = el => {
    let out = '';
    const walk = node => {
      for (const c of node.childNodes) {
        if (c.nodeType === 3) out += c.textContent;
        else if (c.nodeType === 1 && !icon(c) && !['SCRIPT', 'STYLE'].includes(c.tagName)) walk(c);
      }
    };
    walk(el);
    return out.replace(/\s+/g, ' ').trim();
  };
  const above = el => {
    for (let p = el.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) {
      const icons = new Set([...p.querySelectorAll('*')].filter(icon).map(e => e.innerText.trim()));
      const line = (p.innerText || '').split('\n').map(s => s.trim())
        .find(s => s && !icons.has(s) && s !== String(el.value || ''));
      if (line) return line;
    }
    return '';
  };
  const label = el => {
    const named = el.labels && el.labels[0] ? own(el.labels[0]) : '';
    return (named || el.getAttribute('aria-label') || el.title ||
            (el.tagName === 'BUTTON' || el.getAttribute('role') ? own(el) : '') ||
            el.placeholder || above(el)).slice(0, 80);
  };
  const shown = el => el.type === 'file' || el.getClientRects().length > 0;
  const all = [...document.querySelectorAll(
    'input:not([type=hidden]), select, textarea, button, [role=button], [role=checkbox], ' +
    '[role=switch], [role=tab]')].filter(shown);
  return all.map((el, i) => {
    el.setAttribute('data-aistudio-n', String(i + 1));
    const kind = el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName === 'SELECT' ? 'select'
      : el.tagName === 'TEXTAREA' ? 'text' : (el.getAttribute('role') || 'button');
    return {n: i + 1, kind, label: label(el),
            value: ['button', 'file', 'tab'].includes(kind) ? null
              : kind === 'checkbox' || kind === 'radio' ? String(!!el.checked) : String(el.value),
            min: el.min || null, max: el.max || null, accept: el.accept || null,
            options: kind === 'select' ? [...el.options].map(o => o.text.trim()) : null,
            disabled: !!el.disabled};
  });
}"""

_SET = r"""([n, value]) => {
  const el = document.querySelector(`[data-aistudio-n="${n}"]`);
  if (!el) throw new Error('control is gone');
  if (el.type === 'checkbox' || el.type === 'radio') {
    const want = ['true', 'on', 'yes', '1'].includes(String(value).toLowerCase());
    if (el.checked !== want) el.click();
    return String(el.checked);
  }
  let proto = HTMLInputElement.prototype;
  if (el.tagName === 'SELECT') {
    proto = HTMLSelectElement.prototype;
    const option = [...el.options].find(o => o.value === value || o.text.trim() === value);
    if (!option) throw new Error('no option ' + value);
    value = option.value;
  } else if (el.tagName === 'TEXTAREA') proto = HTMLTextAreaElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return String(el.value);
}"""

_COUNT = "() => document.querySelectorAll('button, input, select, textarea').length"

# A drop the way a person makes it: dragenter, dragover, drop, with the file in dataTransfer.
# The target is the element saying "drop", else the middle of the Tool.
_DROP = r"""async ({data, name, type}) => {
  const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
  const transfer = new DataTransfer();
  transfer.items.add(new File([bytes], name, {type}));
  const says = [...document.querySelectorAll('body *')]
    .filter(el => /drop/i.test(el.textContent || '') && el.children.length === 0);
  const target = says.length ? says[0] : document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  if (!target) throw new Error('nothing to drop onto');
  for (const kind of ['dragenter', 'dragover', 'drop']) {
    target.dispatchEvent(new DragEvent(kind, {bubbles: true, cancelable: true,
                                              dataTransfer: transfer}));
  }
  return target.tagName;
}"""
_MAX_DROP = 100 * 1024 * 1024


@dataclass(frozen=True)
class Step:
    target: int | str  # a control's number or (part of) its label; for a text wait, the text
    value: str | None  # None presses the control
    wait: str | None = None  # "control": wait until it is there and enabled; "text": on screen


@dataclass(frozen=True)
class Control:
    n: int
    kind: str  # button, file, range, number, text, select, checkbox, radio, tab, ...
    label: str
    value: str | None = None
    min: str | None = None
    max: str | None = None
    accept: str | None = None
    options: list[str] | None = None
    disabled: bool = False


_WAITS = {"@": "control", "?": "text"}


def parse_steps(raw: list[str]) -> list[Step]:
    steps = []
    for text in raw:
        wait = _WAITS.get(text[:1])
        if wait:
            target = text[1:].strip()
            if not target:
                raise ValidationError(f"empty step {text!r}",
                                      hint='a wait step is @LABEL, @N or "?TEXT"')
            if wait == "control" and "=" in target:
                raise ValidationError(f"wait step {text!r} takes no value",
                                      hint="@LABEL waits until that control is there and enabled")
            steps.append(Step(int(target) if wait == "control" and target.isdigit() else target,
                              None, wait=wait))
            continue
        target, sep, value = text.partition("=")
        target = target.strip()
        if not target:
            raise ValidationError(f"empty step {text!r}",
                                  hint='a step is N, LABEL, N=VALUE or "LABEL=VALUE"')
        steps.append(Step(int(target) if target.isdigit() else target, value if sep else None))
    return steps


def resolve(controls: list[Control], target: int | str) -> Control:
    if isinstance(target, int):
        for control in controls:
            if control.n == target:
                return control
        raise ValidationError(f"no control {target}",
                              hint="run the command without steps to see the numbers")
    wanted = target.casefold()
    exact = [c for c in controls if c.label.casefold() == wanted]
    found = exact or [c for c in controls if wanted in c.label.casefold()]
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValidationError(f"no control labelled {target!r}",
                              hint="run the command without steps to see the labels")
    raise ValidationError(f"{len(found)} controls match {target!r}",
                          hint="use a number: " + ", ".join(str(c.n) for c in found))


def ready(controls: list[Control], text: str, step: Step) -> bool:
    """Whether a wait step's condition holds on this screen. A label on several controls is
    refused at once: waiting would not make it clearer."""
    if step.wait == "text":
        return str(step.target).casefold() in text.casefold()
    try:
        return not resolve(controls, step.target).disabled
    except ValidationError as exc:
        if " controls match " in str(exc):
            raise
        return False


def safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    return base if base and base not in (".", "..") else "download"


def free_path(folder: Path, name: str, *, force: bool) -> Path:
    """Where to save a download: an existing file is never overwritten without ``force``."""
    path = folder / name
    if force or not path.exists():
        return path
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while (folder / f"{stem}-{n}{suffix}").exists():
        n += 1
    return folder / f"{stem}-{n}{suffix}"


class ToolSession:
    """A Tool open in Flow's page."""

    def __init__(self, ui: Any) -> None:
        self._page = ui.raw_page()
        self._frame: Any = None
        self._downloads: list[Any] = []
        self._page.on("download", lambda download: self._downloads.append(download))

    async def open(self, url: str, *, timeout_s: float = 60.0) -> None:
        try:  # Flow's page never reaches "load" while the Tool's frame keeps loading (live)
            await self._page.goto(url, wait_until="commit")
        except Exception as exc:  # playwright: network, closed target
            raise BrowserError(f"could not open {url}: {exc}",
                               hint="check your connection and re-run") from exc
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            if "accounts.google." in self._page.url:
                raise AuthError("Chrome is signed out: the site redirected to Google sign-in",
                                hint='run "aistudio login"')
            for frame in self._page.frames:
                if _SANDBOX not in frame.url:
                    continue
                try:
                    if await frame.evaluate(_COUNT):
                        self._frame = frame
                        await self.settle(timeout_s=10.0)
                        return
                except Exception:  # the frame was replaced while it compiled
                    continue
        raise BrowserError("the Tool did not start in Flow",
                           hint="open it in Flow to check it works: " + url)

    async def controls(self) -> list[Control]:
        return [Control(**c) for c in await self._frame.evaluate(_CONTROLS)]

    async def text(self) -> str:
        return str(await self._frame.evaluate("() => document.body.innerText")).strip()

    async def act(self, control: Control, value: str | None) -> str | None:
        """Press ``control`` (no value) or set it; returns the value the Tool kept."""
        selector = f'[{_ATTR}="{control.n}"]'
        if control.disabled:
            raise ValidationError(f"control {control.n} ({control.label!r}) is disabled",
                                  hint="an earlier step may be missing (e.g. a picture)")
        if control.kind == "file":
            if value is None:
                raise ValidationError(f"control {control.n} needs a file: {control.n}=PATH")
            path = Path(value).expanduser()
            if not path.is_file():
                raise ValidationError(f"{path} is not a file")
            await self._frame.locator(selector).set_input_files(str(path))
            return str(path)
        if value is None:
            await self._frame.locator(selector).click()
            return None
        if control.kind in ("button", "tab"):
            raise ValidationError(f"control {control.n} ({control.label!r}) is a button",
                                  hint=f"press it with {control.n}, without =VALUE")
        try:
            return str(await self._frame.evaluate(_SET, [control.n, value]))
        except Exception as exc:  # playwright: a missing option or a replaced control
            raise ValidationError(f"could not set control {control.n} to {value!r}: {exc}",
                                  hint="run the command without steps to see the controls") from exc

    async def drop(self, path: Path) -> None:
        """Drop a local file onto the Tool, as dragging it from the desktop would."""
        size = path.stat().st_size
        if size > _MAX_DROP:
            raise ValidationError(f"{path} is {size // 1024 // 1024} MB; the most is "
                                  f"{_MAX_DROP // 1024 // 1024} MB")
        try:
            await self._frame.evaluate(_DROP, {
                "data": base64.b64encode(path.read_bytes()).decode(), "name": path.name,
                "type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
        except Exception as exc:  # playwright: no target, or the Tool threw
            raise BrowserError(f"could not drop {path.name} onto the tool: {exc}",
                               hint="check the tool takes dropped files") from exc

    async def settle(self, *, timeout_s: float = 30.0, quiet_s: float = 1.2) -> None:
        """Wait until the Tool's text stops changing (or ``timeout_s``)."""
        deadline = time.monotonic() + timeout_s
        last, since = None, time.monotonic()
        while time.monotonic() < deadline:
            try:
                now = await self.text()
            except Exception:  # navigating or re-rendering
                now = None
            if now != last:
                last, since = now, time.monotonic()
            elif time.monotonic() - since >= quiet_s:
                return
            await asyncio.sleep(0.3)

    async def save_downloads(self, folder: Path, *, force: bool) -> list[dict[str, Any]]:
        await asyncio.sleep(1.5)  # a download can start just after the Tool goes quiet
        saved = []
        for download in self._downloads:
            folder.mkdir(parents=True, exist_ok=True)
            target = free_path(folder, safe_name(download.suggested_filename), force=force)
            await download.save_as(str(target))
            saved.append({"path": str(target), "bytes": target.stat().st_size,
                          "name": download.suggested_filename})
        return saved
