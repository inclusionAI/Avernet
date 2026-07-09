"""Poolab sandbox Plugin Protocol — contract for Poolab REST API operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
    from secbaas.api.device_manage import (
        CommandResult,
        PoolabCreateConfig,
        PoolabCreationResult,
        PoolabDeviceInfo,
        PoolabInstanceSummary,
    )


@runtime_checkable
class PoolabSandboxPlugin(Protocol):
    """Plugin protocol for Poolab platform HTTP operations.

    Abstracts Poolab's REST API behind a pluggable interface so the
    ``PoolabPaasService`` (in core) never imports ``aiohttp`` directly.
    """

    async def create_device(self, config: PoolabCreateConfig) -> PoolabCreationResult:
        """Create a Poolab machine via the Poolab API.

        Args:
            config: PoolabCreateConfig with user_id, tenant_id, image_id, envs.

        Returns:
            PoolabCreationResult with full machine details.

        Raises:
            PaasError: On HTTP error or Poolab application-level failure.
        """
        ...

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a Poolab machine.

        Args:
            paas_device_id: Raw poolab_id.

        Returns:
            True on successful deletion.

        Raises:
            PaasError: On HTTP error or Poolab application-level failure.
        """
        ...

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on a Poolab machine.

        Args:
            paas_device_id: Poolab machine ID.
            cmd: Command string to execute.
            env: Optional environment variables.
            timeout_seconds: Max execution time in seconds.

        Returns:
            CommandResult with exit_code, stdout, stderr.

        Raises:
            NotImplementedError: If cmd is not an execMachine-eligible command.
            PaasError: On HTTP error or Poolab application-level failure.
        """
        ...

    async def get_device_info(self, paas_device_id: str) -> PoolabDeviceInfo:
        """Get Poolab machine info.

        Args:
            paas_device_id: Raw poolab_id.

        Returns:
            PoolabDeviceInfo with current machine status and details.

        Raises:
            PaasError: On HTTP error or device not found.
        """
        ...

    async def list_instances(
        self, params: dict[str, Any]
    ) -> list[PoolabInstanceSummary]:
        """List Poolab instances.

        Args:
            params: Query parameters as a dict.

        Returns:
            List of PoolabInstanceSummary objects.

        Raises:
            PaasError: On HTTP error or Poolab application-level failure.
        """
        ...

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
        """Forward an HTTP request to a Poolab container.

        Args:
            paas_device_id: Poolab machine ID.
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the device.
            path: Request path.
            query_string: Query string including leading '?' or None/empty.
            headers: HTTP headers dict forwarded to the container.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).

        Raises:
            PaasError: On HTTP error or Poolab application-level failure.
        """
        ...

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a Poolab machine.

        Args:
            paas_device_id: Raw poolab_id.

        Returns:
            True if the restart command was sent successfully.

        Raises:
            PaasError: On HTTP error or Poolab application-level failure.
        """
        ...

    async def close(self) -> None:
        """Release underlying HTTP resources."""
        ...

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a Poolab device.

        Args:
            paas_device_id: Poolab machine ID.
            port: Target port on the device.
            path: WebSocket path (e.g., /api/openclaw/ws).
            template_id: Optional template ID for multi-tenant target format.

        Returns:
            WsConnectionInfo with ws_url, token, target, expires_at.
        """
        ...

    async def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a Poolab device.

        Args:
            paas_device_id: Poolab machine ID.
            port: Target port on the device.
            path: Request path (defaults to "/").
            template_id: Optional template ID for multi-tenant target format.

        Returns:
            HttpConnectionInfo with http_url, token, and target.
        """
        ...
