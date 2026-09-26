from typing import Protocol, Optional, List


class WorkerProfileContentStore(Protocol):
    """Public worker profile content store contract.

    Implementations may be OSS defaults (SQLite, filesystem) or internal plugins (ZDAS).
    Public code must depend on this contract, not internal store SDKs.
    """

    def save(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Save profile content for a worker.

        Args:
            worker_id: Unique worker identifier
            profile_id: Profile identifier
            content: Profile content dict

        Returns:
            True if save successful, False otherwise.
        """
        ...

    def get(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Get profile content for a worker.

        Args:
            worker_id: Unique worker identifier
            profile_id: Profile identifier

        Returns:
            Profile content dict if found, None otherwise.
        """
        ...

    def list_profiles(self, worker_id: str) -> List[dict]:
        """List all profiles for a worker.

        Args:
            worker_id: Unique worker identifier

        Returns:
            List of profile metadata dicts.
        """
        ...

    def delete(self, worker_id: str, profile_id: str) -> bool:
        """Delete a profile.

        Args:
            worker_id: Unique worker identifier
            profile_id: Profile identifier

        Returns:
            True if deletion successful, False otherwise.
        """
        ...