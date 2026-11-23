"""Which browser a profile drives, and external browser providers (for example ShardX).

A profile's browser is a spec string saved in ``browser.json``:

* ``chrome`` / ``chromium`` / ``msedge``: aistudio's own persistent browser (the default).
* ``NAME:ARG`` (for example ``shardx:work``): runs the provider ``muse-browser-NAME -- ARG``,
  which prints a DevTools URL on its first stdout line and keeps running until its stdin
  closes. aistudio then attaches to that browser over CDP. It never stops that browser or
  closes tabs it did not open itself.
* ``cdp:http://127.0.0.1:PORT``: attach to a browser that is already running.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aistudio._fs import read_json, write_private_json
from aistudio.exceptions import BrowserError, ValidationError
from aistudio.paths import Profile

LOCAL_BROWSERS = ("chrome", "chromium", "msedge")
DEFAULT_BROWSER = "chrome"
PROVIDER_PREFIX = "muse-browser-"
PROVIDER_START_TIMEOUT_S = 120.0  # ShardX can be slow to start a profile
PROVIDER_EXIT_WAIT_S = 5.0  # after stdin closes, then after SIGTERM
_STDERR_LINES = 20
_STDERR_LINE_CHARS = 300

_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DEVTOOLS_URL = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?/?$")
_REDACTIONS = (
    # a whole cookie header: it can hold several values
    (re.compile(r"(?i)\b((?:set-)?cookie\s*[=:]).*"), r"\1 [redacted]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]+"), r"\1 [redacted]"),
    # user:password@ in a URL
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*@"), r"\1[redacted]@"),
    # api_token=x, "token": "x", x-goog-api-key: x, SAPISID=x, __Secure-1PSID=x ...
    (re.compile(
        r"(?i)([\w-]*(?:token|secret|passw(?:or)?d|api[-_]?key|authorization|sapisid|apisid"
        r"|hsid|ssid|sidcc|__secure-\w+|\bsid)[\w-]*[\"']?\s*[=:]\s*[\"']?)[^\s\"',;&]+"),
     r"\1[redacted]"),
)

_SETUP_HINT = (
    'run "muse-browser-shardx setup" once, and "muse-browser-shardx list" to see your '
    "ShardX profiles"
)
_INSTALL_HINT = (
    "install it with: go install github.com/dtthaison/muse-mcp/cmd/muse-browser-shardx@latest "
    "(or put the muse-browser-shardx binary on your PATH), then run its setup"
)


@dataclass(frozen=True)
class BrowserSpec:
    kind: str  # "local", "provider" or "cdp"
    browser: str = DEFAULT_BROWSER  # local: chrome, chromium or msedge
    name: str = ""  # provider: shardx
    arg: str = ""  # provider: the provider's profile
    url: str = ""  # cdp: the DevTools URL

    @property
    def attached(self) -> bool:
        """True when the browser belongs to someone else: aistudio attaches and never closes it."""
        return self.kind != "local"

    def __str__(self) -> str:
        if self.kind == "provider":
            return f"{self.name}:{self.arg}"
        if self.kind == "cdp":
            return f"cdp:{self.url}"
        return self.browser


DEFAULT_SPEC = BrowserSpec(kind="local")


def check_devtools_url(url: str) -> str:
    """A DevTools address must stay on this machine: it gives full control of the browser."""
    if not _DEVTOOLS_URL.match(url):
        raise BrowserError(
            f"{redact(url)!r} is not a local DevTools address",
            hint="it must look like http://127.0.0.1:PORT (the DevTools port stays on localhost)",
        )
    return url.rstrip("/")


def parse_spec(text: str) -> BrowserSpec:
    spec = text.strip()
    if spec in LOCAL_BROWSERS:
        return BrowserSpec(kind="local", browser=spec)
    name, sep, arg = spec.partition(":")
    usage = "use chrome, chromium, msedge, cdp:URL or NAME:PROFILE (for example shardx:work)"
    if not sep:
        raise ValidationError(f"unknown browser {spec!r}", hint=usage)
    if not _PROVIDER_NAME.match(name):
        raise ValidationError(
            f"bad browser name {name!r} in {spec!r}", hint="use lowercase letters, digits, - and _"
        )
    if not arg.strip():
        raise ValidationError(f"browser {spec!r} has an empty profile after the colon", hint=usage)
    if name == "cdp":
        try:
            return BrowserSpec(kind="cdp", url=check_devtools_url(arg.strip()))
        except BrowserError as exc:
            raise ValidationError(str(exc), hint=exc.hint) from exc
    if name in LOCAL_BROWSERS:
        raise ValidationError(f"{name} takes no profile: write {name!r} alone", hint=usage)
    return BrowserSpec(kind="provider", name=name, arg=arg.strip())


def load_spec(profile: Profile) -> BrowserSpec:
    """The profile's saved browser; Chrome when none was saved."""
    path = profile.browser_json
    if not path.exists():
        return DEFAULT_SPEC
    try:
        data = read_json(path)
        value = data["browser"]
        if not isinstance(value, str):
            raise TypeError("browser must be a string")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValidationError(
            f"cannot read {path}: {exc}", hint=f"fix or delete {path}"
        ) from exc
    return parse_spec(value)


