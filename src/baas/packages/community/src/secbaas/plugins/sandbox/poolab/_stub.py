"""Stub Poolab sandbox plugin — in-memory implementation for testing.

Provides StubPoolabSandboxPlugin that mimics Poolab responses
without making any real HTTP calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.api.device_manage import (
    CommandResult,
    ErrorCode,
    PaasError,
    PoolabCreateConfig,
    PoolabCreationResult,
    PoolabCredentials,
    PoolabDeviceInfo,
    PoolabInstanceSummary,
)
from secbaas.logger import get_logger
from secbaas.spi.sandbox.poolab import PoolabSandboxPlugin

if TYPE_CHECKING:
    pass

logger = get_logger("plugin-sandbox-poolab")


class StubPoolabSandboxPlugin(PoolabSandboxPlugin):
    """Mock Poolab sandbox plugin for testing — no real HTTP calls."""

    def __init__(self, credentials: PoolabCredentials | None = None) -> None:
        self._devices: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:
        self._devices.clear()

    async def create_device(self, config: PoolabCreateConfig) -> PoolabCreationResult:
        poolab_id = f"stub-poolab-{id(config)}"
        self._devices[poolab_id] = {"status": "RUNNING"}
        return PoolabCreationResult(
            platform="poolab",
            status="RUNNING",
            poolab_id=poolab_id,
            poolab_user_id=config.poolab_user_id,
        )

    async def destroy_device(self, paas_device_id: str) -> bool:
        self._devices.pop(paas_device_id, None)
        return True

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        return CommandResult(
            exit_code=0,
            stdout="stub-output",
            stderr="",
            execution_time_ms=0,
            command=cmd,
            env=env,
        )

    async def get_device_info(self, paas_device_id: str) -> PoolabDeviceInfo:
        if paas_device_id not in self._devices:
            raise PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                f"Poolab device {paas_device_id} not found",
            )
        return PoolabDeviceInfo(
            platform="poolab",
            status="RUNNING",
            poolab_id=paas_device_id,
        )

    async def list_instances(
        self, params: dict[str, Any]
    ) -> list[PoolabInstanceSummary]:
        return [
            PoolabInstanceSummary(poolab_id=k, status=v.get("status", "UNKNOWN"))
            for k, v in self._devices.items()
        ]

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
        return {
            "status_code": 200,
            "headers": {},
            "body": "",
        }

    async def restart_device(self, paas_device_id: str) -> bool:
        return True

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        target = (
            f"POOLAB_{paas_device_id}@{template_id}:{port}"
            if template_id is not None
            else f"POOLAB_{paas_device_id}:{port}"
        )
        return WsConnectionInfo(
            ws_url=f"ws://localhost:{port}{path}",
            token="stub-token",
            target=target,
            expires_at=datetime.max,
        )

    async def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        target = (
            f"POOLAB_{paas_device_id}@{template_id}:{port}"
            if template_id is not None
            else f"POOLAB_{paas_device_id}:{port}"
        )
        return HttpConnectionInfo(
            http_url=f"http://localhost:{port}{path}",
            token="stub-token",
            target=target,
        )
