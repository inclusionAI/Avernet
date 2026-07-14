from typing import Protocol, Optional, List
from datetime import datetime


class AuditLogStore(Protocol):
    """Public audit log store contract.

    Implementations may be OSS defaults (file, database) or internal plugins (ZDAS).
    Public code must depend on this contract, not internal audit SDKs.
    """

    def log(self, event_type: str, event_data: dict, timestamp: datetime = None) -> bool:
        """Log an audit event.

        Args:
            event_type: Type of audit event
            event_data: Event data dict
            timestamp: Event timestamp (defaults to now)

        Returns:
            True if log successful, False otherwise.
        """
        ...

    def query(self, start_time: datetime, end_time: datetime, event_type: str = None, limit: int = 100) -> List[dict]:
        """Query audit logs within time range.

        Args:
            start_time: Start of time range
            end_time: End of time range
            event_type: Optional event type filter
            limit: Maximum number of results

        Returns:
            List of audit log entries.
        """
        ...

    def get_by_worker(self, worker_id: str, limit: int = 100) -> List[dict]:
        """Get audit logs for a worker.

        Args:
            worker_id: Unique worker identifier
            limit: Maximum number of results

        Returns:
            List of audit log entries.
        """
        ...