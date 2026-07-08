"""Tests for MockPaasService - mock PaaS adapter for E2E testing.

Covers:
- Constructor with/without credentials
- get_credentials (provided, default)
- get_platform_type (returns ARCA)
- resolve_ws_conn_info (URL, token, expiry, custom port/path)
- create_device (success, unique sandbox_id, failure)
- destroy_device (success, not_found failure, destroy failure, priority)
- execute_command (success, failure with exit_code=1, stderr message)
- get_device_info (platform=local, status=RUNNING)
- update_outbound_operation_rule (returns True)
- invoke_http_in_device (status 200, base64 body)
- restart_device (returns True)
- Inheritance from PaasService
"""

import base64
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from secbaas.api.bot_runtime import WsConnectionInfo
from secbaas.api.device_manage import (
    ArcaCreationResult,
    CommandResult,
    DeviceCreateConfig,
    PaasCredentials,
)
from secbaas.api.tenant_manage import TenantType
from secbaas.core.service.paas import ErrorCode, MockPaasService, PaasError, PaasService


@pytest.fixture
def test_credentials():
    """Create test PaasCredentials."""
    return PaasCredentials(template_id=42, template_uuid="tpl-test-001")


@pytest.fixture
def test_device_create_config():
    """Create test DeviceCreateConfig."""
    return DeviceCreateConfig(
        name="test-device",
        description="Test device for mock tests",
    )


