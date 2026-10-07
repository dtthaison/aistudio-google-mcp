"""Plain-HTTP backend for MakerSuite RPCs that BotGuard does not protect (spec §5.2)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from aistudio._auth.session import Session
from aistudio._auth.signer import API_HOST, build_headers
from aistudio._rpc import wire
from aistudio.exceptions import (
    AIStudioError,
    AuthError,
    ConnectFailedError,
    NetworkError,
    ServerError,
    ValidationError,
    error_from_http,
    error_from_status,
)

SERVICE_URL = (
    f"https://{API_HOST}/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService"
)
_ALLOWED_HOST_SUFFIXES = (".google.com", ".googleapis.com")


def method_url(method: str) -> str:
    return f"{SERVICE_URL}/{method}"


def error_for_response(status: int, text: str) -> AIStudioError:
    try:
        parsed = wire.parse_error(wire.decode(text))
    except ValueError:
        parsed = None
    if parsed is not None:
        return error_from_status(*parsed)
    return error_from_http(status, text)


class HttpTransport:
    def __init__(
        self,
        session: Session,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        reload_session: Callable[[], Session] | None = None,
        max_attempts: int = 3,
        backoff: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._session = session
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._reload_session = reload_session
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._sleep = sleep
        self._jitter = jitter

    async def __aenter__(self) -> HttpTransport:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def post_raw(self, url: str, body_text: str) -> httpx.Response:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(_ALLOWED_HOST_SUFFIXES):
            raise ValidationError(f"refusing to send credentials to {parsed.scheme}://{host}")
        headers = build_headers(self._session, host=host)
        try:
            return await self._client.post(url, content=body_text.encode(), headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ConnectFailedError(f"{type(exc).__name__}: {exc}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

    async def call(self, method: str, body: Any, *, idempotent: bool = False,
                   attempts: int | None = None) -> Any:
        """Send one RPC (spec §9 retries, §4.4 step 1 auth reload).

        At most ``attempts`` sends, plus one more after reloading a session another process
        refreshed on disk (``aistudio login``).
        """
        max_attempts = attempts or self._max_attempts
        text = wire.encode(body)
        reloaded = False
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self.post_raw(method_url(method), text)
            except ConnectFailedError:
                if attempt >= max_attempts:
                    raise
                await self._sleep(self._delay(attempt))
                continue
            except NetworkError:
                if not idempotent or attempt >= max_attempts:
                    raise
                await self._sleep(self._delay(attempt))
                continue
            if response.status_code < 400:
                try:
                    return wire.decode(response.text)
                except ValueError as exc:  # e.g. an HTML page from a proxy or captive portal
                    raise ServerError(
                        "AI Studio answered with a body that is not JSON "
                        f"(HTTP {response.status_code})",
                        hint="re-run; if it persists, AI Studio may be down",
                    ) from exc
            err = error_for_response(response.status_code, response.text)
            if isinstance(err, AuthError) and not reloaded and self._reload_session is not None:
                reloaded = True
                fresh = self._reload_session()
                if fresh != self._session:
                    # Another process (e.g. `aistudio login`) refreshed the cookies on disk.
                    self._session = fresh
                    continue
            if idempotent and err.retriable and attempt < max_attempts:
                await self._sleep(self._delay(attempt))
                continue
            raise err

    async def download(self, url: str) -> bytes:
        """GET a same-origin file with session cookies (Build app files; findings "Export")."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(".google.com"):
            raise ValidationError(f"refusing to send credentials to {parsed.scheme}://{host}")
        reloaded = False
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.get(
                    url, headers={"Cookie": self._session.cookie_header(host)}
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_attempts:
                    raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
                await self._sleep(self._delay(attempt))
                continue
            if 200 <= response.status_code < 300:
                return response.content
            if 300 <= response.status_code < 400:
                target = urlparse(response.headers.get("location", "")).hostname or ""
                if target.startswith("accounts."):
                    raise AuthError("the session expired: the download was redirected to "
                                    "Google sign-in", hint='run "aistudio login"')
                raise ServerError(f"unexpected redirect to {target or 'an unknown host'}")
            err = error_from_http(response.status_code, response.text)
            if isinstance(err, AuthError) and not reloaded and self._reload_session is not None:
                reloaded = True
                fresh = self._reload_session()
                if fresh != self._session:
                    self._session = fresh
                    continue
            if err.retriable and attempt < self._max_attempts:
                await self._sleep(self._delay(attempt))
                continue
            raise err

    def _delay(self, attempt: int) -> float:
        return self._backoff * 2 ** (attempt - 1) + self._jitter() * self._backoff
