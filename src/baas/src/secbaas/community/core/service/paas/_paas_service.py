"""PaaS service abstract base class.

Defines unified interface for Device lifecycle operations across
different PaaS platforms (Arca, Sigma, etc.).

Per Decision D-01: Three core operations defined:
- create_device(config: DeviceCreateConfig) -> DeviceCreationResult
- destroy_device(paas_device_id) -> bool
- execute_command(paas_device_id, cmd, env) -> CommandResult
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_manage import FetchStartProgressResult
from secbaas.community.api.bot_runtime import WsConnectionInfo
from secbaas.community.api.device_manage import (
    CommandResult,
    DeviceCreateConfig,
    DeviceCreationResult,
    PaasCredentials,
)
from secbaas.community.api.paas import PaasService as PaasServiceProtocol
from secbaas.community.api.tenant_manage import TenantType

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import HttpConnectionInfo
    from secbaas.community.api.device_manage import (
        DeviceInfo,
        OutBoundOperationRule,
        OutBoundOperationRuleUpdatedMode,
    )
    from secbaas.community.api.health_check.bot import TTLInfo


class PaasService(PaasServiceProtocol, ABC):
    """Abstract base class for PaaS platform adapters.

        Defines unified interface for Device lifecycle operations across
    different PaaS platforms (Arca, Sigma, etc.).

        Per Decision D-05: Service is stateless; credentials passed as parameters.
        Platform selection is caller's responsibility (Decision D-04).
    """

    @abstractmethod
    async def get_credentials(self) -> PaasCredentials:
        """Get the credentials used by this service instance.

        Returns:
            PaasCredentials instance containing template_id and platform credentials.
            For Arca: returns ArcaCredentials
            For Sigma: returns SigmaCredentials
        """
        ...

    @abstractmethod
    async def get_platform_type(self) -> TenantType:
        """Return the platform type for this service instance.

        Returns:
            TenantType enum value (ARCA, SIGMA, or LOCAL).
        """
        ...

    @abstractmethod
    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a device.

        Args:
            paas_device_id: Raw platform device ID without @template_id suffix.
            port: Target port on the device.
            path: WebSocket path (e.g., /api/openclaw/ws).
            ws_conn_mode: Optional connection mode override (e.g. ``"relay"``).
                ``None`` means no override. Only consumed by LocalPaasService.

        Returns:
            WsConnectionInfo containing ws_url, token, target, and expires_at.

        Raises:
            NotImplementedError: If the platform does not support WebSocket connections.
            PaasError: If device not found or connection info cannot be resolved.
        """
        ...

    @abstractmethod
    async def create_device(
        self,
        config: DeviceCreateConfig,
    ) -> DeviceCreationResult:
        """Create a device/container on the platform.

        Args:
            config: Platform-specific configuration (DeviceCreateConfig or subclass).
                For Arca: use ArcaCreateConfig.
                For Sigma: use SigmaCreateConfig.

        Returns:
            DeviceCreationResult with device details.
            For Arca: returns ArcaCreationResult with full sandbox info.
            For Sigma: returns SigmaCreationResult (when implemented).

        Raises:
            PaasError: With DEVICE_CREATION_FAILED on creation failure.
                       With DEVICE_NOT_READY if device doesn't become ready within timeout.
                       With AUTH_FAILED if credentials invalid.
                       With DEVICE_ALREADY_EXISTS if device exists.
        """
        ...

    @abstractmethod
    async def destroy_device(self, paas_device_id: str) -> bool:
        """Destroy a device/container on the platform.

        Args:
            paas_device_id: Platform-specific device ID (e.g., Arca sandbox_id).

        Returns:
            True if successful, False otherwise.

        Raises:
            PaasError: With DEVICE_DESTROY_FAILED code on failure.
                       With DEVICE_NOT_FOUND if device doesn't exist.
        """
        ...

    @abstractmethod
    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """Execute a command on the device.

        Args:
            paas_device_id: PaaS platform device ID (platform-specific format).
            cmd: Command string to execute.
            env: Command execution context (environment variables), optional.
            timeout_seconds: Maximum execution time in seconds (default: 30).

        Returns:
            CommandResult with execution details (includes env echo back).

        Raises:
            PaasError: With COMMAND_FAILED on execution failure.
                       With COMMAND_TIMEOUT if execution times out.
                       With DEVICE_UNAVAILABLE if device not reachable.
        """
        ...

    @abstractmethod
    async def get_device_info(self, paas_device_id: str) -> DeviceInfo:
        """Get device info by platform-specific device ID.

        Args:
            paas_device_id: Platform-specific device ID (e.g., Arca sandbox_id).

        Returns:
            DeviceInfo with platform-specific details.

        Raises:
            PaasError: With DEVICE_NOT_FOUND if device doesn't exist.
        """
        ...

    @abstractmethod
    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
        mode: OutBoundOperationRuleUpdatedMode | None = None,
    ) -> bool:
        """Update outbound operation rule for a device.

        Args:
            paas_device_id: Platform-specific device ID.
            outbound_operation_rule: New outbound operation rule to apply.

        Returns:
            True if successful.

        Raises:
            PaasError: With DEVICE_NOT_FOUND if device doesn't exist.
                       With DEVICE_UNAVAILABLE if update fails.
        """
        ...

    @abstractmethod
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
        """Invoke HTTP request on a device.

        Args:
            paas_device_id: Platform-specific device ID.
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the device.
            path: Request path (e.g., /api/v1/users).
            query_string: Query string including leading '?' or None/empty.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).

        Raises:
            NotImplementedError: If platform does not support HTTP invocation.
            PaasError: On invocation failure.
        """
        ...

    async def update_device_ttl(self, paas_device_id: str) -> TTLInfo:
        """Extend device TTL - platform decides extension strategy.

        Args:
            paas_device_id: Platform-specific device ID with @template_id suffix.

        Returns:
            TTLInfo with old and new expiration times.

        Raises:
            NotImplementedError: If platform doesn't support TTL extension.
            PaasError: With DEVICE_NOT_FOUND if device doesn't exist.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support TTL extension"
        )

    @abstractmethod
    async def restart_device(self, paas_device_id: str) -> bool:
        """Restart a device/container on the platform.

        Args:
            paas_device_id: Platform-specific device ID.

        Returns:
            True if restart was initiated successfully.

        Raises:
            NotImplementedError: If platform does not support restart operation.
            PaasError: With DEVICE_NOT_FOUND if device doesn't exist.
                       With DEVICE_UNAVAILABLE if restart fails.
        """
        ...

    @abstractmethod
    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Update device/container configuration on the platform.

        Semantically distinct from restart_device: update applies config
        changes (envs, mount_path, resource_spec, etc.), while restart
        only restarts the container process without changing config.

        Args:
            paas_device_id: Platform-specific device ID.
            config: Platform-specific device create configuration for the update.
                Contains the new configuration to apply to the device.
                If None, the platform implementation may use its default
                behavior (e.g., restart without config changes).
                Defaults to None for backward compatibility with platforms
                that do not yet accept config in update_device.

        Returns:
            True if update was initiated successfully.

        Raises:
            NotImplementedError: If platform does not support update operation.
            PaasError: With DEVICE_NOT_FOUND if device doesn't exist.
                       With DEVICE_UNAVAILABLE if update fails.
        """
        ...

    async def open_folder(
        self, paas_device_id: str, folder_path: str | None = None
    ) -> bool:
        """Open a folder in the device's file explorer.

        Only supported on LOCAL platform. Opens the specified folder path
        in the container's default file explorer application. All other
        platforms automatically raise NotImplementedError via this base class
        default implementation.

        Args:
            paas_device_id: Platform-specific device ID.
            folder_path: Optional folder path to open.
                If None, the platform's default path is used.

        Returns:
            True if the open-folder command was sent successfully.

        Raises:
            NotImplementedError: If the platform does not support open_folder
                (all non-LOCAL platforms by default).
        """
        raise NotImplementedError(
            f"open_folder is not supported on {self.__class__.__name__}"
        )

    async def fetch_start_progress(
        self, paas_device_id: str
    ) -> FetchStartProgressResult:
        """Fetch device start/initialization progress from the platform.

        Only supported on LOCAL platform. Returns the current progress
        status from the mng daemon. All other platforms automatically raise
        NotImplementedError via this base class default implementation.

        BaaS only validates that ``progress`` is present in the result;
        its type and value are defined by the mng daemon. All other fields
        are mng-daemon-defined and passed through via extra="allow".

        Args:
            paas_device_id: Raw platform-specific device ID (without
                @template_id suffix). The facade strips the suffix before
                passing to this method.

        Returns:
            FetchStartProgressResult with required ``progress`` field
            plus any additional mng-daemon-defined fields.

        Raises:
            NotImplementedError: If the platform does not support
                start-progress queries (all non-LOCAL platforms by default).
        """
        raise NotImplementedError(
            f"fetch_start_progress is not supported on {self.__class__.__name__}"
        )

    async def list_instances(self, params: dict[str, Any]) -> list[Any]:
        """List platform instances with flexible query parameters.

        Args:
            params: Platform-specific query parameters as a dictionary.
                Each platform interprets keys differently.

        Returns:
            List of platform-specific instance summary objects.
            Subclasses override with typed return values
            (e.g., list[PoolabInstanceSummary]).

        Raises:
            NotImplementedError: If the platform does not support instance listing.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support instance listing"
        )

    @abstractmethod
    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a device.

        Args:
            paas_device_id: Platform-specific device ID.
            port: Target port on the device.
            path: Request path to append to the HTTP URL. If None, the
                service implementation determines the default (typically "/").

        Returns:
            HttpConnectionInfo containing http_url and token.

        Raises:
            NotImplementedError: If the platform does not support
                HTTP invoke info resolution.
        """
        ...

    @abstractmethod
    async def pull_file_from_url(
        self,
        paas_device_id: str,
        source_url: str,
        device_path: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Download file from a URL to the device at the specified path.

        Args:
            paas_device_id: PaaS platform device ID in platform-specific
                format, without @template_id suffix.
            source_url: The URL to download from, e.g. OSS pre-signed GET URL.
            device_path: Absolute path on device to save the downloaded file to.
            timeout_seconds: Maximum download time in seconds (default: 300).

        Returns:
            None on success.

        Raises:
            NotImplementedError: If platform does not support file transfer.
            PaasError: With FILE_TRANSFER_FAILED if download fails.
        """
        ...

    @abstractmethod
    async def push_file_to_url(
        self,
        paas_device_id: str,
        device_path: str,
        target_url: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Upload file from device to the target URL (pre-signed PUT).

        Args:
            paas_device_id: PaaS platform device ID in platform-specific
                format, without @template_id suffix.
            device_path: Absolute path on device of the file to upload.
            target_url: The URL to upload to, e.g. OSS pre-signed PUT URL.
            timeout_seconds: Maximum upload time in seconds (default: 300).

        Returns:
            None on success.

        Raises:
            NotImplementedError: If platform does not support file transfer.
            PaasError: With FILE_TRANSFER_FAILED if upload fails.
        """
        ...
