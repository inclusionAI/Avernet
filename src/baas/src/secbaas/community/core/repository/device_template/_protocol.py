"""
Device template repository for baas_device_template table.

Implements ZDAS sync SQL pattern with soft-delete support per D-04.
Note: Has tenant column but no env/domain (different from bot/device tables).
"""

from typing import Any, Protocol, runtime_checkable

from ._record import DeviceTemplateRecord


@runtime_checkable
class DeviceTemplateRepository(Protocol):
    """Protocol for device template repository."""

    def insert_template(
        self,
        *,
        template_uuid: str,
        template_id: int,
        type: str,
        tenant: str,
        creator: str,
        modifier: str,
        status: str,
        name: str,
        description: str | None,
        config: dict[str, Any] | None,
    ) -> int:
        """Insert a new device template record. Returns the new record ID."""
        ...

    def get_by_id(self, template_id: int, tenant: str) -> DeviceTemplateRecord | None:
        """Get device template by primary key ID with tenant isolation."""
        ...

    def get_by_template_id(self, template_id: int) -> DeviceTemplateRecord | None:
        """Get device template by business template_id.

        Note: template_id is globally unique, tenant parameter not required.
        """
        ...

    def get_by_template_uuid(
        self, template_uuid: str, tenant: str, status: str
    ) -> DeviceTemplateRecord | None:
        """Get device template by business UUID + status with tenant isolation.

        UK: (tenant, template_uuid, status, is_deleted) — status is required for uniqueness.
        """
        ...

    def list_by_template_uuid(
        self, template_uuid: str, tenant: str
    ) -> list[DeviceTemplateRecord]:
        """List all template records by UUID (all statuses) with tenant isolation."""
        ...

    def get_online_by_template_uuid(
        self, template_uuid: str, tenant: str
    ) -> DeviceTemplateRecord | None:
        """Get ONLINE template by UUID with tenant isolation."""
        ...

    def update_template(
        self,
        *,
        template_uuid: str,
        tenant: str,
        status: str,
        modifier: str,
        name: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> int:
        """Update device template fields with tenant isolation.

        Uses (tenant, template_uuid, status) as composite key per UK constraint.
        Returns affected row count.
        """
        ...

    def update_status(
        self, *, template_uuid: str, tenant: str, current_status: str, new_status: str
    ) -> None:
        """Update device template status with tenant isolation.

        Uses (tenant, template_uuid, current_status) to locate the template.
        Updates status to new_status.
        """
        ...

    def soft_delete(
        self, *, template_uuid: str, tenant: str, status: str, modifier: str
    ) -> None:
        """Soft delete template with tenant isolation.

        Uses (tenant, template_uuid, status) as composite key per UK constraint.
        Sets is_deleted to the record's ID.
        """
        ...

    def get_default_local_template_id(self) -> int | None:
        """Get the default Local template ID.

        Queries baas_device_template for the minimum template_id where:
        - type = 'Local'
        - status = 'ONLINE'
        - is_deleted = 0

        Returns:
            Minimum template_id value, or None if no matching template exists.
        """
        ...

    def list_templates(
        self,
        *,
        tenant: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceTemplateRecord]]:
        """List device templates for a tenant with optional filtering and pagination.

        Returns: (total_count, list_of_records)
        """
        ...
