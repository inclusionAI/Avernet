"""
SessionService Protocol — session management plugin interface.

Each engine implementation under engines/<name>/session.py provides a class that
structurally satisfies this Protocol. EngineManager exposes the active engine's
session plugin via `EngineManager.get_instance().session`.

Session data models (Session, Message, and the various request types) live in
`engine.community.core.session.models` and are re-used here rather than duplicated.

See src/engine/docs/heterogeneous-engine-architecture.md §5.1 — method naming
follows the generic CRUD-style template (list/create/delete/update).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.session.models import (
    Session,
    SessionClearRequest,
    SessionCreateRequest,
    SessionDeleteRequest,
    SessionHistoryRequest,
    SessionHistoryResult,
    SessionListRequest,
    SessionResetRequest,
    SessionResetResult,
    SessionUpdateRequest,
)
from engine.community.core.engine.context import AuthContext


@runtime_checkable
class SessionService(Protocol):
    """Backend talks to the session engine through this Protocol.

    All methods accept an optional `auth: AuthContext` — plugins that pool
    upstream connections per-tenant read `auth.token`; plugins that don't need
    per-user isolation ignore it. HTTP routers that don't propagate auth today
    just omit the argument (it defaults to `None`).
    """

    async def list(
        self, request: SessionListRequest, auth: AuthContext | None = None,
    ) -> list[Session]:
        """List sessions matching the request filter."""
        ...

    async def create(
        self, request: SessionCreateRequest, auth: AuthContext | None = None,
    ) -> Session:
        """Create a new session and return it."""
        ...

    async def delete(
        self, request: SessionDeleteRequest, auth: AuthContext | None = None,
    ) -> bool:
        """Delete a session. Returns True if the session existed and was removed."""
        ...

    async def clear(
        self, request: SessionClearRequest, auth: AuthContext | None = None,
    ) -> None:
        """Clear a session's history without deleting the session itself."""
        ...

    async def get_history(
        self, request: SessionHistoryRequest, auth: AuthContext | None = None,
    ) -> SessionHistoryResult:
        """Return the message history for a session."""
        ...

    async def update(
        self, request: SessionUpdateRequest, auth: AuthContext | None = None,
    ) -> Session:
        """Update session metadata (title, model, etc.) and return the updated object."""
        ...

    async def reset(
        self, request: SessionResetRequest, auth: AuthContext | None = None,
    ) -> SessionResetResult:
        """Reset a session to a clean state.

        Semantics are engine-specific — OpenClaw forwards to the gateway's
        `sessions.reset` RPC; other engines might tear down and recreate
        whatever backing state the session holds. Failures are reported via
        `ok=False` + `error_code/error_message` rather than exceptions, matching
        the in-band error shape of the underlying RPC.
        """
        ...


__all__ = ["SessionService"]
