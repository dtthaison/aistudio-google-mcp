"""Exception hierarchy shared by the client, CLI and MCP layers.

Every error carries a stable ``code`` (used in ``--json`` envelopes and MCP tool
errors), an optional ``hint`` telling the user what to do next, and a
``retriable`` flag for the retry middleware.
"""

from __future__ import annotations

_LOGIN_HINT = 'run "aistudio login"'
# An account that never allowed AI Studio to use Google Drive, where prompts live (live:
# ListPrompts; the page itself shows "Allow Drive access"). Signing in again cannot fix it.
_DRIVE_DENIED = "unauthorized_client"
_DRIVE_HINT = ('open https://aistudio.google.com/library in this account and press '
               '"Allow access", then re-run')


class AIStudioError(Exception):
    code = "ERROR"
    default_retriable = False

    def __init__(
        self, message: str, *, hint: str | None = None, retriable: bool | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retriable = self.default_retriable if retriable is None else retriable

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"error": True, "code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data


class AuthError(AIStudioError):
    code = "AUTH_ERROR"


class RateLimitError(AIStudioError):
    code = "RATE_LIMITED"
    default_retriable = True


class QuotaExceededError(AIStudioError):
    code = "QUOTA_EXCEEDED"


class BotGuardError(AIStudioError):
    code = "BOTGUARD_REJECTED"


class SchemaDriftError(AIStudioError):
    code = "SCHEMA_DRIFT"

    def __init__(self, method: str, index_path: tuple[int, ...], expected: str) -> None:
        path = "".join(f"[{i}]" for i in index_path) or "root"
        super().__init__(
            f"{method}: expected {expected} at {path}; the response shape changed",
            hint=f're-capture {method} with "aistudio dev capture"',
        )
        self.method = method
        self.index_path = index_path
        self.expected = expected


class NotFoundError(AIStudioError):
    code = "NOT_FOUND"


class ValidationError(AIStudioError):
    code = "VALIDATION_ERROR"


class NetworkError(AIStudioError):
    code = "NETWORK_ERROR"
    default_retriable = True


class ConnectFailedError(NetworkError):
    """The connection failed before the request was sent; always safe to retry."""


class ServerError(AIStudioError):
    code = "SERVER_ERROR"


class BrowserError(AIStudioError):
    code = "BROWSER_ERROR"


class WindowClosedError(BrowserError):
    """Chrome's window or page closed mid-run: never retried or counted as a slow reply."""


class WaitTimeoutError(AIStudioError):
    code = "TIMEOUT"


class OverBudgetError(AIStudioError):
    code = "OVER_BUDGET"


class PlanMismatchError(AIStudioError):
    code = "PLAN_MISMATCH"


class AgentReplyError(AIStudioError):
    code = "AGENT_REPLY"


def error_from_status(status: int, message: str) -> AIStudioError:
    """Map a gRPC status from an error envelope to an exception (spec §9)."""
    quota = "quota" in message.lower()
    if status == 3:
        if "api key" in message.lower():
            return AuthError(message, hint=_LOGIN_HINT)
        return ValidationError(message)
    if status == 5:
        return NotFoundError(message)
    if status == 8:
        return QuotaExceededError(message) if quota else RateLimitError(message)
    if status == 16 and _DRIVE_DENIED in message:
        return AuthError(f"this account has not allowed AI Studio to use Google Drive ({message})",
                         hint=_DRIVE_HINT)
    if status in (7, 16):
        return QuotaExceededError(message) if quota else AuthError(message, hint=_LOGIN_HINT)
    if status in (13, 14):
        return ServerError(message, retriable=True)
    return ServerError(f"gRPC status {status}: {message}")


def error_from_http(status: int, body_text: str) -> AIStudioError:
    """Fallback mapping for a non-2xx response without a decodable error envelope."""
    snippet = body_text.strip()[:200]
    message = f"HTTP {status}" + (f": {snippet}" if snippet else "")
    if status in (401, 403) or (status == 400 and "api key" in snippet.lower()):
        return AuthError(message, hint=_LOGIN_HINT)
    if status == 404:
        return NotFoundError(message)
    if status == 429:
        return RateLimitError(message)
    if status >= 500:
        return ServerError(message, retriable=True)
    return ValidationError(message)
