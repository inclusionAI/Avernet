"""
In-Memory Worker Profile Content Store

In-memory implementation for testing without database dependencies.
"""
from typing import Optional, List
from datetime import datetime

from src.domain.models.worker_profile_content import (
    WorkerProfileContent,
    WorkerProfileContentList,
)


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

    def save(self, content: WorkerProfileContent) -> WorkerProfileContent:
        """Save a domain profile through the canonical persistence port."""
        stored = content.model_copy(deep=True)
        now = datetime.utcnow()
        previous = self._store.get((stored.worker_id, stored.profile_id))
        if previous is not None:
            previous_content = WorkerProfileContent.model_validate(previous)
            stored.created_at = previous_content.created_at
            stored.version = previous_content.version + 1
            stored.is_active = previous_content.is_active
        else:
            stored.created_at = stored.created_at or now
        stored.updated_at = now
        self._store[(stored.worker_id, stored.profile_id)] = stored.model_dump()
        return stored.model_copy(deep=True)

    def upsert_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Legacy dict write used by existing HTTP response adapters."""
        self._store[(worker_id, profile_id)] = dict(content)
        return True

    def create_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Create a new profile through the legacy dict API."""
        return self.upsert_profile(worker_id, profile_id, content)

    def get(
        self,
        worker_id: str,
        profile_id: str,
    ) -> Optional[WorkerProfileContent]:
        """Get profile content through the canonical persistence port."""
        content = self._store.get((worker_id, profile_id))
        if content is None:
            return None
        return WorkerProfileContent.model_validate(content)

    def get_profile(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Legacy dict view used by existing HTTP response adapters."""
        content = self._store.get((worker_id, profile_id))
        return dict(content) if content is not None else None

    def list_by_worker(self, worker_id: str) -> WorkerProfileContentList:
        """List profiles through the canonical persistence port."""
        items = [
            WorkerProfileContent.model_validate(content)
            for (stored_worker_id, _profile_id), content in self._store.items()
            if stored_worker_id == worker_id
        ]
        return WorkerProfileContentList(
            items=items,
            total=len(items),
            active_profile_id=self._active_profiles.get(worker_id),
        )

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

        for (stored_worker_id, stored_profile_id), profile in self._store.items():
            if stored_worker_id == worker_id and isinstance(profile, dict):
                profile["is_active"] = stored_profile_id == profile_id

        return True

    def activate(
        self,
        worker_id: str,
        profile_id: str,
    ) -> Optional[WorkerProfileContent]:
        if not self.activate_profile(worker_id, profile_id):
            return None
        return self.get(worker_id, profile_id)

    def get_active(self, worker_id: str) -> Optional[WorkerProfileContent]:
        profile_id = self._active_profiles.get(worker_id)
        return self.get(worker_id, profile_id) if profile_id else None

    def exists(self, worker_id: str, profile_id: str) -> bool:
        return (worker_id, profile_id) in self._store

    def count(self, worker_id: Optional[str] = None) -> int:
        if worker_id is None:
            return len(self._store)
        return sum(
            1
            for stored_worker_id, _profile_id in self._store
            if stored_worker_id == worker_id
        )

    def get_all_active(self) -> list[WorkerProfileContent]:
        return [
            content
            for worker_id, profile_id in self._active_profiles.items()
            if (content := self.get(worker_id, profile_id)) is not None
        ]

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
                profile = self.get_profile(worker_id, profile_id)
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

        profile = self.get_profile(worker_id, profile_id)
        if profile:
            return {
                "worker_id": worker_id,
                "profile_id": profile_id,
                "content": profile,
                "is_active": True,
            }
        return None
