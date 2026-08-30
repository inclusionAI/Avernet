"""Service API Protocol for system configuration.

Mirrors the public surface of
:class:`agentclaw.community.core.system_config.SystemConfigService` so HTTP routers
and other delivery adapters depend on this contract rather than on the
concrete service class. DI resolves the Protocol to the concrete
binding in :mod:`agentclaw.community.di.modules.system_config_module`.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SystemConfigServiceProtocol(Protocol):
    """Service API for system-wide configuration storage."""

    def list_categories(self, *, env: str) -> list[dict[str, Any]]: ...

    def get_category_by_id(self, *, category_id: int) -> dict[str, Any] | None: ...

    def create_category(
        self,
        *,
        category: str,
        category_name: str,
        description: str | None = None,
        env: str,
        operator: str | None = None,
    ) -> int: ...

    def delete_category(self, *, category_id: int) -> bool: ...

    def get_config(self, *, category: str, config_key: str, env: str) -> Any: ...

    def set_config(
        self,
        *,
        category: str,
        config_key: str,
        config_value: Any,
        env: str,
        description: str | None = None,
        operator: str | None = None,
    ) -> int: ...

    def delete_config(self, *, config_id: int) -> bool: ...

    def delete_config_by_key(
        self, *, category: str, config_key: str, env: str
    ) -> bool: ...

    def list_configs(self, *, category: str, env: str) -> list[dict[str, Any]]: ...

    def list_all_configs(self, *, env: str) -> list[dict[str, Any]]: ...
