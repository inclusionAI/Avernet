"""
设备绑定数据访问层

参考 agentclaw 项目实现，使用 @with_db_session 装饰器管理数据库连接
"""

from typing import Any, Protocol, runtime_checkable

from ._record import DeviceBindingRecord


@runtime_checkable
class DeviceBindingRepository(Protocol):
    """Protocol for device binding repository."""

    def insert_binding(
        self,
        *,
        entity_id: str,
        entity_type: str,
        device_id: str,
        device_provider: str,
        env: str,
        device_props: dict[str, Any],
        status: str,
        apply_reason: str | None,
        applied_by: str,
    ) -> int: ...

    def get_by_id(self, binding_id: int) -> DeviceBindingRecord | None: ...

    def get_by_device_id(self, device_id: str) -> DeviceBindingRecord | None: ...

    def release_binding(
        self,
        *,
        binding_id: int,
        release_reason: str | None,
        released_by: str,
    ) -> None: ...

    def update_status(self, *, binding_id: int, status: str) -> None: ...

    def update_status_and_alive_at(self, *, binding_id: int, status: str) -> None: ...

    def list_bindings(
        self,
        *,
        entity_id: str | None = None,
        entity_type: str | None = None,
        device_provider: str | None = None,
        env: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceBindingRecord]]: ...

    def list_bindings_by_providers(
        self,
        *,
        providers: list[str],
        env: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceBindingRecord]]:
        """List bindings filtered by multiple device providers.

        Args:
            providers: device provider list, e.g. ["arca", "baas"]
            env: environment filter
            status: status filter
            page: page number
            page_size: items per page
        """
        ...

    def count_non_released_bindings(
        self,
        *,
        entity_id: str,
        entity_type: str,
        env: str,
    ) -> int: ...

    def exists_device_id(
        self,
        *,
        device_id: str,
    ) -> bool: ...

    def get_released_binding(
        self,
        *,
        device_id: str,
    ) -> DeviceBindingRecord | None: ...

    def reuse_binding(
        self,
        *,
        binding_id: int,
        device_props: dict[str, Any],
        apply_reason: str | None,
        applied_by: str,
        status: str = "PENDING",
    ) -> None: ...

    def delete_binding(self, binding_id: int) -> bool: ...

    def exists(self, binding_id: int) -> bool: ...

    def update_device_props_ttl(
        self,
        *,
        binding_id: int,
        ttl_expiration_timestamp: int,
        ttl_expiration_time: str,
        refresh_fail_count: int = 0,
    ) -> None: ...

    def list_active_sandboxes_with_bot(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str | None = None,
        device_provider: str | None = None,
        sort_by: str = "id",
        sort_order: str = "desc",
    ) -> tuple[int, list[tuple["DeviceBindingRecord", dict[str, Any]]]]: ...

    def list_sandboxes_by_bot(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str | None = None,
    ) -> tuple[dict[str, Any] | None, list["DeviceBindingRecord"]]: ...

    def get_binding_by_sandbox_id(
        self,
        *,
        sandbox_id: str,
    ) -> "DeviceBindingRecord | None": ...

    def get_binding_by_sandbox_id_like(
        self,
        *,
        sandbox_id_prefix: str,
    ) -> "DeviceBindingRecord | None": ...

    def list_by_device_id(
        self,
        *,
        device_id: str,
        status: str = "ACTIVE",
        env: str | None = None,
    ) -> list[DeviceBindingRecord]: ...

    # ========== Bot Health Checker Methods ==========

    def list_all_active_bot_device(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str = "prod",
        bot_type: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def get_bot_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> dict[str, Any] | None: ...

    def get_publish_binding(
        self,
        *,
        source_bot_id: str,
        status: str,  # "validating" | "success"
    ) -> int | None: ...

    def list_paas_device_by_bot_personal(
        self,
        *,
        bot_id: str,
        binding_id: int,
    ) -> list[dict[str, Any]]: ...

    def list_paas_device_by_bot_service(
        self,
        *,
        bot_id: str,
        statuses: list[str],  # ["draft", "validating", "online"]
    ) -> list[dict[str, Any]]: ...

    def update_baas_device_ttl(
        self,
        *,
        device_uuid: str,
        ttl_expiration_time: str,
        ttl_expiration_timestamp: int,
    ) -> None: ...

    def update_baas_device_ttl_by_id(
        self,
        *,
        baas_device_id: int,
        ttl_expiration_time: str,
        ttl_expiration_timestamp: int,
        refresh_fail_count: int = 0,
    ) -> None: ...

    def update_device_props_refresh_fail_count(
        self,
        *,
        binding_id: int,
        refresh_fail_count: int,
    ) -> None: ...

    def update_baas_device_refresh_fail_count_by_id(
        self,
        *,
        baas_device_id: int,
        refresh_fail_count: int,
    ) -> None: ...

    def export_device_all(self) -> list[tuple[str, str, str]]: ...

    def export_device_list(
        self,
        *,
        env: str = "pre",
    ) -> list[tuple[str, str, str]]: ...

    def get_baas_device_by_id(
        self, *, baas_device_id: int
    ) -> dict[str, Any] | None: ...

    def list_baas_devices_active_paginated(
        self,
        *,
        env: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    def update_baas_device_status_by_id(
        self, *, baas_device_id: int, status: str, modifier: str = "system"
    ) -> None: ...

    def update_device_props_ttl_by_paas_device_id(
        self,
        *,
        paas_device_id: str,
        ttl_expiration_timestamp: int,
        ttl_expiration_time: str,
    ) -> None: ...

    def list_bindings_by_ttl_asc(
        self,
        *,
        limit: int = 100,
    ) -> list[DeviceBindingRecord]:
        """查询 ac_entity_device_binding 中 ACTIVE 且有 sandbox_id 的记录，
        按 TTL 过期时间 ASC 排序，取前 limit 条。

        用于 DeviceTtlTimer 定时任务：优先处理即将过期的个人 bot 设备。
        """
        ...

    def list_baas_devices_by_ttl_asc(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """查询 baas_device 中 ACTIVE 且有 sandbox_id 的记录，
        按 TTL 过期时间 ASC 排序，取前 limit 条。

        用于 DeviceTtlTimer 定时任务：优先处理即将过期的服务 bot 设备。
        """
        ...