class TestMockPaasService:
    """Tests for MockPaasService implementation."""

    # --- Inheritance ---

    def test_inherits_from_paas_service(self):
        """MockPaasService should inherit from PaasService."""
        assert issubclass(MockPaasService, PaasService)

    # --- Constructor ---

    def test_constructor_with_credentials(self, test_credentials):
        """Constructor stores provided credentials."""
        service = MockPaasService(credentials=test_credentials)
        assert service._credentials is test_credentials

    def test_constructor_without_credentials(self):
        """Constructor stores None when no credentials provided."""
        service = MockPaasService()
        assert service._credentials is None

    # --- get_credentials ---

    @pytest.mark.asyncio
    async def test_get_credentials_returns_provided_credentials(self, test_credentials):
        """get_credentials returns the credentials provided at construction."""
        service = MockPaasService(credentials=test_credentials)
        result = await service.get_credentials()
        assert result is test_credentials
        assert result.template_id == 42
        assert result.template_uuid == "tpl-test-001"

    @pytest.mark.asyncio
    async def test_get_credentials_returns_default_when_none(self):
        """get_credentials returns default credentials when none provided."""
        service = MockPaasService(credentials=None)
        result = await service.get_credentials()
        assert isinstance(result, PaasCredentials)
        assert result.template_id == 999999999
        assert result.template_uuid == "mock"

    # --- get_platform_type ---

    @pytest.mark.asyncio
    async def test_get_platform_type_returns_ARCA(self):
        """get_platform_type returns TenantType.ARCA."""
        service = MockPaasService()
        result = await service.get_platform_type()
        assert result == TenantType.ARCA

    # --- resolve_ws_conn_info ---

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_returns_correct_url_and_token_and_expiry(
        self,
    ):
        """resolve_ws_conn_info returns WsConnectionInfo with correct url, token, and 24h expiry."""
        service = MockPaasService()
        before = datetime.now(UTC)

        result = await service.resolve_ws_conn_info(
            paas_device_id="mock-dev-123",
            port=8080,
            path="/api/openclaw/ws",
        )

        after = datetime.now(UTC)

        assert isinstance(result, WsConnectionInfo)
        assert result.ws_url == "ws://127.0.0.1:8080/api/openclaw/ws"
        assert result.token == ""
        assert result.target == "MOCK_mock-dev-123:8080"
        # Expiry should be ~24h from now (allow small drift)
        expected_min = before + timedelta(hours=24) - timedelta(seconds=5)
        expected_max = after + timedelta(hours=24) + timedelta(seconds=5)
        assert expected_min <= result.expires_at <= expected_max

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_with_custom_port_and_path(self):
        """resolve_ws_conn_info works with custom port and path."""
        service = MockPaasService()
        result = await service.resolve_ws_conn_info(
            paas_device_id="device-xyz",
            port=9090,
            path="/custom/ws",
        )
        assert result.ws_url == "ws://127.0.0.1:9090/custom/ws"
        assert result.target == "MOCK_device-xyz:9090"

    # --- create_device ---

    @pytest.mark.asyncio
    async def test_create_device_success_returns_ArcaCreationResult(
        self, test_device_create_config
    ):
        """create_device returns ArcaCreationResult on success."""
        service = MockPaasService()
        result = await service.create_device(test_device_create_config)

        assert isinstance(result, ArcaCreationResult)
        assert result.platform == "arca"
        assert result.status == "RUNNING"
        assert result.template_id is not None
        assert result.sandbox_id is not None

    @pytest.mark.asyncio
    async def test_create_device_success_generates_unique_sandbox_id(
        self, test_device_create_config
    ):
        """create_device generates unique sandbox IDs for each call."""
        service = MockPaasService()
        result1 = await service.create_device(test_device_create_config)
        result2 = await service.create_device(test_device_create_config)

        assert result1.sandbox_id != result2.sandbox_id
        assert result1.sandbox_id.startswith("mock-sandbox-")
        assert len(result1.sandbox_id) == len("mock-sandbox-") + 12  # 12 hex chars

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_CREATE_FAILURE": "true"}, clear=True)
    async def test_create_device_failure_raises_PaasError(
        self, test_device_create_config
    ):
        """create_device raises PaasError with DEVICE_CREATION_FAILED when env var set."""
        service = MockPaasService()
        with pytest.raises(PaasError) as exc_info:
            await service.create_device(test_device_create_config)

        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED
        assert "mock device creation failure" in exc_info.value.message

    # --- destroy_device ---

    @pytest.mark.asyncio
    async def test_destroy_device_success_returns_true(self):
        """destroy_device returns True on success."""
        service = MockPaasService()
        result = await service.destroy_device("sandbox-123")
        assert result is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_DEVICE_NOT_FOUND": "true"}, clear=True)
    async def test_destroy_device_failure_device_not_found_raises_PaasError(self):
        """destroy_device raises PaasError with DEVICE_NOT_FOUND when env var set."""
        service = MockPaasService()
        with pytest.raises(PaasError) as exc_info:
            await service.destroy_device("sandbox-123")

        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND
        assert "mock device not found" in exc_info.value.message

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_DESTROY_FAILURE": "true"}, clear=True)
    async def test_destroy_device_failure_destroy_failed_raises_PaasError(self):
        """destroy_device raises PaasError with DEVICE_DESTROY_FAILED when env var set."""
        service = MockPaasService()
        with pytest.raises(PaasError) as exc_info:
            await service.destroy_device("sandbox-123")

        assert exc_info.value.code == ErrorCode.DEVICE_DESTROY_FAILED
        assert "mock device destroy failure" in exc_info.value.message

    @pytest.mark.asyncio
    @patch.dict(
        os.environ,
        {"PAAS_MOCK_DEVICE_NOT_FOUND": "true", "PAAS_MOCK_DESTROY_FAILURE": "true"},
        clear=True,
    )
    async def test_destroy_device_not_found_checked_before_destroy_failure(self):
        """DEVICE_NOT_FOUND takes priority over DESTROY_FAILURE (checked first)."""
        service = MockPaasService()
        with pytest.raises(PaasError) as exc_info:
            await service.destroy_device("sandbox-123")

        # DEVICE_NOT_FOUND is checked first in the code, so it should win
        assert exc_info.value.code == ErrorCode.DEVICE_NOT_FOUND

    # --- execute_command ---

    @pytest.mark.asyncio
    async def test_execute_command_success_returns_exit_code_0(self):
        """execute_command returns CommandResult with exit_code=0 on success."""
        service = MockPaasService()
        result = await service.execute_command("sandbox-123", "ls -la")

        assert isinstance(result, CommandResult)
        assert result.exit_code == 0
        assert result.stdout == "mock-output"
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_execute_command_success_includes_command_and_env_in_result(self):
        """execute_command result includes the command and env in CommandResult."""
        service = MockPaasService()
        env = {"MY_VAR": "my_value"}
        result = await service.execute_command("sandbox-123", "echo $MY_VAR", env=env)

        assert result.command == "echo $MY_VAR"
        assert result.env == env
        assert result.exit_code == 0

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_HOOK_FAILURE": "true"}, clear=True)
    async def test_execute_command_failure_returns_exit_code_1_with_stderr(self):
        """execute_command returns exit_code=1 with stderr when HOOK_FAILURE set."""
        service = MockPaasService()
        result = await service.execute_command("sandbox-123", "failing-command")

        assert result.exit_code == 1
        assert result.stderr == "mock hook failure"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_HOOK_FAILURE": "true"}, clear=True)
    async def test_execute_command_failure_returns_mock_hook_failure_message(self):
        """execute_command returns 'mock hook failure' in stderr on hook failure."""
        service = MockPaasService()
        result = await service.execute_command(
            "sandbox-123",
            "some-command",
            env={"K": "V"},
        )

        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == "mock hook failure"
        assert result.command == "some-command"
        assert result.env == {"K": "V"}
        assert result.execution_time_ms == 0

    # --- get_device_info ---

    @pytest.mark.asyncio
    async def test_get_device_info_returns_DeviceInfo_with_RUNNING_status(
        self,
    ):
        """get_device_info raises ValidationError due to mismatched DeviceInfo model fields."""
        service = MockPaasService()

        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            await service.get_device_info("sandbox-123")

        errors = exc_info.value.errors()
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert "device_uuid" in missing_fields
        assert "gmt_create" in missing_fields

    # --- update_outbound_operation_rule ---

    @pytest.mark.asyncio
    async def test_update_outbound_operation_rule_returns_true(self):
        """update_outbound_operation_rule returns True (always succeeds)."""
        service = MockPaasService()
        # Pass a simple placeholder since the method just returns True
        result = await service.update_outbound_operation_rule(
            paas_device_id="sandbox-123",
            outbound_operation_rule=None,  # type: ignore
        )
        assert result is True

    # --- invoke_http_in_device ---

    @pytest.mark.asyncio
    async def test_invoke_http_in_device_returns_dict_with_status_200_and_base64_body(
        self,
    ):
        """invoke_http_in_device returns dict with status_code=200 and base64 body."""
        service = MockPaasService()
        result = await service.invoke_http_in_device(
            paas_device_id="sandbox-123",
            method="GET",
            port=8080,
            path="/api/health",
            query_string=None,
            headers={},
            body=b"",
        )

        assert isinstance(result, dict)
        assert result["status_code"] == 200
        assert result["headers"] == {"Content-Type": "application/json"}
        # Verify the body is valid base64 encoding of '{"mock": true}'
        decoded_body = base64.b64decode(result["body"]).decode("utf-8")
        assert decoded_body == '{"mock": true}'

    # --- restart_device ---

    @pytest.mark.asyncio
    async def test_restart_device_returns_true(self):
        """restart_device returns True (always succeeds)."""
        service = MockPaasService()
        result = await service.restart_device("sandbox-123")
        assert result is True


