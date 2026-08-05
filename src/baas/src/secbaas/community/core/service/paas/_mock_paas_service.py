"""Mock PaaS service for E2E testing.

Returns successful results without contacting real PaaS platforms.
Enabled via PAAS_MOCK_MODE=true environment variable.

Failure modes (set alongside PAAS_MOCK_MODE=true):
- PAAS_MOCK_CREATE_FAILURE=true: create_device() raises PaasError(DEVICE_CREATION_FAILED)
- PAAS_MOCK_DESTROY_FAILURE=true: destroy_device() raises PaasError(DEVICE_DESTROY_FAILED)
- PAAS_MOCK_DEVICE_NOT_FOUND=true: destroy_device() raises PaasError(DEVICE_NOT_FOUND)
- PAAS_MOCK_HOOK_FAILURE=true: execute_command() returns exit_code=1
"""

import asyncio  # noqa: F401
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    CommandResult,
    DeviceCreateConfig,
    DeviceInfo,
    ErrorCode,
    PaasCredentials,
    PaasError,
)
from secbaas.community.api.tenant_manage import TenantType

from ._paas_service import PaasService

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import (
        OutBoundOperationRule,
        OutBoundOperationRuleUpdatedMode,
    )


def _is_mock_failure(env_var: str) -> bool:
    return os.environ.get(env_var, "").lower() in ("true", "1", "yes")


