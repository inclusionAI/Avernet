"""Service API Protocol for common configuration."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CommonConfigServiceProtocol(Protocol):
    def get_config(
        self,
        *,
        business_code: str,
        param_code: str,
        env: str,
        only_enabled: bool = False,
    ) -> dict[str, Any] | None: ...

    def get_value(
        self,
        *,
        business_code: str,
        param_code: str,
        env: str,
        default: Any = None,
        only_enabled: bool = True,
    ) -> Any: ...

    def list_configs(
        self,
        *,
        env: str,
        business_code: str | None = None,
        enable: str | None = None,
        keyword: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]: ...

    def create_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: Any,
        business_name: str | None = None,
        param_code: str,
        enable: str = "1",
        ext_info: Any = None,
        env: str,
    ) -> int: ...

    def update_config(self, *, config_id: int, updates: dict[str, Any]) -> bool: ...

    def upsert_config(
        self,
        *,
        business_code: str,
        param_name: str,
        param_value: Any,
        business_name: str | None = None,
        param_code: str,
        enable: str = "1",
        ext_info: Any = None,
        env: str,
    ) -> int: ...

    def delete_config(
        self,
        *,
        config_id: int | None = None,
        business_code: str | None = None,
        param_code: str | None = None,
        env: str | None = None,
    ) -> bool: ...

    def enable_config(self, *, config_id: int) -> bool: ...

    def disable_config(self, *, config_id: int) -> bool: ...
