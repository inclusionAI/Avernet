"""Sandbox Device Router - 按 table_type 路由沙箱设备查询、告警、续期操作。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from secbaas.api.device_manage import DeviceStatus
from secbaas.api.health_check.sandbox import (
    SandboxDeviceRouter as SandboxDeviceRouterProtocol,
)
from secbaas.logger import get_logger

if TYPE_CHECKING:
    from secbaas.core.repository.device_binding import DeviceBindingRepository
    from secbaas.core.service.paas import PaasServiceFacade

logger = get_logger("core-service")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WARNING_THRESHOLD = 10


# ---------------------------------------------------------------------------
# 枚举 & 数据模型
# ---------------------------------------------------------------------------


class TableType(StrEnum):
    AC_BINDING = "ac_binding"
    BAAS = "baas"


@dataclass
class SandboxDeviceInfo:
    """沙箱设备摘要信息（从 JSON 字段解析）。"""

    table_id: int
    table_type: str
    sandbox_id: str | None
    ttl_expiration_time: str | None
    ttl_expiration_timestamp: int | None
    refresh_fail_count: int
    status: str


@dataclass
class PaginatedResult:
    """分页查询结果。"""

    total: int
    page: int
    page_size: int
    items: list[SandboxDeviceInfo]


@dataclass
class WarnResult:
    """告警操作结果。"""

    table_id: int
    table_type: str
    action: str  # "STOPPED" | "RESET" | "INCREMENT"
    refresh_fail_count: int


@dataclass
class RenewTtlResult:
    """续期操作结果。"""

    table_id: int
    table_type: str
    device_id: str
    success: bool
    old_expiration_time: str | None = None
    new_expiration_time: str | None = None
    refresh_fail_count: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Handler Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SandboxDeviceHandler(Protocol):
    """按表类型路由的沙箱设备操作接口。"""

    def query_active_sandboxes(
        self, *, env: str, page: int, page_size: int
    ) -> PaginatedResult: ...

    async def warn_device(self, *, table_id: int) -> WarnResult: ...

    async def renew_ttl(self, *, table_id: int) -> RenewTtlResult: ...


# ---------------------------------------------------------------------------
# JSON 字段解析辅助
# ---------------------------------------------------------------------------


def _parse_device_props(props: Any) -> dict[str, Any]:
    if props is None:
        return {}
    if isinstance(props, str):
        try:
            return json.loads(props)
        except (json.JSONDecodeError, TypeError):
            return {}
    return props if isinstance(props, dict) else {}


def _extract_sandbox_info_from_binding(record: Any) -> SandboxDeviceInfo:
    """从 DeviceBindingRecord 提取沙箱信息。"""
    dp = _parse_device_props(record.device_props)
    return SandboxDeviceInfo(
        table_id=record.id,
        table_type=TableType.AC_BINDING,
        sandbox_id=dp.get("sandbox_id"),
        ttl_expiration_time=dp.get("ttl_expiration_time"),
        ttl_expiration_timestamp=dp.get("ttl_expiration_timestamp"),
        refresh_fail_count=dp.get("refresh_fail_count", 0),
        status=record.status,
    )


def _extract_sandbox_info_from_device(record: Any) -> SandboxDeviceInfo:
    """从 DeviceRecord 提取沙箱信息。"""
    dp = _parse_device_props(record.provider_device_props)
    return SandboxDeviceInfo(
        table_id=record.id,
        table_type=TableType.BAAS,
        sandbox_id=dp.get("sandbox_id"),
        ttl_expiration_time=dp.get("ttl_expiration_time"),
        ttl_expiration_timestamp=dp.get("ttl_expiration_timestamp"),
        refresh_fail_count=dp.get("refresh_fail_count", 0),
        status=record.status,
    )


# ---------------------------------------------------------------------------
# AcBinding Handler
# ---------------------------------------------------------------------------


class AcBindingSandboxHandler:
    """ac_entity_device_binding 表的沙箱设备操作。"""

    def __init__(
        self,
        binding_repo: DeviceBindingRepository,
        paas_facade: PaasServiceFacade,
    ):
        self._binding_repo = binding_repo
        self._paas_facade = paas_facade

    def query_active_sandboxes(
        self, *, env: str, page: int, page_size: int
    ) -> PaginatedResult:
        # 只查询 arca 和 baas 类型的设备
        total, records = self._binding_repo.list_bindings_by_providers(
            providers=["arca", "baas"],
            env=env,
            status="ACTIVE",
            page=page,
            page_size=page_size,
        )
        items = [_extract_sandbox_info_from_binding(r) for r in records]
        return PaginatedResult(total=total, page=page, page_size=page_size, items=items)

    async def warn_device(self, *, table_id: int) -> WarnResult:
        record = self._binding_repo.get_by_id(binding_id=table_id)
        if record is None:
            raise ValueError(f"Binding record not found: id={table_id}")

        # 非 ACTIVE 状态不探活
        if record.status != "ACTIVE":
            return WarnResult(
                table_id=table_id,
                table_type=TableType.AC_BINDING,
                action="SKIP",
                refresh_fail_count=0,
            )

        dp = _parse_device_props(record.device_props)
        sandbox_id = dp.get("sandbox_id")
        # 没有 sandbox_id 不处理
        if not sandbox_id:
            return WarnResult(
                table_id=table_id,
                table_type=TableType.AC_BINDING,
                action="SKIP",
                refresh_fail_count=0,
            )

        fail_count = dp.get("refresh_fail_count", 0)

        if fail_count >= WARNING_THRESHOLD:
            self._binding_repo.update_status(
                binding_id=table_id, status=DeviceStatus.STOPPED.value
            )
            return WarnResult(
                table_id=table_id,
                table_type=TableType.AC_BINDING,
                action="STOPPED",
                refresh_fail_count=fail_count,
            )

        # 探活
        sandbox_id = dp.get("sandbox_id")
        try:
            device_info = await self._paas_facade.get_device_info(sandbox_id)
            new_count = 0
            # 更新 TTL 字段
            if hasattr(device_info, "ttl_timestamp") and device_info.ttl_timestamp:
                ttl_time_str = datetime.fromtimestamp(
                    device_info.ttl_timestamp / 1000
                ).strftime("%Y-%m-%d %H:%M:%S")
                self._binding_repo.update_device_props_ttl(
                    binding_id=table_id,
                    ttl_expiration_timestamp=device_info.ttl_timestamp,
                    ttl_expiration_time=ttl_time_str,
                    refresh_fail_count=0,
                )
            else:
                self._binding_repo.update_device_props_refresh_fail_count(
                    binding_id=table_id, refresh_fail_count=new_count
                )
            action = "RESET"
        except Exception as e:
            logger.warning(
                f"[AcBindingSandboxHandler] Probe failed for {sandbox_id}: {e}"
            )
            new_count = fail_count + 1
            self._binding_repo.update_device_props_refresh_fail_count(
                binding_id=table_id, refresh_fail_count=new_count
            )
            action = "INCREMENT"

        return WarnResult(
            table_id=table_id,
            table_type=TableType.AC_BINDING,
            action=action,
            refresh_fail_count=new_count,
        )

    async def renew_ttl(self, *, table_id: int) -> RenewTtlResult:
        record = self._binding_repo.get_by_id(binding_id=table_id)
        if record is None:
            raise ValueError(f"Binding record not found: id={table_id}")

        dp = _parse_device_props(record.device_props)
        sandbox_id = dp.get("sandbox_id")
        if not sandbox_id:
            raise ValueError(f"No sandbox_id in device_props for binding id={table_id}")

        old_ttl = dp.get("ttl_expiration_time")

        try:
            ttl_info = await self._paas_facade.update_device_ttl(
                paas_device_id=sandbox_id
            )
            if ttl_info.success and ttl_info.new_expiration_time:
                ttl_timestamp = int(ttl_info.new_expiration_time.timestamp() * 1000)
                ttl_time_str = ttl_info.new_expiration_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                self._binding_repo.update_device_props_ttl(
                    binding_id=table_id,
                    ttl_expiration_timestamp=ttl_timestamp,
                    ttl_expiration_time=ttl_time_str,
                    refresh_fail_count=0,
                )
                return RenewTtlResult(
                    table_id=table_id,
                    table_type=TableType.AC_BINDING,
                    device_id=sandbox_id,
                    success=True,
                    old_expiration_time=old_ttl,
                    new_expiration_time=ttl_time_str,
                    refresh_fail_count=0,
                )
            else:
                return RenewTtlResult(
                    table_id=table_id,
                    table_type=TableType.AC_BINDING,
                    device_id=sandbox_id,
                    success=False,
                    old_expiration_time=old_ttl,
                    refresh_fail_count=dp.get("refresh_fail_count", 0),
                    error=ttl_info.error or "TTL extension failed",
                )
        except Exception as e:
            logger.warning(
                f"[AcBindingSandboxHandler] Renew TTL failed for {sandbox_id}: {e}"
            )
            return RenewTtlResult(
                table_id=table_id,
                table_type=TableType.AC_BINDING,
                device_id=sandbox_id,
                success=False,
                old_expiration_time=old_ttl,
                refresh_fail_count=dp.get("refresh_fail_count", 0),
                error=str(e),
            )


# ---------------------------------------------------------------------------
# Baas Handler
# ---------------------------------------------------------------------------


class BaasSandboxHandler:
    """baas_device 表的沙箱设备操作。"""

    def __init__(
        self,
        binding_repo: DeviceBindingRepository,
        paas_facade: PaasServiceFacade,
    ):
        self._binding_repo = binding_repo
        self._paas_facade = paas_facade

    def query_active_sandboxes(
        self, *, env: str, page: int, page_size: int
    ) -> PaginatedResult:
        # 使用 binding_repo 的 list_baas_devices_active_paginated
        total, rows = self._binding_repo.list_baas_devices_active_paginated(
            env=env, page=page, page_size=page_size
        )
        items = []
        for row in rows:
            dp = _parse_device_props(row.get("provider_device_props"))
            items.append(
                SandboxDeviceInfo(
                    table_id=row["id"],
                    table_type=TableType.BAAS,
                    sandbox_id=dp.get("sandbox_id"),
                    ttl_expiration_time=dp.get("ttl_expiration_time"),
                    ttl_expiration_timestamp=dp.get("ttl_expiration_timestamp"),
                    refresh_fail_count=dp.get("refresh_fail_count", 0),
                    status=row.get("status", ""),
                )
            )
        return PaginatedResult(total=total, page=page, page_size=page_size, items=items)

    async def warn_device(self, *, table_id: int) -> WarnResult:
        row = self._binding_repo.get_baas_device_by_id(baas_device_id=table_id)
        if row is None:
            raise ValueError(f"Device record not found: id={table_id}")

        # 非 ACTIVE 状态不探活
        if row.get("status") != "ACTIVE":
            return WarnResult(
                table_id=table_id,
                table_type=TableType.BAAS,
                action="SKIP",
                refresh_fail_count=0,
            )

        dp = _parse_device_props(row.get("provider_device_props"))
        fail_count = dp.get("refresh_fail_count", 0)

        if fail_count >= WARNING_THRESHOLD:
            self._binding_repo.update_baas_device_status_by_id(
                baas_device_id=table_id,
                status=DeviceStatus.STOPPED.value,
                modifier="health-checker",
            )
            return WarnResult(
                table_id=table_id,
                table_type=TableType.BAAS,
                action="STOPPED",
                refresh_fail_count=fail_count,
            )

        # 探活
        sandbox_id = dp.get("sandbox_id")
        try:
            device_info = await self._paas_facade.get_device_info(sandbox_id)
            new_count = 0
            # 更新 TTL 字段
            if hasattr(device_info, "ttl_timestamp") and device_info.ttl_timestamp:
                ttl_time_str = datetime.fromtimestamp(
                    device_info.ttl_timestamp / 1000
                ).strftime("%Y-%m-%d %H:%M:%S")
                self._binding_repo.update_baas_device_ttl_by_id(
                    baas_device_id=table_id,
                    ttl_expiration_timestamp=device_info.ttl_timestamp,
                    ttl_expiration_time=ttl_time_str,
                    refresh_fail_count=0,
                )
            else:
                self._binding_repo.update_baas_device_refresh_fail_count_by_id(
                    baas_device_id=table_id, refresh_fail_count=new_count
                )
            action = "RESET"
        except Exception as e:
            logger.warning(f"[BaasSandboxHandler] Probe failed for {sandbox_id}: {e}")
            new_count = fail_count + 1
            self._binding_repo.update_baas_device_refresh_fail_count_by_id(
                baas_device_id=table_id, refresh_fail_count=new_count
            )
            action = "INCREMENT"

        return WarnResult(
            table_id=table_id,
            table_type=TableType.BAAS,
            action=action,
            refresh_fail_count=new_count,
        )

    async def renew_ttl(self, *, table_id: int) -> RenewTtlResult:
        row = self._binding_repo.get_baas_device_by_id(baas_device_id=table_id)
        if row is None:
            raise ValueError(f"Device record not found: id={table_id}")

        # baas_device 表的 provider_device_id 就是 sandbox_id
        sandbox_id = row.get("provider_device_id")
        if not sandbox_id:
            raise ValueError(f"No provider_device_id for device id={table_id}")

        dp = _parse_device_props(row.get("provider_device_props"))
        old_ttl = dp.get("ttl_expiration_time")

        try:
            ttl_info = await self._paas_facade.update_device_ttl(
                paas_device_id=sandbox_id
            )
            if ttl_info.success and ttl_info.new_expiration_time:
                ttl_timestamp = int(ttl_info.new_expiration_time.timestamp() * 1000)
                ttl_time_str = ttl_info.new_expiration_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                self._binding_repo.update_baas_device_ttl_by_id(
                    baas_device_id=table_id,
                    ttl_expiration_time=ttl_time_str,
                    ttl_expiration_timestamp=ttl_timestamp,
                    refresh_fail_count=0,
                )
                return RenewTtlResult(
                    table_id=table_id,
                    table_type=TableType.BAAS,
                    device_id=sandbox_id,
                    success=True,
                    old_expiration_time=old_ttl,
                    new_expiration_time=ttl_time_str,
                    refresh_fail_count=0,
                )
            else:
                return RenewTtlResult(
                    table_id=table_id,
                    table_type=TableType.BAAS,
                    device_id=sandbox_id,
                    success=False,
                    old_expiration_time=old_ttl,
                    refresh_fail_count=dp.get("refresh_fail_count", 0),
                    error=ttl_info.error or "TTL extension failed",
                )
        except Exception as e:
            logger.warning(
                f"[BaasSandboxHandler] Renew TTL failed for {sandbox_id}: {e}"
            )
            return RenewTtlResult(
                table_id=table_id,
                table_type=TableType.BAAS,
                device_id=sandbox_id,
                success=False,
                old_expiration_time=old_ttl,
                refresh_fail_count=dp.get("refresh_fail_count", 0),
                error=str(e),
            )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class SandboxDeviceRouter(SandboxDeviceRouterProtocol):
    """按 table_type 路由沙箱设备操作的统一入口。"""

    def __init__(
        self,
        handlers: dict[TableType, SandboxDeviceHandler],
    ):
        if TableType.AC_BINDING not in handlers:
            raise ValueError(f"Missing handler for {TableType.AC_BINDING}")
        if TableType.BAAS not in handlers:
            raise ValueError(f"Missing handler for {TableType.BAAS}")
        self._handlers = handlers
        logger.info("SandboxDeviceRouter initialized")

    def _get_handler(self, table_type: str | TableType) -> SandboxDeviceHandler:
        key = TableType(table_type)
        return self._handlers[key]

    def query_active_sandboxes(
        self,
        *,
        env: str = "prod",
        table_type: str,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResult:
        handler = self._get_handler(table_type)
        return handler.query_active_sandboxes(env=env, page=page, page_size=page_size)

    async def warn_device(self, *, table_id: int, table_type: str) -> WarnResult:
        handler = self._get_handler(table_type)
        return await handler.warn_device(table_id=table_id)

    async def renew_ttl(self, *, table_type: str, table_id: int) -> RenewTtlResult:
        handler = self._get_handler(table_type)
        return await handler.renew_ttl(table_id=table_id)
