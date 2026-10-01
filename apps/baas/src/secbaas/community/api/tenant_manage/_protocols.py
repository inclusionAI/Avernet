"""
Tenant Management Service Protocol.

Defines the SPI interface for tenant CRUD operations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._models import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    TenantUpdate,
)


@runtime_checkable
class TenantManageService(Protocol):
    """Protocol for tenant management service."""

    def create_tenant(self, data: TenantCreate) -> TenantResponse:
        """Create a new tenant."""
        ...

    def get_tenant_by_name(self, name: str) -> TenantResponse | None:
        """Get tenant by name."""
        ...

    def get_tenant_config(self, name: str) -> TenantConfig | None:
        """Get tenant extra config."""
        ...

    def update_tenant(self, name: str, data: TenantUpdate) -> TenantResponse | None:
        """Update tenant."""
        ...

    def list_tenants(self, page: int = 1, page_size: int = 20) -> TenantListResponse:
        """List tenants."""
        ...

    def soft_delete_tenant(self, name: str, operator: str) -> bool:
        """Soft delete tenant."""
        ...
