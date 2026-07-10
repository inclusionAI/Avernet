"""Mock Arca sandbox plugin — in-memory implementation for testing.

Provides:
- StubArcaSandboxPlugin: factory that creates/connects mock sandboxes
- StubArcaSandbox: ArcaSandbox protocol mock implementation
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.api.device_manage import (
    MountPoint,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    ResourceSpecification,
    Storage,
)
from secbaas.logger import get_logger
from secbaas.spi.sandbox.arca import ArcaSandbox, ArcaSandboxPlugin

if TYPE_CHECKING:
    from secbaas.api.device_manage import ArcaCredentials

logger = get_logger("plugin-sandbox-arca")


class StubSandboxInfo:
    """Mimics the Arca SDK SandboxInfo object returned by SyncSandbox.get_info()."""

    def __init__(self, sandbox_id: str, template_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.status = "RUNNING"
        self.template_id = template_id
        self.resources = None
        self.ttl_in_minutes = None
        self.ttl_timestamp = None
        self.envs = None
        self.snapshot_id = None
        self.metadata = None
        self.outbound_operation_rule = None


class StubArcaSandbox(ArcaSandbox):
    """Mock implementation of ArcaSandbox for testing."""

    def __init__(self, sandbox_id: str, template_id: str = "mock-template") -> None:
        self._sandbox_id = sandbox_id
        self._template_id = template_id

    @property
    def is_ready(self) -> bool:
        return True

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    def get_info(self) -> Any:
        return StubSandboxInfo(self._sandbox_id, self._template_id)

    def destroy(self) -> Any:
        logger.info("[stub] sandbox destroyed sandbox_id=%s", self._sandbox_id)
        return True

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

    def update_outbound_rule(
        self,
        rule: OutBoundOperationRule,
        updated_mode: OutBoundOperationRuleUpdatedMode,
    ) -> Any:
        logger.info("[stub] update_outbound_rule sandbox_id=%s", self._sandbox_id)
        return True

    def extend_ttl(self, ttl_minutes: int) -> Any:
        logger.info(
            "[stub] extend_ttl sandbox_id=%s ttl_minutes=%d",
            self._sandbox_id,
            ttl_minutes,
        )
        return True


class StubCommandResult:
    exit_code = 0
    stdout = "mock-output"
    stderr = ""
    elapsed_time = 0.0


class StubArcaSandboxPlugin(ArcaSandboxPlugin):
    """Mock Arca sandbox plugin for testing — no real SDK calls."""

    def __init__(
        self,
        credentials: ArcaCredentials | None = None,
    ) -> None:
        self._sandboxes: dict[str, StubArcaSandbox] = {}
        logger.info("[stub] initialized")

    def create_sync_sandbox(
        self,
        template_id: str,
        ttl_in_minutes: int | None = None,
        envs: dict[str, str] | None = None,
        mount_points: list[MountPoint] | None = None,
        resource_spec: ResourceSpecification | None = None,
        metadata: dict[str, str] | None = None,
        outbound_operation_rule: OutBoundOperationRule | None = None,
        storage: Storage | None = None,
        image: str | None = None,
        timeout_in_millis: int = 60000,
        ready_timeout_in_seconds: int = 60,
    ) -> ArcaSandbox:
        sandbox_id = f"stub-arca-{uuid.uuid4().hex[:12]}"
        device = StubArcaSandbox(sandbox_id, template_id)
        self._sandboxes[sandbox_id] = device
        logger.info(
            "[stub] sandbox created template_id=%s sandbox_id=%s",
            template_id,
            sandbox_id,
        )
        return device

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        if sandbox_id not in self._sandboxes:
            logger.info(
                "[stub] sandbox not in memory, creating on-the-fly sandbox_id=%s",
                sandbox_id,
            )
            self._sandboxes[sandbox_id] = StubArcaSandbox(sandbox_id)
        logger.info("[stub] sandbox connected sandbox_id=%s", sandbox_id)
        return self._sandboxes[sandbox_id]

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info — mock implementation.

        Returns a deterministic WsConnectionInfo for testing.

        Args:
            paas_device_id: Arca sandbox_id (ignored in stub).
            port: Target port on the device.
            path: WebSocket path.
            template_id: Optional template ID for multi-tenant target format.

        Returns:
            WsConnectionInfo with a ws://localhost URL, empty token, and 120s expiry.
        """
        return WsConnectionInfo(
            ws_url=f"ws://localhost:{port}{path}",
            token="",
            target=f"ARCA_{paas_device_id}@{template_id}:{port}"
            if template_id is not None
            else f"ARCA_{paas_device_id}:{port}",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

    def close(self) -> None:
        pass

    def delete_storage(self, storage_id: str, tenant_name: str) -> bool:
        logger.info(
            "[stub] delete_storage storage_id=%s tenant_name=%s",
            storage_id,
            tenant_name,
        )
        return True

    def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info — mock implementation.

        Returns a deterministic HttpConnectionInfo for testing.

        Args:
            paas_device_id: Arca sandbox_id (ignored in stub).
            port: Target port on the device.
            path: HTTP path (defaults to "/").
            template_id: Optional template ID for multi-tenant target format.

        Returns:
            HttpConnectionInfo with a http://localhost URL and empty token.
        """
        return HttpConnectionInfo(
            http_url=f"http://localhost:{port}{path}",
            token="",
            target=f"ARCA_{paas_device_id}@{template_id}:{port}"
            if template_id is not None
            else f"ARCA_{paas_device_id}:{port}",
        )
