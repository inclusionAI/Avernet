"""Arca device plugin Protocol — contract for Arca sandbox lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from secbaas.api.device_manage import (
    MountPoint,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    ResourceSpecification,
    Storage,
)

if TYPE_CHECKING:
    from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo


class ArcaSandbox(Protocol):
    """An Arca sandbox device with lifecycle operations.

    Mirrors the Arca SDK SyncSandbox API but as a Protocol,
    allowing Docker-based and mock implementations.
    """

    is_ready: bool
    sandbox_id: str

    def get_info(self) -> Any:
        """Get sandbox info including status, template_id, resources, ttl, etc.

        Returns:
            An info object with at minimum: sandbox_id, status, template_id.
            Optional fields depend on the implementation.
        """
        ...

    def destroy(self) -> Any:
        """Destroy the sandbox.

        Returns:
            True/False or an object with a .success attribute.
        """
        ...

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """Execute a command on the sandbox.

        Args:
            cmd: Command string to execute.
            timeout_in_millis: Maximum execution time in milliseconds.
            envs: Environment variables for the command context.

        Returns:
            An object with at minimum: exit_code, stdout, stderr, elapsed_time.
        """
        ...

    def update_outbound_rule(
        self,
        rule: OutBoundOperationRule,
        updated_mode: OutBoundOperationRuleUpdatedMode,
    ) -> Any:
        """Update outbound operation rules for the sandbox.

        Args:
            rule: The outbound operation rule to apply.
            updated_mode: The update mode (e.g., REPLACE).

        Returns:
            True/successful result or raises on failure.
        """
        ...

    def extend_ttl(self, ttl_minutes: int) -> Any:
        """Extend the sandbox TTL.

        Args:
            ttl_minutes: Additional TTL minutes to add.

        Returns:
            True/False or an object with a .success attribute.
        """
        ...


class ArcaSandboxPlugin(Protocol):
    """Factory protocol for creating and connecting to Arca sandbox devices.

    Implementations:
    - ArcaSdkSandboxPlugin: wraps the real Arca SDK (SandboxFactory + SyncSandbox)
    - StubArcaSandboxPlugin: in-memory mock for unit tests
    - DockerArcaSandboxPlugin: local Docker containers with Arca-compatible API

    Note:
        This is a SYNC factory (mirroring Arca SDK's sync API).
        Callers wrap calls in asyncio.to_thread() for async usage.
        The delete_storage method performs a platform-level HTTP call.
        Callers may invoke it synchronously from within an existing
        asyncio.to_thread() context (e.g. inside _destroy_device_sync),
        or wrap it in its own asyncio.to_thread() when called standalone.
    """

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
        """Create a new sandbox and wait for it to be ready.

        Args:
            template_id: Platform template ID.
            ttl_in_minutes: Time-to-live in minutes.
            envs: Environment variables to set in the sandbox.
            mount_points: Storage mount point configurations.
            resource_spec: CPU/memory resource specification.
            metadata: Arbitrary passthrough metadata.
            outbound_operation_rule: Network outbound rules.
            storage: NAS storage binding configuration.
            image: Docker image override (overrides template default).
            timeout_in_millis: Maximum creation wait time in milliseconds.
            ready_timeout_in_seconds: Maximum time to wait for sandbox ready state.

        Returns:
            An ArcaSandbox ready for use.

        Raises:
            RuntimeError: On creation failure or timeout.
        """
        ...

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        """Connect to an existing sandbox by ID.

        Args:
            sandbox_id: The sandbox ID to connect to.

        Returns:
            An ArcaSandbox for the existing sandbox.

        Raises:
            RuntimeError: If the sandbox is not found or cannot be connected.
        """
        ...

    def close(self) -> None:
        """Release any resources held by the plugin."""
        ...

    def delete_storage(self, storage_id: str, tenant_name: str) -> bool:
        """Delete NAS persistent storage associated with a sandbox.

        This is a platform-level operation (requires base_url from
        SandboxConfig), not a per-sandbox operation like create/destroy.

        Args:
            storage_id: The storage ID to delete from the platform.
            tenant_name: Tenant name for authorization header (X-Tenant-Id).

        Returns:
            True if deletion succeeded or storage was not found (idempotent).
            False on unexpected errors.
        """
        ...

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a sandbox device.

        Args:
            paas_device_id: Arca sandbox_id (raw platform device ID).
            port: Target port on the device.
            path: WebSocket path (e.g., /api/openclaw/ws).
            template_id: Optional template ID for multi-tenant proxypass routing.

        Returns:
            WsConnectionInfo with wss:// URL, JWT token, target, and expiry.
        """
        ...

    def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a device.

        Args:
            paas_device_id: Arca sandbox_id (raw platform device ID).
            port: Target port on the device.
            path: HTTP path (defaults to "/" when not provided).
            template_id: Optional template ID for multi-tenant proxypass routing.

        Returns:
            HttpConnectionInfo with http_url, token, and expires_at.
        """
        ...
