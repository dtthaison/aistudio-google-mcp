"""Plain-HTTP backend for MakerSuite RPCs that BotGuard does not protect (spec §5.2)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from aistudio._auth.session import Session
from aistudio._auth.signer import API_HOST, build_headers
from aistudio._rpc import wire
from aistudio.exceptions import (
    AIStudioError,
    NetworkError,
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
    ) -> None:
        self._session = session
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

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
        except httpx.TransportError as exc:
            raise NetworkError(f"{type(exc).__name__}: {exc}") from exc

    async def call(self, method: str, body: Any) -> Any:
        response = await self.post_raw(method_url(method), wire.encode(body))
        if response.status_code >= 400:
            raise error_for_response(response.status_code, response.text)
        return wire.decode(response.text)
