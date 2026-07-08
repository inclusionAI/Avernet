"""
System Config Management Service Protocol.

Defines the SPI interface for system config CRUD operations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._models import (
    SystemConfigCreate,
    SystemConfigListResponse,
    SystemConfigResponse,
    SystemConfigUpdate,
)


@runtime_checkable
class SystemConfigManageService(Protocol):
    """Protocol for system config management service."""

    def create_config(self, data: SystemConfigCreate) -> SystemConfigResponse:
        """Create a new system config."""
        ...

    def get_config(self, conf_key: str) -> SystemConfigResponse | None:
        """Get config by conf_key for current environment."""
        ...

    def update_config(
        self, conf_key: str, data: SystemConfigUpdate
    ) -> SystemConfigResponse | None:
        """Update system config."""
        ...

    def delete_config(self, conf_key: str) -> bool:
        """Delete system config for current environment."""
        ...

    def list_configs(
        self, page: int = 1, page_size: int = 20
    ) -> SystemConfigListResponse:
        """List system configs for current environment."""
        ...
