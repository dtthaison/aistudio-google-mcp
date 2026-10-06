"""``aistudio dev replay``: re-send captured requests over plain HTTP to learn which RPCs
need BotGuard (spec §8.2).

Only read-only RPCs are sent unless the caller opts in, because replaying a create,
delete or generate call changes the account or spends quota and money.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aistudio._dev.fixtures import iter_fixtures, load_fixture
from aistudio._dev.redact import REDACTED_KEY
from aistudio._transport.http import HttpTransport, error_for_response
from aistudio.exceptions import AIStudioError

READ_ONLY_PREFIXES = ("List", "Get", "Count", "Search", "Fetch")
_READ_ONLY_RE = re.compile(rf"^({'|'.join(READ_ONLY_PREFIXES)})[A-Z]")
_MUTATING_WORD_RE = re.compile(
    r"Create|Update|Delete|Generate|Push|Remove|Insert|Upsert|Write|Move|Copy|Commit|Import"
    r"|Upload|Send|Cancel|Deploy|Publish|Rename|Patch|Modify|Mutate|Save|Edit"
)
_REACHED = {"VALIDATION_ERROR", "NOT_FOUND"}
_REJECTED = {"AUTH_ERROR", "BOTGUARD_REJECTED"}


def is_read_only(method: str) -> bool:
    return bool(_READ_ONLY_RE.match(method)) and not _MUTATING_WORD_RE.search(method)


def clean_url(url: str) -> str:
    """Drop query parameters that carry a redacted value; the signer adds real credentials."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if v != REDACTED_KEY]
    return urlunsplit(parts._replace(query=urlencode(query)))


@dataclass(frozen=True)
class ReplayResult:
    fixture: str
    method: str
    verdict: str
    http_status: int | None = None
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def replay_fixture(path: Path, transport: HttpTransport, *,
                         allow_mutating: bool = False) -> ReplayResult:
    data = load_fixture(path)
    label = data.get("method", "")
    request = data["request"]
    url_path = urlsplit(data["url"]).path
    if "/$rpc/" not in url_path:
        return ReplayResult(str(path), label, "skipped", message="not a MakerSuite $rpc call")
    # Gate on the method the request would actually hit, never on the fixture's own label.
    method = url_path.rsplit("/", 1)[-1]
    if label != method:
        return ReplayResult(str(path), label, "skipped",
                            message=f"fixture label {label!r} does not match URL method {method!r}")
    if not allow_mutating and not is_read_only(method):
        return ReplayResult(str(path), method, "skipped",
                            message="changes the account or spends quota; "
                                    "pass --allow-mutating to send it")
    if request.get("body_encoding"):
        return ReplayResult(str(path), method, "skipped",
                            message=f"{request['body_encoding']} request body")
    try:
        response = await transport.post_raw(clean_url(data["url"]), request.get("body", ""))
    except AIStudioError as exc:
        return ReplayResult(str(path), method, "error", error_code=exc.code, message=exc.message)
    if 200 <= response.status_code < 300:
        return ReplayResult(str(path), method, "http-ok", response.status_code)
    if 300 <= response.status_code < 400:
        target = urlsplit(response.headers.get("location", "")).hostname or "unknown host"
        return ReplayResult(str(path), method, "http-rejected", response.status_code, "REDIRECT",
                            f"redirected to {target}")
    err = error_for_response(response.status_code, response.text)
    if err.code in _REACHED:
        verdict = "http-reached"
    elif err.code in _REJECTED:
        verdict = "http-rejected"
    else:
        verdict = "error"
    return ReplayResult(str(path), method, verdict, response.status_code, err.code, err.message)


async def replay_all(target: Path, transport: HttpTransport, *, allow_mutating: bool = False,
                     delay: float = 0.5) -> list[ReplayResult]:
    paths = iter_fixtures(target) if target.is_dir() else [target]
    results: list[ReplayResult] = []
    for path in paths:
        result = await replay_fixture(path, transport, allow_mutating=allow_mutating)
        results.append(result)
        if result.verdict != "skipped" and delay:
            await asyncio.sleep(delay)
    return results
