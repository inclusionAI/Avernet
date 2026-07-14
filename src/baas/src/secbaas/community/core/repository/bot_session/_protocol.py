from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ._record import BotSessionRecord


@runtime_checkable
class BotSessionRepository(Protocol):
    """Protocol for bot session repository."""

    def insert_session(
        self,
        *,
        bot_uuid: str,
        invoker: str,
        session_id: str,
        req: dict[str, Any] | None,
        result: dict[str, Any] | None,
        err_msg: str | None,
        context: dict[str, Any] | None,
        status: str,
        device_uuid: str,
        tenant: str,
    ) -> int:
        """Insert a new session record. Returns the new record ID."""
        ...

    def get_by_id(self, session_pk_id: int) -> BotSessionRecord | None:
        """Get session by primary key ID."""
        ...

    def get_by_session_id(self, session_id: str) -> BotSessionRecord | None:
        """Get session by business session_id."""
        ...

    def update_result(
        self,
        *,
        session_id: str,
        result: dict[str, Any] | None,
        err_msg: str | None,
        status: str,
    ) -> None:
        """Update session result, error message, and status."""
        ...

    def update_status(self, *, session_id: str, status: str) -> None:
        """Update session status."""
        ...

    def update_context(
        self,
        *,
        session_id: str,
        context: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        err_msg: str | None = None,
    ) -> None:
        """Update session context, result, and/or error message."""
        ...

    def list_by_bot_uuid(
        self,
        *,
        bot_uuid: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[BotSessionRecord]]:
        """List sessions by bot UUID with pagination.

        Returns: (total_count, list_of_records)
        """
        ...

    def list_by_session_ids(self, session_ids: list[str]) -> list[BotSessionRecord]:
        """Get sessions by list of session IDs."""
        ...

    def list_by_time_range(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        bot_uuid: str | None = None,
    ) -> list[BotSessionRecord]:
        """List sessions within a time range.

        Args:
            start_time: Start of time range
            end_time: End of time range
            bot_uuid: Optional bot UUID filter
        """
        ...

    def list_by_bot_device_invoker(
        self,
        *,
        bot_uuid: str,
        device_uuid: str | None,
        invoker: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[BotSessionRecord]:
        """List sessions by bot, device, invoker with time range.

        Uses idx_bot_dev_ivk_time composite index for efficient lookup.
        device_uuid is optional to allow listing sessions by bot+invoker only.

        Args:
            bot_uuid: Bot UUID
            device_uuid: Device UUID (optional, None means all devices)
            invoker: Invoker identifier
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)
        """
        ...

    def count_active_sessions_by_device(self, *, device_uuid: str, tenant: str) -> int:
        """Count active sessions (PENDING, RUNNING) for a device.

        Args:
            device_uuid: Device UUID to count sessions for
            tenant: Tenant name for multi-tenant isolation

        Returns:
            Count of sessions with status PENDING or RUNNING
        """
        ...
