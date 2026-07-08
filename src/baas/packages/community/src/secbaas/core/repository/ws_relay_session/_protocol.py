from typing import Any, Protocol, runtime_checkable

from ._record import WsRelaySessionRecord


@runtime_checkable
class WsRelaySessionRepository(Protocol):
    """Protocol for WS relay session repository."""

    def insert_init(
        self,
        *,
        session_id: str,
        machine_id: str,
        operator: str,
    ) -> int:
        """Insert a new relay session record with status='init'.

        Returns the new record ID.
        """
        ...

    def get_by_session_id(self, session_id: str) -> WsRelaySessionRecord | None:
        """Get relay session by business session_id (env-filtered)."""
        ...

    def update_active(
        self,
        *,
        session_id: str,
        connected_server_instance: str,
        connected_route_info: dict[str, Any],
    ) -> None:
        """Update status to 'active' with routing information."""
        ...

    def update_closed(self, *, session_id: str) -> None:
        """Update status to 'closed' and set gmt_close."""
        ...

    def _validate_transition(self, current: str, target: str) -> None:
        """Validate state transition. Raises DeviceCreationError on conflict."""
        ...
