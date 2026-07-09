"""PaaS domain protocols."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from secbaas.api.bot_runtime import WsConnectionInfo
from secbaas.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    DeviceCreationResult,
    PaasCredentials,
)
from secbaas.api.tenant_manage import TenantType

if TYPE_CHECKING:
    from secbaas.api.bot_manage import FetchStartProgressResult
    from secbaas.api.bot_runtime import HttpConnectionInfo
    from secbaas.api.device_manage import (
        DeviceInfo,
        LocalPaasService,
        OutBoundOperationRule,
    )
    from secbaas.api.health_check.bot import TTLInfo


# ── PaasService Protocol ──────────────────────────────────────────


@runtime_checkable
class PaasService(Protocol):
    """Protocol for PaaS platform adapters.

    Defines the unified interface for Device lifecycle operations across
    different PaaS platforms (Arca, Sigma, etc.).

    Per Decision D-05: Service is stateless; credentials passed as parameters.
    Platform selection is caller's responsibility (Decision D-04).
    """

    async def get_credentials(self) -> PaasCredentials:
        """Get the credentials used by this service instance."""
        ...

    async def get_platform_type(self) -> TenantType:
        """Return the platform type for this service instance (ARCA, SIGMA, LOCAL)."""
        ...

    async def resolve_ws_conn_info(
        self, paas_device_id: str, port: int, path: str
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a device."""
        ...

    async def create_device(self, config: DeviceCreateConfig) -> DeviceCreationResult:
        """Create a device/container on the platform."""
        ...

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a device/container on the platform."""
        ...

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on the device."""
        ...

    async def get_device_info(self, paas_device_id: str) -> DeviceInfo:
        """Get device info by platform-specific device ID."""
        ...

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """Update outbound operation rule for a device."""
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
        """Invoke HTTP request on a device."""
        ...

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Extend device TTL — platform decides extension strategy."""
        ...

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a device/container on the platform."""
        ...

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Update device/container configuration on the platform."""
        ...

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Open a folder in the device's file explorer."""
        ...

    async def fetch_start_progress(
        self, paas_device_id: str
    ) -> FetchStartProgressResult:
        """Fetch device start/initialization progress from the platform."""
        ...

    async def list_instances(self, params: dict[str, Any]) -> list[Any]:
        """List platform instances with flexible query parameters."""
        ...

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a device."""
        ...


# ── Factory / ConnectionManager Protocols ─────────────────────────


@runtime_checkable
class PaasServiceFactory(Protocol):
    """Protocol for the PaaS service factory.

    Used by adapter routers to create platform-specific PaasService
    instances without importing the concrete factory from core.service.
    """

    def create(
        self,
        tenant_name: str,
        template_uuid: str | None = None,
        template: object | None = None,
    ) -> PaasService:
        """Create a PaasService for the given tenant and template."""
        ...

    def create_local_paas_service(
        self,
        user_id: str,
        machine_id: str,
        env: str | None = None,
    ) -> LocalPaasService:
        """Create a LocalPaasService with direct credentials."""
        ...


@runtime_checkable
class ConnectionManager(Protocol):
    """SPI protocol for desktop device WebSocket management.

    Abstracts the ConnectionManager interface so consumers can inject
    mock or alternative implementations for testing.

    Lifecycle order: ensure_initialized() → ... use ... → shutdown()
    Or explicit: initialize() → start() → ... use ... → shutdown()
    """

    async def send_command(self, machine_id: str, command: dict) -> dict:
        """Send command to a connected mng daemon and wait for the result.

        Args:
            machine_id: Target machine identifier.
            command: Command payload dict to send.

        Returns:
            Result dict from mng daemon response.

        Raises:
            ConnectionError: If the machine is not connected.
            TimeoutError: If the command times out.
        """
        ...

    async def send_command_with_request_id(
        self, machine_id: str, command: dict, request_id: str
    ) -> dict:
        """Send command with a pre-generated request_id for end-to-end tracing.

        Args:
            machine_id: Target machine identifier.
            command: Command payload dict to send.
            request_id: Pre-generated request ID for correlation.

        Returns:
            Result dict from mng daemon response.

        Raises:
            ConnectionError: If the machine is not connected.
            TimeoutError: If the command times out.
        """
        ...

    def is_connected(self, machine_id: str) -> bool:
        """Check whether a machine has an active WebSocket connection.

        Args:
            machine_id: Target machine identifier.

        Returns:
            True if the machine has an active connection in this process.
        """
        ...

    def initialize(
        self,
        env: str,
        instance_id: str | None = None,
        uds_config: object | None = None,
    ) -> None:
        """Set runtime dependencies before start().

        Args:
            env: Environment identifier (dev, pre, prod).
            instance_id: Optional instance ID, auto-detected if None.
            uds_config: Optional UDS config for socket_path derivation.
        """
        ...

    def ensure_initialized(self) -> None:
        """Idempotent lifecycle bootstrap: initialize + start.

        Reads environment from the runtime automatically.
        Safe to call multiple times; sweep task only starts once.

        Raises:
            RuntimeError: If initialize() or start() fails.
        """
        ...

    @property
    def is_initialized(self) -> bool:
        """Check whether initialize() has been called."""
        ...

    def start(self) -> None:
        """Start background sweep tasks (heartbeat detection, etc.)."""
        ...

    async def shutdown(self) -> None:
        """Graceful shutdown — cancel sweep tasks and close connections."""
        ...

    async def send_callback_result(self, request_id: str, result: object) -> bool:
        """Send a callback result for a pending request."""
        ...

    @property
    def MAX_CONNECTIONS(self) -> int:  # noqa: N802
        """Maximum allowed concurrent connections."""
        ...

    def is_at_capacity(self) -> bool:
        """Check whether connection pool is at capacity."""
        ...
