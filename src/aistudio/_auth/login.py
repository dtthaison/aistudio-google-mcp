"""Interactive Google sign-in through a persistent Playwright context (spec §4.2)."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from aistudio._auth.signer import API_HOST
from aistudio._auth.storage import AccountInfo, save_session_info, save_state
from aistudio.exceptions import AIStudioError, BrowserError, WaitTimeoutError
from aistudio.paths import Profile

APP_URL = "https://aistudio.google.com/"
APP_HOST = "aistudio.google.com"
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
CHROME_ARGS = ["--disable-blink-features=AutomationControlled"]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def is_logged_in(url: str, cookie_names: set[str]) -> bool:
    return urlparse(url).hostname == APP_HOST and "SAPISID" in cookie_names


def api_key_from_request(url: str, headers: Mapping[str, str]) -> str | None:
    if urlparse(url).hostname != API_HOST:
        return None
    key = headers.get("x-goog-api-key")
    return key if key and API_KEY_RE.fullmatch(key) else None


def authuser_from_url(url: str) -> int:
    values = parse_qs(urlparse(url).query).get("authuser")
    try:
        return int(values[0]) if values else 0
    except ValueError:
        return 0


def default_playwright_factory() -> Callable[[], Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserError(
            "playwright is not installed", hint='pip install "aistudio-py[browser]"'
        ) from exc
    return async_playwright


_PROFILE_IN_USE = re.compile(
    r"ProcessSingleton|SingletonLock|already in use|Opening in existing browser session", re.I)
_BROWSER_MISSING = re.compile(
    r"Executable doesn't exist|distribution '[^']*' is not found|ENOENT|not found at", re.I)


async def launch_persistent(playwright: Any, profile: Profile, *, browser: str, headless: bool,
                            viewport: dict[str, int] | None = None) -> Any:
    """Open the profile's persistent Chrome; translate launch failures into BrowserError."""
    options: dict[str, Any] = {
        "channel": None if browser == "chromium" else browser,
        "headless": headless,
        "args": CHROME_ARGS,
        "ignore_default_args": IGNORE_DEFAULT_ARGS,
    }
    if viewport:
        options["viewport"] = viewport
    else:
        options["no_viewport"] = True
    try:
        return await playwright.chromium.launch_persistent_context(
            str(profile.chrome_dir), **options
        )
    except Exception as exc:  # playwright.async_api.Error, missing executables
        if _PROFILE_IN_USE.search(str(exc)):
            raise BrowserError(
                "another aistudio command is using this profile's browser",
                hint="wait for it to finish, then retry; browser commands run one at a time",
            ) from exc
        elif not _BROWSER_MISSING.search(str(exc)):
            hint = "re-run; if it persists, check that Chrome opens normally on this machine"
        elif browser == "chrome":
            hint = (
                'install Google Chrome, or use "--browser chromium" '
                'after "playwright install chromium"'
            )
        else:
            hint = f'run "playwright install {browser}", or use "--browser chrome"'
        raise BrowserError(f"could not launch {browser}: {exc}", hint=hint) from exc


@dataclass(frozen=True)
class LoginResult:
    profile: str
    account: AccountInfo
    api_key_captured: bool


async def run_login(
    profile: Profile,
    *,
    browser: str = "chrome",
    fresh: bool = False,
    timeout: float = 300.0,
    key_wait: float = 20.0,
    poll: float = 1.0,
    playwright_factory: Callable[[], Any] | None = None,
) -> LoginResult:
    factory = playwright_factory or default_playwright_factory()
    profile.ensure()
    if fresh and profile.chrome_dir.exists():
        shutil.rmtree(profile.chrome_dir)
    loop = asyncio.get_running_loop()
    async with factory() as playwright:
        context = await launch_persistent(playwright, profile, browser=browser, headless=False)
        try:
            keys: list[str] = []

            def on_request(request: Any) -> None:
                key = api_key_from_request(request.url, request.headers)
                if key:
                    keys.append(key)

            context.on("request", on_request)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(APP_URL, wait_until="commit")
            deadline = loop.time() + timeout
            while not is_logged_in(page.url, {c["name"] for c in await context.cookies()}):
                if loop.time() > deadline:
                    raise WaitTimeoutError(
                        f"sign-in not completed within {timeout:.0f}s",
                        hint="re-run with a larger --timeout",
                    )
                await asyncio.sleep(poll)
            key_deadline = loop.time() + key_wait
            while not keys and loop.time() < key_deadline:
                await asyncio.sleep(min(poll, 0.5))
            api_key = keys[-1] if keys else _key_from_html(await page.content())
            account = AccountInfo(email=None, authuser=authuser_from_url(page.url))
            save_state(profile, await context.storage_state(), account)
            save_session_info(profile, api_key)
            return LoginResult(profile.name, account, api_key is not None)
        except AIStudioError:
            raise
        except Exception as exc:  # playwright errors once the window is gone
            if "closed" in str(exc).lower():
                raise BrowserError("the login window was closed before sign-in finished",
                                   hint='run "aistudio login" again') from exc
            raise BrowserError(f"the login browser failed: {exc}",
                               hint='run "aistudio login" again') from exc
        finally:
            await context.close()


def _key_from_html(html: str) -> str | None:
    match = API_KEY_RE.search(html)
    return match.group(0) if match else None
