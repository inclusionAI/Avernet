from typing import Protocol, Optional, List
from datetime import datetime


class WorkerRegistryStore(Protocol):
    """Public worker registry store contract.

    Implementations may be OSS defaults (SQLite, PostgreSQL) or internal plugins (ZDAS).
    Public code must depend on this contract, not internal store SDKs.
    """

    def register(self, worker_id: str, worker_info: dict) -> bool:
        """Register a new worker.

        Args:
            worker_id: Unique worker identifier
            worker_info: Worker metadata and capabilities

        Returns:
            True if registration successful, False otherwise.
        """
        ...

    def get(self, worker_id: str) -> Optional[dict]:
        """Get worker info by ID.

        Args:
            worker_id: Unique worker identifier

        Returns:
            Worker info dict if found, None otherwise.
        """
        ...

    def update(self, worker_id: str, updates: dict) -> bool:
        """Update worker info.

        Args:
            worker_id: Unique worker identifier
            updates: Fields to update

        Returns:
            True if update successful, False otherwise.
        """
        ...

    def delete(self, worker_id: str) -> bool:
        """Delete a worker.

        Args:
            worker_id: Unique worker identifier

        Returns:
            True if deletion successful, False otherwise.
        """
        ...

    def list_all(self) -> List[dict]:
        """List all registered workers.

        Returns:
            List of worker info dicts.
        """
        ...

    def find_by_capability(self, capability: str) -> List[dict]:
        """Find workers by capability.

        Args:
            capability: Required capability

        Returns:
            List of matching worker info dicts.
        """
        ...