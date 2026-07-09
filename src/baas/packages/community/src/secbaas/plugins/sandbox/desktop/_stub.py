"""Mock Desktop device plugin — in-memory implementation for testing.

Provides:
- StubDesktopSandboxPlugin: factory that creates/connects mock desktop containers
- StubDesktopSandbox: DesktopSandbox protocol mock implementation
"""

from __future__ import annotations

import uuid
from typing import Any

from secbaas.spi.sandbox.desktop import DesktopSandbox, DesktopSandboxPlugin


class StubDesktopSandbox(DesktopSandbox):
    """Mock implementation of DesktopSandbox for testing."""

    def __init__(
        self,
        container_id: str,
        machine_id: str = "",
        user_id: str = "",
    ) -> None:
        self._container_id = container_id
        self._machine_id = machine_id
        self._user_id = user_id

    @property
    def container_id(self) -> str:
        return self._container_id

    @property
    def machine_id(self) -> str:
        return self._machine_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def is_running(self) -> bool:
        return True

    def get_info(self) -> Any:
        return {
            "container_id": self._container_id,
            "machine_id": self._machine_id,
            "user_id": self._user_id,
            "status": "RUNNING",
            "platform": "desktop",
            "port": 8080,
        }

    def exec_shell(
        self,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> Any:
        return StubCommandResult()

    def http_proxy(
        self,
        method: str,
        port: int,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query_string: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status_code": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": "bW9jayBodHRwIHJlc3BvbnNl",  # base64 "mock http response"
        }

    def destroy(self) -> bool:
        return True

    def restart(self) -> bool:
        return True


class StubCommandResult:
    exit_code = 0
    stdout = "mock-output"
    stderr = ""
    execution_time_ms = 0.0


class StubDesktopSandboxPlugin(DesktopSandboxPlugin):
    """Mock Desktop device plugin for testing — no ConnectionManager dependency."""

    def __init__(self) -> None:
        self._devices: dict[str, StubDesktopSandbox] = {}

    def create_device(
        self,
        machine_id: str,
        bot_uuid: str,
        agent_code: str,
        user_id: str,
        envs: dict[str, str] | None = None,
        mount_path: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> DesktopSandbox:
        container_id = f"mock-{uuid.uuid4().hex[:12]}"
        device = StubDesktopSandbox(
            container_id=container_id,
            machine_id=machine_id,
            user_id=user_id,
        )
        self._devices[container_id] = device
        return device

    def connect_device(
        self,
        container_id: str,
        machine_id: str,
        user_id: str,
    ) -> DesktopSandbox:
        if container_id not in self._devices:
            device = StubDesktopSandbox(
                container_id=container_id,
                machine_id=machine_id,
                user_id=user_id,
            )
            self._devices[container_id] = device
        return self._devices[container_id]

    def get_machine_info(self, machine_id: str) -> dict[str, Any]:
        return {
            "machine_id": machine_id,
            "cpu_cores": 4,
            "memory_gb": 16,
            "disk_gb": 256,
        }

    def get_machine_res_dirs(
        self, machine_id: str, dir: str = "~/Desktop"
    ) -> dict[str, Any]:
        return {
            "name": "Desktop",
            "children": [
                {"name": "agent-code", "children": []},
                {"name": "projects", "children": []},
            ],
        }

    def resolve_ws_conn_info(
        self,
        session_id: str,
        container_id: str,
        machine_id: str,
        user_id: str,
        port: int,
        path: str,
        template_id: int,
    ) -> Any:
        """Construct mock relay WebSocket connection info for testing.

        Returns a WsConnectionInfo object (same type as the Real Plugin) so
        the Service layer can access .token / .ws_url / .target / .expires_at
        as attributes regardless of which Plugin implementation DI injects.

        Args:
            session_id: Relay session identifier (managed by Service layer).
            container_id: Container identifier from three-part LocalDeviceId.
            machine_id: Machine identifier from three-part LocalDeviceId.
            user_id: User identifier from three-part LocalDeviceId.
            port: Target port for WebSocket connection.
            path: WebSocket path (retained for interface compatibility; not used).
            template_id: Template ID used in the @{template_id} target suffix.

        Returns:
            WsConnectionInfo with ws_url, token, target, and expires_at.
        """
        from datetime import UTC, datetime

        from secbaas.api.bot_runtime import WsConnectionInfo
        from secbaas.config import ConfigPath, get_config, get_config_by_path

        device_id = f"{container_id}--{machine_id}--{user_id}"
        host = get_config_by_path(get_config(), ConfigPath.AGENTCLAW_PROXY_HOST_DEV)
        return WsConnectionInfo(
            ws_url=f"wss://{host}/wsrelay/{session_id}",
            token="mock-jwt-token",
            target=f"LOCAL_{device_id}@{template_id}:{port}:{session_id}",
            expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC),
        )

    def close(self) -> None:
        pass