class MockPaasService(PaasService):
    """Mock PaaS adapter that returns fake successful results.

    Used for E2E testing when real PaaS infrastructure is unavailable
    or CPU quotas are exhausted. All operations succeed immediately
    unless a failure env var is set.

    Failure modes (env vars checked at runtime):
    - PAAS_MOCK_HOOK_FAILURE: execute_command() returns exit_code=1
    - PAAS_MOCK_CREATE_FAILURE: create_device() raises PaasError(DEVICE_CREATION_FAILED)
    - PAAS_MOCK_DESTROY_FAILURE: destroy_device() raises PaasError(DEVICE_DESTROY_FAILED)
    - PAAS_MOCK_DEVICE_NOT_FOUND: destroy_device() raises PaasError(DEVICE_NOT_FOUND)

    Configurable file transfer failure attributes (test-only):
    - _pull_should_fail: Set to True to make pull_file_from_url() raise PaasError
    - _push_should_fail: Set to True to make push_file_to_url() raise PaasError
    """

    def __init__(self, credentials: PaasCredentials | None = None) -> None:
        self._credentials = credentials

    async def get_credentials(self) -> PaasCredentials:
        if self._credentials:
            return self._credentials
        return PaasCredentials(template_id=999999999, template_uuid="mock")

    async def get_platform_type(self) -> TenantType:
        """Return mock platform type (ARCA for compatibility)."""
        return TenantType.ARCA

    async def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        ws_conn_mode: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a mock device.

        Args:
            paas_device_id: Mock device ID.
            port: Target port on the device.
            path: WebSocket path (e.g., /api/openclaw/ws).

        Returns:
            WsConnectionInfo with ws:// URL, empty token, and 24h expiry.
        """
        target = f"MOCK_{paas_device_id}:{port}"
        ws_url = f"ws://127.0.0.1:{port}{path}"
        token = ""
        expires_at = datetime.now(UTC) + timedelta(hours=24)

        return WsConnectionInfo(
            ws_url=ws_url,
            token=token,
            target=target,
            expires_at=expires_at,
        )

    async def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str | None = None
    ) -> HttpConnectionInfo:
        """STUB: Resolve HTTP connection info for a mock device.

        Mock platform does not support HTTP invoke info resolution.

        Raises:
            NotImplementedError: Mock platform does not support HTTP invoke info.
        """
        raise NotImplementedError(
            "Mock platform does not support HTTP invoke info resolution"
        )

    async def create_device(self, config: DeviceCreateConfig) -> ArcaCreationResult:
        if _is_mock_failure("PAAS_MOCK_CREATE_FAILURE"):
            raise PaasError(
                ErrorCode.DEVICE_CREATION_FAILED, "mock device creation failure"
            )
        sandbox_id = f"mock-sandbox-{uuid.uuid4().hex[:12]}"
        credentials = await self.get_credentials()
        return ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id=str(credentials.template_id),
            sandbox_id=sandbox_id,
        )

    async def destroy_device(self, paas_device_id: str) -> bool:
        if _is_mock_failure("PAAS_MOCK_DEVICE_NOT_FOUND"):
            raise PaasError(ErrorCode.DEVICE_NOT_FOUND, "mock device not found")
        if _is_mock_failure("PAAS_MOCK_DESTROY_FAILURE"):
            raise PaasError(
                ErrorCode.DEVICE_DESTROY_FAILED, "mock device destroy failure"
            )
        return True

    async def execute_command(
        self,
        paas_device_id: str,
        cmd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> CommandResult:
        if _is_mock_failure("PAAS_MOCK_HOOK_FAILURE"):
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr="mock hook failure",
                execution_time_ms=0,
                command=cmd,
                env=env,
            )
        return CommandResult(
            exit_code=0,
            stdout="mock-output",
            stderr="",
            execution_time_ms=0,
            command=cmd,
            env=env,
        )

    async def get_device_info(self, paas_device_id: str) -> DeviceInfo:
        """Get mock device info.

        Args:
            paas_device_id: Mock device ID to query.

        Returns:
            DeviceInfo with mock data.
        """
        return DeviceInfo(
            platform="local",
            status="RUNNING",
        )

    async def update_outbound_operation_rule(
        self,
        paas_device_id: str,
        outbound_operation_rule: "OutBoundOperationRule",
        mode: "OutBoundOperationRuleUpdatedMode | None" = None,
    ) -> bool:
        """Mock update outbound operation rule.

        Args:
            paas_device_id: Mock device ID.
            outbound_operation_rule: Outbound operation rule to apply.

        Returns:
            True (always succeeds in mock mode).
        """
        return True

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
        """Invoke HTTP request on a mock device.

        Args:
            paas_device_id: Mock device ID.
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            port: Target port on the device.
            path: Request path.
            query_string: Query string including leading '?' or None/empty.
            headers: HTTP headers dict.
            body: Raw request body bytes.

        Returns:
            Dict with keys: status_code (int), headers (dict), body (base64 str).
            Returns a mock successful response.
        """
        return {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"mock": true}').decode("utf-8"),
        }

    async def restart_device(self, paas_device_id: str) -> bool:
        """Mock restart device.

        Args:
            paas_device_id: Mock device ID.

        Returns:
            True (always succeeds in mock mode).
        """
        return True

    async def update_device(
        self,
        paas_device_id: str,
        config: DeviceCreateConfig | None = None,
    ) -> bool:
        """Mock update device configuration.

        Args:
            paas_device_id: Mock device ID.
            config: Platform-specific device create configuration for the update.
                Ignored in mock mode.
                Defaults to None for backward compatibility.

        Returns:
            True (always succeeds in mock mode).
        """
        return True

    async def pull_file_from_url(
        self,
        paas_device_id: str,
        source_url: str,
        device_path: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Download file from a URL to the mock device at the specified path.

        Args:
            paas_device_id: Mock device ID.
            source_url: The URL to download from, e.g. OSS pre-signed GET URL.
            device_path: Absolute path on device to save the downloaded file to.
            timeout_seconds: Maximum download time in seconds (default: 300).

        Returns:
            None on success.

        Raises:
            PaasError: With FILE_TRANSFER_FAILED if _pull_should_fail is True.
        """
        if getattr(self, "_pull_should_fail", False):
            raise PaasError(ErrorCode.FILE_TRANSFER_FAILED, "mock file pull failure")

    async def push_file_to_url(
        self,
        paas_device_id: str,
        device_path: str,
        target_url: str,
        timeout_seconds: int = 300,
    ) -> None:
        """Upload file from mock device to the target URL (pre-signed PUT).

        Args:
            paas_device_id: Mock device ID.
            device_path: Absolute path on device of the file to upload.
            target_url: The URL to upload to, e.g. OSS pre-signed PUT URL.
            timeout_seconds: Maximum upload time in seconds (default: 300).

        Returns:
            None on success.

        Raises:
            PaasError: With FILE_TRANSFER_FAILED if _push_should_fail is True.
        """
        if getattr(self, "_push_should_fail", False):
            raise PaasError(ErrorCode.FILE_TRANSFER_FAILED, "mock file push failure")