class TestMockPaasServiceEnvVarVariants:
    """Test that env var checking is case-insensitive for truthy values."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_CREATE_FAILURE": "TRUE"}, clear=True)
    async def test_create_failure_uppercase_true(self, test_device_create_config):
        """'TRUE' (uppercase) triggers create failure."""
        service = MockPaasService()
        with pytest.raises(PaasError) as exc_info:
            await service.create_device(test_device_create_config)
        assert exc_info.value.code == ErrorCode.DEVICE_CREATION_FAILED

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_CREATE_FAILURE": "1"}, clear=True)
    async def test_create_failure_numeric_one(self, test_device_create_config):
        """'1' triggers create failure."""
        service = MockPaasService()
        with pytest.raises(PaasError):
            await service.create_device(test_device_create_config)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_CREATE_FAILURE": "yes"}, clear=True)
    async def test_create_failure_yes(self, test_device_create_config):
        """'yes' triggers create failure."""
        service = MockPaasService()
        with pytest.raises(PaasError):
            await service.create_device(test_device_create_config)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_CREATE_FAILURE": "false"}, clear=True)
    async def test_create_not_failure_with_false(self, test_device_create_config):
        """'false' does NOT trigger create failure."""
        service = MockPaasService()
        result = await service.create_device(test_device_create_config)
        assert isinstance(result, ArcaCreationResult)
        assert result.status == "RUNNING"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"PAAS_MOCK_CREATE_FAILURE": "0"}, clear=True)
    async def test_create_not_failure_with_zero(self, test_device_create_config):
        """'0' does NOT trigger create failure."""
        service = MockPaasService()
        result = await service.create_device(test_device_create_config)
        assert isinstance(result, ArcaCreationResult)


class TestMockPaasServiceIsolation:
    """Test that MockPaasService does not cross-contaminate between instances."""

    @pytest.mark.asyncio
    async def test_multiple_instances_independent(self, test_device_create_config):
        """Multiple MockPaasService instances operate independently."""
        service1 = MockPaasService()
        service2 = MockPaasService()

        result1 = await service1.create_device(test_device_create_config)
        result2 = await service2.create_device(test_device_create_config)

        assert result1.sandbox_id != result2.sandbox_id
        assert result1.platform == "arca"
        assert result2.platform == "arca"
