"""Transport-independent relay routing read model and Service API."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(slots=True)
class WsRelaySessionInfo:
    """Detached relay routing snapshot returned to API consumers."""

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    session_id: str
    machine_id: str
    connected_server_instance: str
    status: str
    env: str
    gmt_close: datetime | None
    connected_route_info: dict[str, Any] | None
    operator: str


@runtime_checkable
class WsRelaySessionService(Protocol):
    """Relay session Service API, implemented in core and consumed by adapters.

    Version 1: read a routing snapshot or transition init -> active/closed,
    active -> closed. Same-state requests are no-ops: they never overwrite
    routing or close time. Unknown sessions raise RELAY_SESSION_NOT_FOUND;
    disallowed or concurrent reverse transitions raise RELAY_STATE_CONFLICT;
    a non-idempotent activation without route/server raises INVALID_PARAMS.
    Errors are DeviceCreationError; persistence failures propagate unchanged.
    """

    def get_by_session_id(self, session_id: str) -> WsRelaySessionInfo:
        """Return an env-scoped snapshot, or raise when no session exists."""
        ...

    def update_status(
        self,
        *,
        session_id: str,
        status: Literal["active", "closed"],
        connected_server_instance: str | None = None,
        connected_route_info: dict[str, Any] | None = None,
    ) -> str:
        """Apply a CAS transition, or accept an already-satisfied status."""
        ...
