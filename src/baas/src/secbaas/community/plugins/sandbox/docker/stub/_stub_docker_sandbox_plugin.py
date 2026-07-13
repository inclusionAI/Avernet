"""Mock Docker sandbox plugin — in-memory implementation for testing.

Provides:
- StubDockerSandboxPlugin: factory that creates/connects mock sandboxes
- StubDockerSandbox: DockerSandbox protocol mock implementation
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.docker import DockerSandbox, DockerSandboxPlugin

logger = get_logger("plugin-sandbox-docker-stub")


class StubCommandResult:
    def __init__(self) -> None:
        self.exit_code = 0
        self.stdout = "mock-output"
        self.stderr = ""
        self.elapsed_time = 0.0


class StubDockerSandbox(DockerSandbox):
    """Mock implementation of DockerSandbox for testing."""

    def __init__(self, sandbox_id: str, host_port: int = 18080) -> None:
        self._sandbox_id = sandbox_id
        self._host_port = host_port
        self._destroyed = False
        self._status = "running"

    @property
    def is_ready(self) -> bool:
        return not self._destroyed

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    def get_info(self) -> Any:
        logger.info("[stub] get_info sandbox_id=%s", self._sandbox_id)
        status = "TERMINATING" if self._destroyed else "running"
        return {
            "sandbox_id": self._sandbox_id,
            "status": status,
            "container_id": self._sandbox_id,
            "host_port": self._host_port,
            "image": "stub-image:latest",
        }

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        logger.info(
            "[stub] exec_command sandbox_id=%s timeout=%d cmd=%s",
            self._sandbox_id,
            timeout_in_millis,
            cmd[:200],
        )
        return StubCommandResult()

    def destroy(self) -> Any:
        self._destroyed = True
        self._status = "exited"
        logger.info("[stub] sandbox destroyed sandbox_id=%s", self._sandbox_id)
        return True

    def restart(self) -> Any:
        if self._destroyed:
            raise RuntimeError("sandbox not found (404)")
        logger.info("[stub] sandbox restarted sandbox_id=%s", self._sandbox_id)
        return True


class StubDockerSandboxPlugin(DockerSandboxPlugin):
    """Mock Docker sandbox plugin for testing — no real docker-py SDK calls."""

    def __init__(self) -> None:
        self._sandboxes: dict[str, StubDockerSandbox] = {}
        logger.info("[stub] initialized")

    def _id(self) -> str:
        return uuid.uuid4().hex[:12]

    def create_device(
        self,
        template_id: int,
        template_uuid: str,
        tenant_name: str,
        container_name: str,
        image: str,
        container_port: int,
        envs: dict[str, str] | None = None,
        cpu_limit: str | None = None,
        memory_limit: str | None = None,
        image_pull_policy: str = "if_not_present",
        health_endpoint: str = "/health",
        health_timeout_seconds: int = 120,
    ) -> StubDockerSandbox:
        sandbox_id = self._id()
        sandbox = StubDockerSandbox(sandbox_id=sandbox_id, host_port=18080)
        self._sandboxes[sandbox_id] = sandbox
        logger.info(
            "[stub] sandbox created template_id=%s tenant_name=%s sandbox_id=%s",
            template_id,
            tenant_name,
            sandbox_id,
        )
        return sandbox

    def destroy_device(self, paas_device_id: str) -> bool:
        if paas_device_id in self._sandboxes:
            self._sandboxes[paas_device_id].destroy()
            del self._sandboxes[paas_device_id]
            logger.info("[stub] sandbox destroyed paas_device_id=%s", paas_device_id)
        else:
            logger.info(
                "[stub] sandbox already gone (idempotent) paas_device_id=%s",
                paas_device_id,
            )
        return True

    def connect_device(self, sandbox_id: str) -> StubDockerSandbox:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError("sandbox not found (404)")
        logger.info("[stub] sandbox connected sandbox_id=%s", sandbox_id)
        return sandbox

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> WsConnectionInfo:
        normalized_path = "/" + path.lstrip("/")
        logger.info(
            "[stub] resolve_ws_conn_info device_id=%s port=%d path=%s",
            paas_device_id,
            port,
            normalized_path,
        )
        return WsConnectionInfo(
            ws_url=f"ws://127.0.0.1:{port}{normalized_path}",
            token="",
            target=f"DOCKER_{paas_device_id}:{port}",
            expires_at=datetime.max,
        )

    def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> HttpConnectionInfo:
        normalized_path = "/" + path.lstrip("/")
        logger.info(
            "[stub] resolve_invoke_http_info device_id=%s port=%d path=%s",
            paas_device_id,
            port,
            normalized_path,
        )
        return HttpConnectionInfo(
            http_url=f"http://127.0.0.1:{port}{normalized_path}",
            token="",
            target=f"DOCKER_{paas_device_id}:{port}",
        )

    def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        query_string: str | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        logger.info(
            "[stub] invoke_http_in_device device_id=%s method=%s port=%d path=%s",
            paas_device_id,
            method,
            port,
            path,
        )
        return {
            "status_code": 200,
            "headers": {},
            "body": "",
        }

    def close(self) -> None:
        logger.info("[stub] plugin closed")
        self._sandboxes.clear()
        logger.info("[stub] sandboxes cleared")
