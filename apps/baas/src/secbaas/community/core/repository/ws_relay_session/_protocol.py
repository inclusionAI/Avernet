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
        expected_statuses: frozenset[str],
    ) -> int:
        """CAS routing/status, filtered by env and source states; return affected rows."""
        ...

    def update_closed(
        self, *, session_id: str, expected_statuses: frozenset[str] | None = None
    ) -> int:
        """Write closed/gmt_close by env; return affected rows.

        Service transitions supply source states for CAS. None intentionally
        means unconditional terminal cleanup (LocalPaasService failure path).
        """
        ...
