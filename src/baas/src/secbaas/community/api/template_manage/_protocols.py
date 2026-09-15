"""
Device Template Management Service Protocol.

Defines the SPI interface for device template CRUD operations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._enums import TemplateStatus
from ._models import (
    DeviceTemplateResponse,
    TemplateCreate,
    TemplateListResponse,
    TemplateStorageCapability,
    TemplateUpdate,
)


@runtime_checkable
class DeviceTemplateManageService(Protocol):
    """Protocol for device template management service."""

    def get_storage_capability(
        self,
        tenant: str,
        template_uuid: str,
        env: str | None = None,
    ) -> TemplateStorageCapability:
        """Check the ONLINE tenant-scoped template in the current server env.

        A mismatched requested env, unsupported provider, or missing Volume is
        not ready. The response never includes any template secrets.
        """
        ...

    def create_template(
        self, tenant: str, data: TemplateCreate
    ) -> DeviceTemplateResponse:
        """Create a new device template (default status: CREATED)."""
        ...

    def get_by_template_id(self, template_id: int) -> DeviceTemplateResponse | None:
        """Get template by template_id (PaaS platform tenant business ID)."""
        ...

    def get_default_or_explicit_template(
        self,
        tenant: str,
        template_uuid: str | None = None,
    ) -> DeviceTemplateResponse:
        """Get template: prefer explicit template_uuid, fall back to tenant default."""
        ...

    def get_online_template_by_uuid(
        self, tenant: str, template_uuid: str
    ) -> DeviceTemplateResponse | None:
        """Get ONLINE template by UUID (with tenant isolation)."""
        ...

    def update_template(
        self,
        tenant: str,
        template_uuid: str,
        status: TemplateStatus,
        data: TemplateUpdate,
    ) -> DeviceTemplateResponse | None:
        """Update template fields for a specific status version."""
        ...

    def update_status(
        self,
        tenant: str,
        template_uuid: str,
        current_status: TemplateStatus,
        new_status: TemplateStatus,
    ) -> DeviceTemplateResponse | None:
        """Update template status (CREATED -> AUDITED -> ONLINE <-> OFFLINE)."""
        ...

    def list_templates(
        self,
        tenant: str,
        status: TemplateStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> TemplateListResponse:
        """List templates for a tenant, optionally filtered by status."""
        ...

    def list_online_templates(
        self,
        tenant: str,
        page: int = 1,
        page_size: int = 20,
    ) -> TemplateListResponse:
        """List ONLINE templates for a tenant (used for Bot creation)."""
        ...

    def soft_delete_template(
        self,
        tenant: str,
        template_uuid: str,
        status: TemplateStatus,
        operator: str,
    ) -> bool:
        """Soft delete a device template."""
        ...
