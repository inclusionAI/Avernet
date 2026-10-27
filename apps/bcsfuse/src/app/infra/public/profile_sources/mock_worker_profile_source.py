"""
Mock Worker Profile Source - For Testing

Mock implementation for testing without external dependencies.
"""
from typing import List, Optional


class MockWorkerProfileSource:
    """
    Mock Worker Profile Source for OSS testing.

    Returns predefined mock profiles for testing.
    DO NOT use in production.
    """

    def __init__(self, profiles: Optional[List[dict]] = None):
        """Initialize mock profile source.

        Args:
            profiles: List of mock profiles to return.
        """
        self._profiles = profiles or [
            {
                "worker_id": "test-worker-1",
                "name": "Test Worker 1",
                "description": "A test worker for unit testing",
                "capabilities": ["test", "mock"],
                "config": {
                    "model": "test-model",
                    "temperature": 0.7,
                },
            },
            {
                "worker_id": "test-worker-2",
                "name": "Test Worker 2",
                "description": "Another test worker",
                "capabilities": ["test", "example"],
                "config": {
                    "model": "test-model-2",
                    "temperature": 0.5,
                },
            },
        ]

    def get_profile(self, worker_id: str) -> Optional[dict]:
        """Get profile by worker ID."""
        for profile in self._profiles:
            if profile.get("worker_id") == worker_id:
                return profile
        return None

    def list_profiles(self) -> List[dict]:
        """List all profiles."""
        return self._profiles

    def load(self) -> List[dict]:
        """Load all profiles."""
        return self._profiles