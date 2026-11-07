"""Download Flow media from its signed CDN links and Flow Music songs from public storage; no
cookies are sent (findings "Google Flow", "Flow Music")."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from aistudio.exceptions import NetworkError, ServerError, ValidationError

ALLOWED_HOST = "flow-content.google"


async def download_signed(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValidationError(f"refusing to download from {parsed.scheme}://{parsed.hostname}")
    owned = client is None
    http = client or httpx.AsyncClient(timeout=120.0, follow_redirects=False)
    try:
        response = await http.get(url)
    except httpx.HTTPError as exc:  # transport, decoding, too many redirects, …
        raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if owned:
            await http.aclose()
    if 200 <= response.status_code < 300:
        return response.content
    if response.status_code in (401, 403):
        raise ServerError("the Flow download link expired or was refused",
                          hint="open the project in Flow and download it there")
    raise ServerError(f"Flow download failed (HTTP {response.status_code})")


MUSIC_HOST = "storage.googleapis.com"
MUSIC_PREFIX = "/producer-app-public/clips/"


async def download_music(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """A Flow Music song file: public objects in Google Cloud Storage, fetched without cookies
    (findings "Flow Music"). Only that bucket's clip folder is allowed."""
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != MUSIC_HOST
            or not parsed.path.startswith(MUSIC_PREFIX)):
        raise ValidationError(f"refusing to download from {parsed.scheme}://{parsed.hostname}"
                              f"{parsed.path}")
    owned = client is None
    http = client or httpx.AsyncClient(timeout=300.0, follow_redirects=False)
    try:
        response = await http.get(url)
    except httpx.HTTPError as exc:
        raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if owned:
            await http.aclose()
    if 200 <= response.status_code < 300:
        return response.content
    raise ServerError(f"Flow Music download failed (HTTP {response.status_code})",
                      hint="open the song in Flow Music and download it there")