def save_spec(profile: Profile, spec: BrowserSpec) -> None:
    profile.ensure()
    write_private_json(profile.browser_json, {"browser": str(spec)})


def reset_spec(profile: Profile) -> bool:
    """Back to the default browser; True if a saved choice was removed."""
    if profile.browser_json.exists():
        profile.browser_json.unlink()
        return True
    return False


def resolve_spec(profile: Profile, override: str | None = None) -> BrowserSpec:
    """The browser for this run: an explicit ``--browser`` value, else the profile's saved one."""
    return parse_spec(override) if override else load_spec(profile)


def has_browser_session(profile: Profile, spec: BrowserSpec) -> bool:
    """Whether the profile has signed in with this browser (an attached browser keeps its own
    profile, so aistudio's saved login stands in for the Chrome folder)."""
    return profile.storage_state.exists() if spec.attached else profile.chrome_dir.exists()


async def page_to_drive(context: Any) -> Any:
    """The page a run works in: aistudio's own browser reuses its first tab, an attached
    browser gets a new tab (its existing tabs are the user's)."""
    if getattr(context, "attached", False):
        try:
            return await context.new_page()
        except Exception as exc:  # playwright.async_api.Error
            raise BrowserError(
                f"could not open a tab in the attached browser: {_first_line(exc)}",
                hint="check that the browser is running and its window is not blocked",
            ) from exc
    return context.pages[0] if context.pages else await context.new_page()


def request_from_own_page(context: Any, request: Any) -> bool:
    """Whether a request on ``context`` comes from a tab aistudio opened. An attached browser's
    context is shared with the user's other tabs, whose traffic must not be read or recorded."""
    if not getattr(context, "attached", False):
        return True
    try:
        return bool(request.frame.page in context.opened_pages)
    except Exception:  # a service worker or a closed frame has no page: not ours
        return False


def check_fresh_allowed(spec: BrowserSpec, fresh: bool) -> None:
    if fresh and spec.attached:
        raise ValidationError(
            "--fresh only resets aistudio's own Chrome profile; it never touches an attached "
            f"browser ({spec})",
            hint="drop --fresh, or sign out in that browser yourself",
        )


def _is_posix() -> bool:
    return os.name == "posix"


# --- one browser run per profile ---------------------------------------------------------------


