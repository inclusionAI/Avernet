"""
Device Management Service Protocols.

Defines the SPI interfaces for device lifecycle management operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ._device import (
    DestroyDeviceResponse,
    DeviceCreate,
    DeviceResponse,
)

if TYPE_CHECKING:
    from secbaas.community.api.bot_manage import FetchStartProgressResult
    from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
    from secbaas.community.api.health_check.bot import TTLInfo
    from secbaas.community.api.tenant_manage import TenantType

    from ._command_result import CommandResult
    from ._credentials import PaasCredentials
    from ._device import DeviceInfo
    from ._device_config import DeviceCreateConfig
    from ._device_creation_result import DeviceCreationResult
    from ._device_facade_config import (
        ArcaDeviceConfig,
        DockerDeviceConfig,
        K8sDeviceConfig,
        LocalDeviceConfig,
        PoolabDeviceConfig,
        SigmaDeviceConfig,
        TeClawDeviceConfig,
    )
    from ._outbound_rule import OutBoundOperationRule


@runtime_checkable
class DeviceService(Protocol):
    """Protocol for device lifecycle management service."""

    def create_device(
        self,
        tenant: str,
        data: DeviceCreate,
    ) -> DeviceResponse:
        """Create device: inserts PENDING device record with generated UUID."""
        ...

    async def start_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        """Start device: provision PaaS container and execute initialization hooks."""
        ...

    async def restart_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        """Restart device: destroy and recreate PaaS container."""
        ...

    async def update_device(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str = "system",
        publish_id: int | None = None,
    ) -> DeviceResponse:
        """Update device configuration and apply changes.

        Semantically distinct from restart_device: this applies configuration
        changes (envs, mount_path, resource_spec, etc.) to the device container.
        """
        ...

    async def destroy_device_by_uuid(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str,
        for_restart: bool = False,
    ) -> DestroyDeviceResponse:
        """Destroy device by UUID: terminates PaaS container, soft-deletes DB record."""
        ...

    async def stop_device_by_uuid(
        self,
        tenant: str,
        device_uuid: str,
        modifier: str,
    ) -> DestroyDeviceResponse:
        """Stop device by UUID: terminates PaaS container, sets STOPPED status, preserves record."""
        ...

    def get_device_info(
        self,
        device_uuid: str,
    ) -> DeviceResponse | None:
        """Get device information by UUID."""
        ...


@runtime_checkable
class LocalPaasService(Protocol):
    """Protocol for local PaaS device lifecycle service.

    Covers the public API surface of LocalPaasService used by routers:
    local_paas_router and internal_router.
    """

    async def get_credentials(self) -> PaasCredentials:
        """Get the credentials used by this service instance."""
        ...

    async def get_platform_type(self) -> TenantType:
        """Return the platform type for this service instance."""
        ...

    async def get_machine_info(self, machine_id: str) -> dict[str, Any]:
        """Get machine resource info from mng daemon."""
        ...

    async def get_machine_res_dirs(self, machine_id: str, dir: str) -> dict[str, Any]:
        """Get machine resource directory tree from mng daemon."""
        ...

    async def list_machines_by_user(self, user_id: str) -> list[Any]:
        """List all machines for a given user."""
        ...

    async def dispatch_to_local_connection(
        self,
        machine_id: str,
        command: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch command to local connection with cross-process fallback."""
        ...

    async def create_device(
        self,
        config: DeviceCreateConfig,
    ) -> DeviceCreationResult:
        """Create a device on the platform."""
        ...

    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a device by its platform-specific device ID."""
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

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """Update outbound operation rule for a device."""
        ...

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Extend device TTL."""
        ...

    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a device."""
        ...

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Update device configuration."""
        ...

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Open a folder in the device's file explorer."""
        ...

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a device."""
        ...

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a device."""
        ...

    async def fetch_start_progress(
        self, paas_device_id: str
    ) -> FetchStartProgressResult:
        """Fetch device start/initialization progress from mng daemon."""
        ...


@runtime_checkable
class PaasServiceFacade(Protocol):
    """Protocol for the PaaS Service Facade.

    Provides a unified interface for callers to manage device lifecycle
    without handling Factory and Service coordination details directly.
    """

    async def create_device(
        self,
        tenant_name: str,
        device_template_uuid: str | None = None,
        detail_config: (
            ArcaDeviceConfig
            | SigmaDeviceConfig
            | LocalDeviceConfig
            | PoolabDeviceConfig
            | TeClawDeviceConfig
            | K8sDeviceConfig
            | DockerDeviceConfig
            | None
        ) = None,
    ) -> DeviceCreationResult:
        """Create a device on the PaaS platform."""
        ...

    async def destroy_device(
        self,
        paas_device_id: str,
    ) -> bool:
        """Destroy a device by its paas_device_id."""
        ...

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on a device."""
        ...

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a device."""
        ...

    async def get_device_info(
        self,
        paas_device_id: str,
    ) -> DeviceInfo:
        """Get device info by its paas_device_id."""
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

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Open a folder in the device's file explorer."""
        ...

    async def fetch_start_progress(
        self, paas_device_id: str
    ) -> FetchStartProgressResult:
        """Fetch device start/initialization progress from the platform.

        Routes the request to the correct platform PaasService,
        stripping the @template_id suffix from paas_device_id.
        """
        ...

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Extend device TTL."""
        ...

    async def restart_device(
        self,
        paas_device_id: str,
    ) -> bool:
        """Restart a device by its paas_device_id."""
        ...

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Update a device by its paas_device_id."""
        ...

    async def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str | None = None,
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a device."""
        ...
