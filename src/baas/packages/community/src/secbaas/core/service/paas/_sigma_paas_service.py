"""Sigma platform PaaS adapter stub implementation.

Full implementation deferred to future phase.
This stub provides the interface with appropriate error handling.
"""

from __future__ import annotations

import asyncio  # noqa: F401
from typing import TYPE_CHECKING, Any

from secbaas.api.device_manage import CommandResult, DeviceCreateConfig

if TYPE_CHECKING:
    from secbaas.api.device_manage import DeviceInfo, OutBoundOperationRule

from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.api.device_manage import (
    ErrorCode,
    PaasError,
    SigmaCreateConfig,
    SigmaCreationResult,
    SigmaCredentials,
)
from secbaas.api.tenant_manage import TenantType

from ._paas_service import PaasService


class SigmaPaasService(PaasService):
    """Sigma platform PaaS adapter stub.

    Stub implementation for Sigma platform integration.
    All methods raise PaasError indicating unimplemented functionality.

    TODO: Full implementation in future phase.
    """

    def __init__(self, credentials: SigmaCredentials | None) -> None:
        """Initialize SigmaPaasService stub with credentials.

        Args:
            credentials: SigmaCredentials containing endpoint, access_key, secret_key, region.
                If None, default credentials will be loaded from configuration.

        TODO: Full implementation will use credentials for Sigma API calls:
            - credentials.endpoint: Sigma API endpoint
            - credentials.access_key: Authentication access key
            - credentials.secret_key: Authentication secret key
            - credentials.region: Target region

        TODO: Implement default credentials loading when credentials is None:
            - Load from config: sigma.endpoint, sigma.access_key, etc.
            - Or from environment variables: SIGMA_ENDPOINT, SIGMA_ACCESS_KEY, etc.
        """
        # TODO: Implement default credentials loading from configuration when credentials is None
        if credentials is None:
            # STUB: Load from config or environment variables
            # Example:
            #   endpoint = config.get("sigma.default_endpoint")
            #   access_key = config.get("sigma.default_access_key")
            #   secret_key = config.get("sigma.default_secret_key")
            #   region = config.get("sigma.default_region", "default")
            raise NotImplementedError(
                "Default credentials loading not yet implemented. "
                "Please provide credentials explicitly."
            )

        self._credentials = credentials

    async def get_credentials(self) -> SigmaCredentials:
        """Get the credentials used by this service instance.

        Returns:
            SigmaCredentials instance containing template_id and Sigma platform credentials.
        """
        return self._credentials

    async def get_platform_type(self) -> TenantType:
        """Return Sigma platform type."""
        return TenantType.SIGMA

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
    ) -> WsConnectionInfo:
        """STUB: Resolve WebSocket connection info for Sigma device.

        Args:
            paas_device_id: Sigma device ID.
            port: Target port on the device.
            path: WebSocket path.

        Raises:
            NotImplementedError: Sigma platform WebSocket connection not yet implemented.
        """
        raise NotImplementedError(
            "Sigma platform WebSocket connection not yet implemented"
        )

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """STUB: Resolve HTTP connection info for Sigma device.

        Raises:
            NotImplementedError: Sigma platform does not support HTTP invoke info.
        """
        raise NotImplementedError(
            "Sigma platform does not support HTTP invoke info resolution"
        )

    async def create_device(  # type: ignore[override]
        self,
        config: SigmaCreateConfig,
    ) -> SigmaCreationResult:
        """STUB: Create Sigma device.

        Args:
            config: SigmaCreateConfig containing:
                - template_id: str (required) - Sigma template ID
                - ttl_in_minutes: int (default: 60)
                - name: str | None - device name
                - description: str | None - device description
                - region: str | None - target region
                - zone: str | None - availability zone
                - vpc_config: dict | None - VPC configuration
                - resource_spec: ResourceSpec | None - CPU/memory specification
                - metadata: dict[str, str] | None - device metadata

        Raises:
            PaasError: Always raises with DEVICE_CREATION_FAILED.

        TODO: Implement Sigma device creation:
            - Map config to Sigma API parameters
            - Call Sigma container creation API
            - Return SigmaCreationResult with device details
        """
        raise PaasError(
            ErrorCode.DEVICE_CREATION_FAILED,
            "Sigma platform integration not yet implemented",
        )

    async def destroy_device(self, paas_device_id: str) -> bool:
        """STUB: Destroy Sigma device.

        Args:
            paas_device_id: Device ID to destroy (Sigma-specific format)

        Raises:
            PaasError: Always raises with DEVICE_DESTROY_FAILED.

        TODO: Implement Sigma device destruction:
            - Call Sigma container deletion API
            - Cleanup associated resources
            - Return success status
        """
        raise PaasError(
            ErrorCode.DEVICE_DESTROY_FAILED,
            "Sigma platform integration not yet implemented",
        )

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        """STUB: Execute command on Sigma device.

        Args:
            paas_device_id: Target device ID (Sigma-specific format)
            cmd: Command to execute
            env: Command execution context (environment variables), optional
            timeout_seconds: Maximum execution time in seconds (default: 30)

        Raises:
            PaasError: Always raises with COMMAND_FAILED.

        TODO: Implement Sigma command execution:
            - Call Sigma exec API or SSH equivalent
            - Stream command output
            - Return CommandResult with exit_code, stdout, stderr, env
        """
        raise PaasError(
            ErrorCode.COMMAND_FAILED,
            "Sigma platform integration not yet implemented",
        )

    async def get_device_info(self, paas_device_id: str) -> DeviceInfo:
        """STUB: Get Sigma device info.

        Args:
            paas_device_id: Device ID to query (Sigma-specific format).

        Raises:
            PaasError: Always raises with DEVICE_NOT_FOUND.

        TODO: Implement Sigma device info retrieval:
            - Call Sigma container info API
            - Return SigmaDeviceInfo with device details
        """
        raise PaasError(
            ErrorCode.DEVICE_NOT_FOUND,
            "Sigma platform get_device_info not yet implemented",
        )

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: OutBoundOperationRule,
    ) -> bool:
        """STUB: Update outbound operation rule for Sigma device.

        Args:
            paas_device_id: Device ID to update (Sigma-specific format).
            outbound_operation_rule: New outbound operation rule to apply.

        Raises:
            PaasError: Always raises with DEVICE_UNAVAILABLE.

        TODO: Implement Sigma outbound rule update:
            - Call Sigma security group or firewall API
            - Apply outbound operation rule
            - Return success status
        """
        raise PaasError(
            ErrorCode.DEVICE_UNAVAILABLE,
            "Sigma platform update_outbound_operation_rule not yet implemented",
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
        """STUB: Invoke HTTP request on a Sigma device.

        Args:
            paas_device_id: Sigma device ID.
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the device.
            path: Request path.
            query_string: Query string or None.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Raises:
            NotImplementedError: Sigma platform does not support HTTP invocation.
                Only Local platform supports direct HTTP proxy to containers.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support HTTP invocation. "
            "Only Local platform supports direct HTTP proxy to containers."
        )

    async def restart_device(self, paas_device_id: str) -> bool:
        """STUB: Restart Sigma device.

        Args:
            paas_device_id: Device ID to restart (Sigma-specific format).

        Raises:
            NotImplementedError: Sigma platform restart not yet implemented.
        """
        raise NotImplementedError("Sigma platform restart_device not yet implemented")

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """STUB: Update Sigma device configuration.

        Raises:
            NotImplementedError: Sigma platform update not yet implemented.
        """
        raise NotImplementedError("Sigma platform update_device not yet implemented")