class BrowserLock:
    """A non-blocking exclusive lock on ``browser.lock``; Chrome's own profile lock does this for
    aistudio's Chrome, an attached browser has none."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        import fcntl

        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise BrowserError(
                "another aistudio command is using this profile's browser",
                hint="wait for it to finish, then retry; browser commands run one at a time",
            ) from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)  # closing the descriptor drops the lock
            self._fd = None


# --- provider process --------------------------------------------------------------------------


def redact(text: str) -> str:
    """Cheap, general masking of values that look like credentials, for messages that may reach
    the screen or a log (a provider's stderr, a browser error)."""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def _clean(line: str) -> str:
    return redact(line.strip())[:_STDERR_LINE_CHARS]


class ProviderProcess:
    """A running provider; stdin stays open until ``stop``."""

    def __init__(self, proc: asyncio.subprocess.Process, name: str) -> None:
        self.proc = proc
        self.name = name
        self.stderr_tail: deque[str] = deque(maxlen=_STDERR_LINES)
        self._stderr_task = asyncio.ensure_future(self._read_stderr())
        self._tasks: list[asyncio.Future[None]] = []
        self._stopped = False

    async def _read_stderr(self) -> None:
        """Keep the last lines. Chunked reads: a line over asyncio's 64 KiB line limit must not
        end the draining, or a full pipe would block the provider."""
        stream = self.proc.stderr
        if stream is None:
            return
        buf = ""
        with contextlib.suppress(Exception):
            while chunk := await stream.read(4096):
                buf += chunk.decode("utf-8", "replace")
                *lines, buf = buf.split("\n")
                buf = buf[-4 * _STDERR_LINE_CHARS:]
                for line in lines:
                    if text := _clean(line):
                        self.stderr_tail.append(text)
        if text := _clean(buf):
            self.stderr_tail.append(text)

    def report(self) -> str:
        return "\n".join(self.stderr_tail)

    async def drain_stdout(self) -> None:
        stream = self.proc.stdout
        if stream is None:
            return
        with contextlib.suppress(Exception):
            while await stream.read(4096):
                pass

    async def stop(self) -> None:
        """Close its stdin and let it clean up; SIGTERM, then SIGKILL, if it does not exit.
        This stops the provider (and its tunnels), never the ShardX browser."""
        if self._stopped:
            return
        self._stopped = True
        try:
            stdin = self.proc.stdin
            if stdin is not None:
                with contextlib.suppress(Exception):
                    stdin.close()
            for sig in (None, signal.SIGTERM, signal.SIGKILL):
                if sig is not None:
                    self._signal_group(sig)
                try:
                    await asyncio.wait_for(self.proc.wait(), PROVIDER_EXIT_WAIT_S)
                    break
                except asyncio.TimeoutError:
                    continue
            with contextlib.suppress(Exception):  # let the last stderr lines arrive
                await asyncio.wait_for(asyncio.shield(self._stderr_task), 1.0)
        finally:
            # Also when cancelled, or when the leader died and left a tunnel in its group.
            self._signal_group(signal.SIGKILL if self.proc.returncode is None else signal.SIGTERM)
            for task in (self._stderr_task, *self._tasks):
                task.cancel()

    def _signal_group(self, sig: int) -> None:
        # The provider leads its own process group, so a tunnel it left behind goes too.
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError, AttributeError):
            os.killpg(self.proc.pid, sig)


def provider_command(spec: BrowserSpec) -> list[str]:
    import shutil

    exe = PROVIDER_PREFIX + spec.name
    found = shutil.which(exe)
    if not found:
        raise BrowserError(f"browser provider {exe!r} is not installed", hint=_INSTALL_HINT)
    return [found, "--", spec.arg]


async def start_provider(spec: BrowserSpec, *, timeout: float | None = None) -> tuple[
        str, ProviderProcess]:
    """Run the provider and return its DevTools URL and the running process."""
    argv = provider_command(spec)
    name = Path(argv[0]).name
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
    except OSError as exc:
        raise BrowserError(f"could not run {name}: {exc}", hint=_INSTALL_HINT) from exc
    provider = ProviderProcess(proc, name)
    wait = PROVIDER_START_TIMEOUT_S if timeout is None else timeout
    try:
        raw = await asyncio.wait_for(_first_line_of(proc), wait)
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            reason = "exited before it printed a DevTools address"
        elif not _DEVTOOLS_URL.match(line):
            reason = "printed something that is not a local DevTools address"
        else:
            provider._tasks.append(asyncio.ensure_future(provider.drain_stdout()))
            return line.rstrip("/"), provider
    except asyncio.TimeoutError:
        reason = f"did not print a DevTools address within {wait:.0f}s"
    except BaseException:
        await asyncio.shield(provider.stop())
        raise
    await provider.stop()
    code = proc.returncode
    detail = provider.report() or "(no message on stderr)"
    raise BrowserError(
        f"{name} {reason}" + (f" (exit code {code})" if code else "") + f":\n{detail}",
        hint=_SETUP_HINT,
    )


async def _first_line_of(proc: asyncio.subprocess.Process) -> bytes:
    return await proc.stdout.readline() if proc.stdout is not None else b""


# --- the attached context ---------------------------------------------------------------------


