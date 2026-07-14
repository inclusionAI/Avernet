"""
In-Memory Worker Profile Content Store

In-memory implementation for testing without database dependencies.
"""
from typing import Optional, List
from datetime import datetime


class InMemoryWorkerProfileContentStore:
    """
    In-Memory Worker Profile Content Store for OSS testing.

    Suitable for testing only. DO NOT use in production.
    Data is NOT persisted and is lost on restart.
    """

    def __init__(self):
        """Initialize in-memory store."""
        self._store: dict[tuple[str, str], dict] = {}
        self._active_profiles: dict[str, str] = {}  # worker_id -> active profile_id

    def save(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Save profile content."""
        self._store[(worker_id, profile_id)] = content
        return True

    def upsert_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Alias for save() for API consistency."""
        return self.save(worker_id, profile_id, content)

    def create_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Create a new profile (alias for save)."""
        return self.save(worker_id, profile_id, content)

    def get(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Get profile content."""
        return self._store.get((worker_id, profile_id))

    def get_profile(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Alias for get() for API consistency."""
        return self.get(worker_id, profile_id)

    def list_profiles(self, worker_id: str) -> List[dict]:
        """List all profiles for a worker."""
        profiles = []
        for (wid, pid), content in self._store.items():
            if wid == worker_id:
                profiles.append({
                    "profile_id": pid,
                    "worker_id": wid,
                    "content": content,
                    "is_active": self._active_profiles.get(wid) == pid,
                })
        return profiles

    def delete(self, worker_id: str, profile_id: str) -> bool:
        """Delete profile content."""
        key = (worker_id, profile_id)
        if key in self._store:
            del self._store[key]
            # Clear active profile if deleted
            if self._active_profiles.get(worker_id) == profile_id:
                del self._active_profiles[worker_id]
            return True
        return False

    def delete_profile(self, worker_id: str, profile_id: str) -> bool:
        """Alias for delete() for API consistency."""
        return self.delete(worker_id, profile_id)

    def activate_profile(self, worker_id: str, profile_id: str) -> bool:
        """
        Mark a profile as active for a worker.

        Args:
            worker_id: Worker ID
            profile_id: Profile ID to activate

        Returns:
            True if profile exists and was activated, False otherwise
        """
        key = (worker_id, profile_id)
        if key not in self._store:
            return False

        self._active_profiles[worker_id] = profile_id

        # Update the profile's is_active flag
        profile = self._store[key]
        if isinstance(profile, dict):
            profile['is_active'] = True
            profile['activated_at'] = datetime.utcnow().isoformat()

        return True

    def get_active_profiles(self, worker_ids: Optional[List[str]] = None) -> List[dict]:
        """
        Get all active profiles.

        Args:
            worker_ids: Optional list of worker IDs to filter by

        Returns:
            List of active profile data
        """
        active = []
        for worker_id, profile_id in self._active_profiles.items():
            if worker_ids is None or worker_id in worker_ids:
                profile = self.get(worker_id, profile_id)
                if profile:
                    active.append({
                        "worker_id": worker_id,
                        "profile_id": profile_id,
                        "content": profile,
                        "is_active": True,
                    })
        return active

    def get_active_profile_for_worker(self, worker_id: str) -> Optional[dict]:
        """
        Get the active profile for a specific worker.

        Args:
            worker_id: Worker ID

        Returns:
            Active profile data or None
        """
        profile_id = self._active_profiles.get(worker_id)
        if not profile_id:
            return None

        profile = self.get(worker_id, profile_id)
        if profile:
            return {
                "worker_id": worker_id,
                "profile_id": profile_id,
                "content": profile,
                "is_active": True,
            }
        return None