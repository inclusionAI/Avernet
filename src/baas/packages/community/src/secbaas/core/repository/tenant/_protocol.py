from typing import Any, Protocol, runtime_checkable

from ._record import TenantRecord


@runtime_checkable
class TenantRepository(Protocol):
    """Protocol for tenant repository."""

    def insert_tenant(
        self,
        *,
        creator: str,
        modifier: str,
        name: str,
        description: str | None,
        env: str,
        extra_config: dict[str, Any] | None,
    ) -> int:
        """Insert a new tenant record. Returns the new record ID."""
        ...

    def get_by_id(self, id: int) -> TenantRecord | None:
        """Get tenant by primary key ID."""
        ...

    def get_by_name(self, name: str, env: str) -> TenantRecord | None:
        """Get tenant by name and env."""
        ...

    def update_tenant(
        self,
        *,
        name: str,
        env: str,
        modifier: str,
        description: str | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> int:
        """Update tenant fields. Returns affected row count."""
        ...

    def soft_delete(self, *, name: str, env: str, modifier: str) -> None:
        """Soft delete tenant by setting is_deleted to the record ID (per D-04)."""
        ...

    def list_tenants(
        self,
        *,
        env: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[TenantRecord]]:
        """List tenants with pagination.

        Returns: (total_count, list_of_records)
        """
        ...