class AttachedContext:
    """A browser context aistudio attached to but does not own. It offers what callers use of a
    persistent context. ``close`` closes only the pages aistudio opened, then disconnects; the
    browser, its tabs and the provider's browser profile are left as they were."""

    attached = True

    def __init__(self, browser: Any, context: Any, *, owns_context: bool,
                 provider: ProviderProcess | None, lock: BrowserLock) -> None:
        self._browser = browser
        self._context = context
        self._owns_context = owns_context
        self._provider = provider
        self._lock = lock
        self._opened: list[Any] = []
        self._closed = False

    @property
    def pages(self) -> list[Any]:
        return list(self._context.pages)

    @property
    def opened_pages(self) -> list[Any]:
        """The tabs aistudio opened (the only ones it touches)."""
        return list(self._opened)

    async def new_page(self) -> Any:
        page = await self._context.new_page()
        self._opened.append(page)
        return page

    def on(self, event: str, handler: Any) -> Any:
        return self._context.on(event, handler)

    async def cookies(self, *args: Any, **kwargs: Any) -> Any:
        return await self._context.cookies(*args, **kwargs)

    async def storage_state(self, *args: Any, **kwargs: Any) -> Any:
        return await self._context.storage_state(*args, **kwargs)

    async def new_cdp_session(self, page: Any) -> Any:
        return await self._context.new_cdp_session(page)

    async def close(self) -> None:
        """Every step runs even if an earlier one fails or this is cancelled; the first
        cancellation (a BaseException) is re-raised at the end."""
        if self._closed:
            return
        self._closed = True
        interrupted: BaseException | None = None
        steps = (self._close_own_pages, self._close_owned_context, self._disconnect,
                 self._stop_provider)
        for step in steps:
            try:
                await step()
            except Exception:  # noqa: S110 (best effort: the user may have closed things)
                pass
            except BaseException as exc:  # CancelledError, KeyboardInterrupt
                interrupted = interrupted or exc
        self._lock.release()
        if interrupted is not None:
            raise interrupted

    async def _close_owned_context(self) -> None:
        if self._owns_context:  # only a context aistudio created itself
            await self._context.close()

    async def _disconnect(self) -> None:
        # On a CDP-connected browser this drops the connection; it does not quit the browser.
        await self._browser.close()

    async def _stop_provider(self) -> None:
        if self._provider is not None:
            await asyncio.shield(self._provider.stop())

    async def _close_own_pages(self) -> None:
        for page in self._opened:
            with contextlib.suppress(Exception):
                if page.is_closed():
                    continue
                others = [p for p in self._context.pages if p is not page and not p.is_closed()]
                if others or self._owns_context:
                    await page.close()
                else:  # closing a window's last tab can quit the browser: leave it blank
                    await page.goto("about:blank")


async def connect_browser(playwright: Any, profile: Profile, spec: BrowserSpec) -> AttachedContext:
    """Attach to a provider's (or an already running) browser over CDP."""
    if not _is_posix():
        raise BrowserError(
            "attaching to a ShardX or CDP browser needs macOS or Linux",
            hint="use aistudio's own Chrome here: aistudio profile browser --reset",
        )
    profile.ensure()
    lock = BrowserLock(profile.root / "browser.lock")
    lock.acquire()
    provider: ProviderProcess | None = None
    browser: Any = None
    try:
        if spec.kind == "provider":
            url, provider = await start_provider(spec)
        else:
            url = check_devtools_url(spec.url)
        hint = (_SETUP_HINT if spec.kind == "provider"
                else "check that the browser runs with its DevTools port open on localhost")
        try:
            browser = await playwright.chromium.connect_over_cdp(url)
        except Exception as exc:  # playwright.async_api.Error
            raise BrowserError(
                f"could not attach to the browser at {url}: {_first_line(exc)}", hint=hint
            ) from exc
        owns = not browser.contexts
        try:
            context = await browser.new_context() if owns else browser.contexts[0]
        except Exception as exc:
            raise BrowserError(
                f"could not open a browser context at {url}: {_first_line(exc)}", hint=hint
            ) from exc
        return AttachedContext(browser, context, owns_context=owns, provider=provider, lock=lock)
    except BaseException:
        try:
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
        finally:
            try:
                if provider is not None:
                    await asyncio.shield(provider.stop())
            finally:
                lock.release()
        raise


def _first_line(exc: BaseException) -> str:
    return _clean(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
