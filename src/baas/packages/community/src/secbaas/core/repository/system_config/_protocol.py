from typing import Protocol, runtime_checkable

from ._record import SystemConfigRecord


@runtime_checkable
class SystemConfigRepository(Protocol):
    """Protocol for system config repository."""

    def insert_config(
        self,
        *,
        conf_key: str,
        conf_value: str | None,
        env: str,
        name: str,
        description: str | None,
        creator: str,
        modifier: str,
    ) -> int:
        """Insert a new config record. Returns the new record ID."""
        ...

    def get_by_id(self, config_id: int) -> SystemConfigRecord | None:
        """Get config by primary key ID."""
        ...

    def get_by_env_and_key(self, env: str, conf_key: str) -> SystemConfigRecord | None:
        """Get config by (env, conf_key) unique key."""
        ...

    def update_config(
        self,
        *,
        config_id: int,
        conf_value: str | None = None,
        name: str | None = None,
        description: str | None = None,
        modifier: str | None = None,
    ) -> int:
        """Update config fields. Returns affected row count."""
        ...

    def delete_config(self, *, config_id: int) -> int:
        """Hard delete config by ID (per D-01, no soft-delete). Returns affected row count."""
        ...

    def list_configs(
        self,
        *,
        env: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[SystemConfigRecord]]:
        """List configs with optional filtering and pagination.

        Returns: (total_count, list_of_records)
        """
        ...
