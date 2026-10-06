"""Every MakerSuite RPC the client sends, with the transport proved in M1 (spec §5.1).

Transports come from `aistudio dev replay` results recorded in docs/wire/findings.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Transport(enum.Enum):
    HTTP = "http"
    BROWSER = "browser"


@dataclass(frozen=True)
class RpcSpec:
    method: str
    transport: Transport
    idempotent: bool


LIST_MODELS = RpcSpec("ListModels", Transport.HTTP, idempotent=True)
LIST_PROMPTS = RpcSpec("ListPrompts", Transport.HTTP, idempotent=True)
COUNT_TOKENS = RpcSpec("CountTokens", Transport.HTTP, idempotent=True)
LIST_APPLETS = RpcSpec("ListApplets", Transport.HTTP, idempotent=True)
LIST_RECENT_APPLETS = RpcSpec("ListRecentApplets", Transport.HTTP, idempotent=True)
LIST_BUNDLED_APPLETS = RpcSpec("ListBundledApplets", Transport.HTTP, idempotent=True)
LIST_APPLET_TRAJECTORIES = RpcSpec("ListAppletTrajectories", Transport.HTTP, idempotent=True)
LIST_INCIDENTS = RpcSpec("ListIncidentsHistory", Transport.HTTP, idempotent=True)
GET_PREFERENCES = RpcSpec("GetUserPreferences", Transport.HTTP, idempotent=True)
UPDATE_PREFERENCES = RpcSpec("UpdateUserPreferences", Transport.HTTP, idempotent=False)
GET_APPLET = RpcSpec("GetApplet", Transport.HTTP, idempotent=True)
GET_SNAPSHOT = RpcSpec("GetCodeAssistantSnapshot", Transport.HTTP, idempotent=True)
