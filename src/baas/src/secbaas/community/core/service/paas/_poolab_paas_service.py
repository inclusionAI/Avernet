"""Poolab platform PaaS adapter.

Provides device lifecycle CRUD operations via the Poolab REST API
using a pluggable ``PoolabSandboxPlugin`` (injected at construction)
so that the core layer never imports ``aiohttp`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    OutBoundOperationRule,
    PoolabCreateConfig,
    PoolabCreationResult,
    PoolabCredentials,
    PoolabDeviceInfo,
    PoolabInstanceSummary,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.poolab import PoolabSandboxPlugin

from ._paas_service import PaasService

if TYPE_CHECKING:
    from secbaas.community.api.health_check.bot import TTLInfo


class PoolabPaasService(PaasService):
    """Poolab platform PaaS adapter.

    Delegates all HTTP operations to the injected ``PoolabSandboxPlugin``,
    keeping this class free of transport-framework imports.
    """

    def __init__(self, credentials: PoolabCredentials, plugin: PoolabSandboxPlugin):
        if credentials is None:
            raise ValueError("credentials is required")
        if plugin is None:
            raise ValueError("plugin is required")
        self._credentials = credentials
        self._plugin = plugin
        self._logger = get_logger("core-service")

    async def get_credentials(self) -> PoolabCredentials:
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        return TenantType.POOLAB

    async def create_device(  # type: ignore[override]
        self,
        config: PoolabCreateConfig,
    ) -> PoolabCreationResult:
        return await self._plugin.create_device(config)

    async def destroy_device(self, paas_device_id: str) -> bool:
        return await self._plugin.destroy_device(paas_device_id)

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        return await self._plugin.execute_command(
            paas_device_id, cmd, env=env, timeout_seconds=timeout_seconds
        )

    async def get_device_info(  # type: ignore[override]
        self, paas_device_id: str
    ) -> PoolabDeviceInfo:
        return await self._plugin.get_device_info(paas_device_id)

    async def list_instances(
        self, params: dict[str, Any]
    ) -> list[PoolabInstanceSummary]:
        return await self._plugin.list_instances(params)

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        raise NotImplementedError(
            "Poolab platform does not support outbound operation rules"
        )

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        return await self._plugin.resolve_ws_conn_info(
            paas_device_id,
            port,
            path,
            template_id=self._credentials.template_id,
        )

    async def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str | None = None,
    ) -> HttpConnectionInfo:
        return await self._plugin.resolve_http_connection_info(
            paas_device_id,
            port,
            path or "/",
            template_id=self._credentials.template_id,
        )

    async def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        return await self._plugin.invoke_http_in_device(
            paas_device_id,
            method,
            port,
            path,
            query_string,
            headers,
            body,
        )

    async def restart_device(self, paas_device_id: str) -> bool:
        return await self._plugin.restart_device(paas_device_id)

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        return await self.restart_device(paas_device_id)

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        raise NotImplementedError("Poolab API does not support TTL renewal")

    async def open_folder(
        self,
        paas_device_id: str,
        folder_path: str | None = None,
    ) -> bool:
        raise NotImplementedError("Poolab platform does not support open_folder")

    async def close(self) -> None:
        await self._plugin.close()
