"""
SQLite Worker Profile Content Store - OSS Wrapper

Wraps existing SQLite implementation for OSS compatibility.
"""
from typing import Optional, List
from src.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore as _SQLiteWorkerProfileContentStore


class SQLiteWorkerProfileContentStore(_SQLiteWorkerProfileContentStore):
    """
    SQLite Worker Profile Content Store for OSS.

    This is a thin wrapper around the existing SQLite implementation
    to maintain consistent naming and future extensibility.

    Suitable for development and single-instance deployments.
    For production, consider MySQLWorkerProfileContentStore.
    """

    def upsert_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """
        Upsert profile content (alias for save with dict conversion).

        Args:
            worker_id: Worker ID
            profile_id: Profile ID
            content: Profile content dict (supports 'content' -> 'soul_md' mapping)

        Returns:
            True if successful
        """
        from src.domain.models.worker_profile_content import WorkerProfileContent

        # Map 'content' to 'soul_md' if provided
        if isinstance(content, dict):
            # Extract known fields
            profile_data = {
                "worker_id": worker_id,
                "profile_id": profile_id,
            }

            # Map 'content' to 'soul_md'
            if "content" in content:
                profile_data["soul_md"] = content.pop("content")

            # Map other known fields
            for field in ["soul_md", "agents_md", "tools_md", "boot_md", "heartbeat_md",
                         "display_name", "description", "skill_sets", "metadata", "contents"]:
                if field in content:
                    profile_data[field] = content[field]

            profile_content = WorkerProfileContent(**profile_data)
        else:
            profile_content = content

        self.save(profile_content)
        return True

    def create_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Create a new profile (alias for upsert_profile)."""
        return self.upsert_profile(worker_id, profile_id, content)

    def get_profile(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """
        Get profile content as dict.

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            Profile data dict or None
        """
        profile = self.get(worker_id, profile_id)
        if profile is None:
            return None

        # Convert WorkerProfileContent to dict
        return {
            "worker_id": profile.worker_id,
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "soul_md": profile.soul_md,
            "agents_md": profile.agents_md,
            "tools_md": profile.tools_md,
            "boot_md": profile.boot_md,
            "heartbeat_md": profile.heartbeat_md,
            "contents": profile.contents,
            "skill_sets": profile.skill_sets,
            "metadata": profile.metadata,
            "content_type": profile.content_type.value if profile.content_type else "api",
            "is_active": profile.is_active,
            "version": profile.version,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }

    def list_profiles(self, worker_id: str) -> List[dict]:
        """
        List all profiles for a worker.

        Args:
            worker_id: Worker ID

        Returns:
            List of profile dicts
        """
        result = self.list_by_worker(worker_id)
        profiles = []

        for item in result.items:
            profiles.append({
                "worker_id": item.worker_id,
                "profile_id": item.profile_id,
                "display_name": item.display_name,
                "is_active": item.is_active,
                "version": item.version,
            })

        return profiles

    def delete_profile(self, worker_id: str, profile_id: str) -> bool:
        """Delete profile (alias for delete)."""
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
        result = self.activate(worker_id, profile_id)
        return result is not None

    def get_active_profiles(self, worker_ids: Optional[List[str]] = None) -> List[dict]:
        """
        Get all active profiles.

        Args:
            worker_ids: Optional list of worker IDs to filter by

        Returns:
            List of active profile data
        """
        # Get all active profiles from underlying store
        all_active = self.get_all_active()

        # Filter by worker_ids if provided
        if worker_ids:
            all_active = [p for p in all_active if p.worker_id in worker_ids]

        # Convert to dict format
        profiles = []
        for profile in all_active:
            profiles.append({
                "worker_id": profile.worker_id,
                "profile_id": profile.profile_id,
                "display_name": profile.display_name,
                "is_active": True,
            })

        return profiles

    def get_active_profile_for_worker(self, worker_id: str) -> Optional[dict]:
        """
        Get the active profile for a specific worker.

        Args:
            worker_id: Worker ID

        Returns:
            Active profile data or None
        """
        profile = self.get_active(worker_id)
        if profile is None:
            return None

        return {
            "worker_id": profile.worker_id,
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "is_active": True,
        }