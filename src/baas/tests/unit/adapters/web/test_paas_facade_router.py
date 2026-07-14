"""Unit tests for paas_facade_router.

Tests the HTTP endpoints that expose PaasServiceFacade methods.
Uses FastAPI TestClient for endpoint testing with mocked facade.
"""

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from dependency_injector.wiring import Provide
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.app import app
from secbaas.community.adapters.web.routers.paas_service.paas_facade_router import (
    router as _router,
)
from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    ArcaDeviceConfig,
    CommandResult,
)
from secbaas.community.api.health_check.bot import TTLInfo
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    DeviceNotActiveException,
    DeviceNotFoundException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
)
from tests.unit.adapters.web.conftest import iter_api_routes


@pytest.fixture(autouse=True)
def _patch_facade():
    """Override DI-injected Depends(Provide[...]) with a mock via dependency_overrides.

    Iterates the app's route tree (handling _IncludedRouter wrappers) to find
    and replace every Provide[services.paas_facade] dependency.
    """
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: AsyncMock(
                    spec=PaasServiceFacade
                )
    yield
    app.dependency_overrides = old_overrides


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def mock_facade():
    """Create a mock facade for testing using AsyncMock with spec.

    Iterates the app's route tree to find and replace every
    Provide[services.paas_facade] dependency with the mock.
    """
    mock = AsyncMock(spec=PaasServiceFacade)

    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: mock

    yield {
        "create_device": mock.create_device,
        "destroy_device": mock.destroy_device,
        "execute_command": mock.execute_command,
        "resolve_ws_conn_info": mock.resolve_ws_conn_info,
        "invoke_http_in_device": mock.invoke_http_in_device,
        "update_device_ttl": mock.update_device_ttl,
        "update_outbound_operation_rule": mock.update_outbound_operation_rule,
        "get_device_info": mock.get_device_info,
        "_mock": mock,
    }

    # Restore the original dependency
    app.dependency_overrides = old_overrides


@pytest.mark.unit
class TestCreateDeviceEndpoint:
    """Test POST /api/v1/paas/devices endpoint."""

    def test_create_device_arca_success(self, client, mock_facade):
        """Successfully create an ARCA device via HTTP endpoint with new signature."""
        mock_result = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="template-123",
            sandbox_id="sandbox-abc123@42",
        )
        mock_facade["create_device"].return_value = mock_result

        # NEW request format: tenant_name + optional device_template_uuid + optional detail_config
        request_data = {
            "tenant_name": "test-tenant",
            "device_template_uuid": "template-uuid-123",
            "detail_config": {
                "ttl_in_minutes": 60,
                "name": "test-device",
                "description": "Test device",
            },
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        assert response.status_code == 200
        result = response.json()
        assert result["data"]["platform"] == "arca"
        assert result["data"]["status"] == "RUNNING"

        # Verify facade called with new parameters
        mock_facade["create_device"].assert_called_once()
        call_kwargs = mock_facade["create_device"].call_args
        assert call_kwargs.kwargs["tenant_name"] == "test-tenant"
        assert call_kwargs.kwargs["device_template_uuid"] == "template-uuid-123"
        assert isinstance(call_kwargs.kwargs["detail_config"], ArcaDeviceConfig)

    def test_create_device_validation_error_missing_tenant_name(self, client):
        """Validation error when tenant_name is missing (required field)."""
        request_data = {
            "device_template_uuid": "template-uuid",
            "detail_config": {"name": "test"},
            # tenant_name is missing - required per D-04
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        assert response.status_code == 422  # Validation error

    def test_create_device_without_template_uuid_uses_default(
        self, client, mock_facade
    ):
        """Create device without template UUID uses tenant default."""
        mock_result = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="default-template",
            sandbox_id="sandbox-default@42",
        )
        mock_facade["create_device"].return_value = mock_result

        # Omit device_template_uuid - should use tenant default
        request_data = {
            "tenant_name": "test-tenant",
            # device_template_uuid not provided - triggers default lookup
            "detail_config": None,
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        assert response.status_code == 200

        call_kwargs = mock_facade["create_device"].call_args
        assert call_kwargs.kwargs["tenant_name"] == "test-tenant"
        assert call_kwargs.kwargs["device_template_uuid"] is None
        assert call_kwargs.kwargs["detail_config"] is None

    def test_create_device_facade_error_mapping(self, client, mock_facade):
        """DeviceFacadeException is correctly mapped to HTTP status codes."""
        # Setup facade to raise DeviceFacadeException
        original_error = PaasError(
            ErrorCode.DEVICE_CREATION_FAILED, "Failed to create device"
        )
        facade_exception = DeviceFacadeException(
            operation="create_device",
            platform_type="ARCA",
            template_id=42,
            paas_device_id=None,
            original_error=original_error,
        )
        mock_facade["create_device"].side_effect = facade_exception

        request_data = {
            "tenant_name": "test-tenant",
            "device_template_uuid": "template-uuid",
            "detail_config": {"name": "test-device"},
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        # Verify: 500 for DEVICE_CREATION_FAILED
        assert response.status_code == 500
        result = response.json()
        assert result["detail"]["error_code"] == "DEVICE_CREATION_FAILED"
        assert "create_device" in result["detail"]["context"]["operation"]

    def test_create_device_platform_unavailable(self, client, mock_facade):
        """PLATFORM_UNAVAILABLE error maps to 503 status code."""
        original_error = PaasError(
            ErrorCode.PLATFORM_UNAVAILABLE, "Arca platform is not reachable"
        )
        facade_exception = DeviceFacadeException(
            operation="create_device",
            platform_type="ARCA",
            template_id=42,
            paas_device_id=None,
            original_error=original_error,
        )
        mock_facade["create_device"].side_effect = facade_exception

        request_data = {
            "tenant_name": "test-tenant",
            "device_template_uuid": "template-uuid",
            "detail_config": {"name": "test-device"},
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        # Verify: 503 for PLATFORM_UNAVAILABLE
        assert response.status_code == 503
        result = response.json()
        assert result["detail"]["error_code"] == "PLATFORM_UNAVAILABLE"

    def test_create_device_config_invalid(self, client, mock_facade):
        """CONFIG_INVALID error maps to 400 status code."""
        original_error = PaasError(
            ErrorCode.CONFIG_INVALID, "Invalid template_id format"
        )
        facade_exception = DeviceFacadeException(
            operation="create_device",
            platform_type="ARCA",
            template_id=42,
            paas_device_id=None,
            original_error=original_error,
        )
        mock_facade["create_device"].side_effect = facade_exception

        request_data = {
            "tenant_name": "test-tenant",
            "device_template_uuid": "template-uuid",
            "detail_config": {"name": "test-device"},
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        # Verify: 400 for CONFIG_INVALID
        assert response.status_code == 400
        result = response.json()
        assert result["detail"]["error_code"] == "CONFIG_INVALID"


@pytest.mark.unit
class TestDestroyDeviceEndpoint:
    """Test DELETE /api/v1/paas/devices/{paas_device_id} endpoint."""

    def test_destroy_device_success(self, client, mock_facade):
        """Successfully destroy a device via HTTP endpoint."""
        mock_facade["destroy_device"].return_value = True

        response = client.delete("/api/v1/paas/devices/sandbox-abc123@42")

        assert response.status_code == 200
        result = response.json()
        assert result["data"]["success"] is True

        # Verify facade called correctly
        mock_facade["destroy_device"].assert_called_once_with(
            paas_device_id="sandbox-abc123@42"
        )

    def test_destroy_device_not_found(self, client, mock_facade):
        """Device not found returns 404."""
        original_error = PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device does not exist")
        facade_exception = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-missing@42",
            original_error=original_error,
        )
        mock_facade["destroy_device"].side_effect = facade_exception

        response = client.delete("/api/v1/paas/devices/sandbox-missing@42")

        assert response.status_code == 404
        result = response.json()
        assert result["detail"]["error_code"] == "DEVICE_NOT_FOUND"
        assert result["detail"]["context"]["paas_device_id"] == "sandbox-missing@42"

    def test_destroy_device_destroy_failed(self, client, mock_facade):
        """Device destroy failure returns 500."""
        original_error = PaasError(
            ErrorCode.DEVICE_DESTROY_FAILED, "Failed to destroy device"
        )
        facade_exception = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-abc@42",
            original_error=original_error,
        )
        mock_facade["destroy_device"].side_effect = facade_exception

        response = client.delete("/api/v1/paas/devices/sandbox-abc@42")

        assert response.status_code == 500
        result = response.json()
        assert result["detail"]["error_code"] == "DEVICE_DESTROY_FAILED"


@pytest.mark.unit
class TestExecuteCommandEndpoint:
    """Test POST /api/v1/paas/devices/{paas_device_id}/commands endpoint."""

    def test_execute_command_success(self, client, mock_facade):
        """Successfully execute command on a device."""
        mock_result = CommandResult(
            exit_code=0,
            stdout="Hello World",
            stderr="",
            execution_time_ms=150,
            command="echo 'Hello World'",
            env=None,
        )
        mock_facade["execute_command"].return_value = mock_result

        request_data = {"cmd": "echo 'Hello World'"}

        response = client.post(
            "/api/v1/paas/devices/sandbox-abc@42/commands", json=request_data
        )

        assert response.status_code == 200
        result = response.json()
        assert result["data"]["exit_code"] == 0
        assert result["data"]["stdout"] == "Hello World"
        assert result["data"]["stderr"] == ""
        assert result["data"]["command"] == "echo 'Hello World'"

        # Verify facade called correctly
        mock_facade["execute_command"].assert_called_once_with(
            paas_device_id="sandbox-abc@42",
            cmd="echo 'Hello World'",
            env=None,
        )

    def test_execute_command_with_env(self, client, mock_facade):
        """Execute command with environment variables."""
        mock_result = CommandResult(
            exit_code=0,
            stdout="KEY=value\nFOO=bar",
            stderr="",
            execution_time_ms=200,
            command="env",
            env={"KEY": "value", "FOO": "bar"},
        )
        mock_facade["execute_command"].return_value = mock_result

        request_data = {"cmd": "env", "env": {"KEY": "value", "FOO": "bar"}}

        response = client.post(
            "/api/v1/paas/devices/sandbox-abc@42/commands", json=request_data
        )

        assert response.status_code == 200
        result = response.json()
        assert result["data"]["exit_code"] == 0

        # Verify facade called with env
        mock_facade["execute_command"].assert_called_once_with(
            paas_device_id="sandbox-abc@42",
            cmd="env",
            env={"KEY": "value", "FOO": "bar"},
        )

    def test_execute_command_validation_error(self, client):
        """Validation error when cmd is empty."""
        request_data = {"cmd": ""}  # Empty command - min_length=1

        response = client.post(
            "/api/v1/paas/devices/sandbox-abc@42/commands", json=request_data
        )

        assert response.status_code == 422  # Validation error

    def test_execute_command_device_unavailable(self, client, mock_facade):
        """Device unavailable returns 503."""
        original_error = PaasError(
            ErrorCode.DEVICE_UNAVAILABLE, "Device is not responding"
        )
        facade_exception = DeviceFacadeException(
            operation="execute_command",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-abc@42",
            original_error=original_error,
        )
        mock_facade["execute_command"].side_effect = facade_exception

        request_data = {"cmd": "ls"}

        response = client.post(
            "/api/v1/paas/devices/sandbox-abc@42/commands", json=request_data
        )

        assert response.status_code == 503
        result = response.json()
        assert result["detail"]["error_code"] == "DEVICE_UNAVAILABLE"

    def test_execute_command_timeout(self, client, mock_facade):
        """Command timeout returns 504."""
        original_error = PaasError(
            ErrorCode.COMMAND_TIMEOUT, "Command timed out after 30s"
        )
        facade_exception = DeviceFacadeException(
            operation="execute_command",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-abc@42",
            original_error=original_error,
        )
        mock_facade["execute_command"].side_effect = facade_exception

        request_data = {"cmd": "sleep 100"}

        response = client.post(
            "/api/v1/paas/devices/sandbox-abc@42/commands", json=request_data
        )

        assert response.status_code == 504
        result = response.json()
        assert result["detail"]["error_code"] == "COMMAND_TIMEOUT"

    def test_execute_command_device_not_found(self, client, mock_facade):
        """Device not found returns 404."""
        original_error = PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device does not exist")
        facade_exception = DeviceFacadeException(
            operation="execute_command",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-missing@42",
            original_error=original_error,
        )
        mock_facade["execute_command"].side_effect = facade_exception

        request_data = {"cmd": "ls"}

        response = client.post(
            "/api/v1/paas/devices/sandbox-missing@42/commands", json=request_data
        )

        assert response.status_code == 404
        result = response.json()
        assert result["detail"]["error_code"] == "DEVICE_NOT_FOUND"


@pytest.mark.unit
class TestErrorResponseFormat:
    """Test error response format consistency."""

    def test_error_response_structure(self, client, mock_facade):
        """Error response follows expected structure."""
        original_error = PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found")
        facade_exception = DeviceFacadeException(
            operation="destroy_device",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="test-device@42",
            original_error=original_error,
        )
        mock_facade["destroy_device"].side_effect = facade_exception

        response = client.delete("/api/v1/paas/devices/test-device@42")

        assert response.status_code == 404
        result = response.json()
        detail = result["detail"]

        # Verify structure
        assert "error_code" in detail
        assert "message" in detail
        assert "context" in detail

        # Verify context structure
        context = detail["context"]
        assert "operation" in context
        assert "platform_type" in context
        assert "template_id" in context
        assert "paas_device_id" in context

    def test_internal_error_response(self, client, mock_facade):
        """Unexpected errors return INTERNAL_ERROR format."""
        mock_facade["create_device"].side_effect = Exception("Unexpected error")

        request_data = {
            "tenant_name": "test-tenant",
            "device_template_uuid": "template-uuid",
            "detail_config": {"name": "test-device"},
        }

        response = client.post("/api/v1/paas/devices", json=request_data)

        assert response.status_code == 500
        result = response.json()
        assert result["detail"]["error_code"] == "INTERNAL_ERROR"
        assert "Unexpected error" in result["detail"]["message"]


@pytest.mark.unit
class TestGetWsConnectionInfo:
    """Test cases for GET /api/v1/paas/devices/{paas_device_id}/ws-info endpoint."""

    def test_get_ws_connection_info_success(self, client, mock_facade):
        """Should return 200 with connection info."""
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        mock_conn_info = WsConnectionInfo(
            ws_url="wss://example.com/ws",
            token="token123",
            target="ARCA_TEST:8080",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        mock_facade["resolve_ws_conn_info"].return_value = mock_conn_info

        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=8080&path=/api/openclaw/ws"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["ws_url"] == "wss://example.com/ws"
        assert data["data"]["token"] == "token123"
        assert data["data"]["target"] == "ARCA_TEST:8080"
        assert "expires_at" in data["data"]

        # Verify facade called correctly
        mock_facade["resolve_ws_conn_info"].assert_called_once_with(
            paas_device_id="ARCA-123@42",
            port=8080,
            path="/api/openclaw/ws",
        )

    def test_get_ws_connection_info_invalid_port(self, client):
        """Should return 422 for invalid port values."""
        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=0&path=/api/openclaw/ws"
        )
        assert response.status_code == 422

        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=70000&path=/api/openclaw/ws"
        )
        assert response.status_code == 422

    def test_get_ws_connection_info_missing_port(self, client):
        """Should return 422 when port parameter is missing."""
        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?path=/api/openclaw/ws"
        )
        assert response.status_code == 422

    def test_get_ws_connection_info_device_not_found(self, client, mock_facade):
        """Should return 404 when device not found."""
        mock_facade["resolve_ws_conn_info"].side_effect = DeviceNotFoundException(
            "Device not found", paas_device_id="ARCA-123@42"
        )

        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=8080&path=/api/openclaw/ws"
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "DEVICE_NOT_FOUND"

    def test_get_ws_connection_info_device_not_active(self, client, mock_facade):
        """Should return 409 when device not active."""
        mock_facade["resolve_ws_conn_info"].side_effect = DeviceNotActiveException(
            "Device not active", paas_device_id="ARCA-123@42"
        )

        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=8080&path=/api/openclaw/ws"
        )

        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["error"] == "DEVICE_NOT_ACTIVE"

    def test_get_ws_connection_info_sigma_not_implemented(self, client, mock_facade):
        """Should return 501 for Sigma platform."""
        mock_facade["resolve_ws_conn_info"].side_effect = NotImplementedError(
            "Sigma platform not yet implemented"
        )

        response = client.get(
            "/api/v1/paas/devices/sigma-123@42/ws-info?port=8080&path=/api/openclaw/ws"
        )

        assert response.status_code == 501
        data = response.json()
        assert data["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.unit
class TestInvokeHttpEndpoint:
    """Test GET/POST/PUT/DELETE /api/v1/paas/devices/{id}/invoke-http/{port}/{path}."""

    def test_invoke_http_get_success(self, client, mock_facade):
        """Successfully proxy GET request to device internal service."""
        mock_response = {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"status": "healthy"}').decode(),
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health"
        )

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

        # Verify facade called correctly
        mock_facade["invoke_http_in_device"].assert_called_once()
        call_kwargs = mock_facade["invoke_http_in_device"].call_args.kwargs
        assert call_kwargs["paas_device_id"] == "device@1"
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["port"] == 8080
        assert call_kwargs["path"] == "/api/health"

    def test_invoke_http_post_with_body(self, client, mock_facade):
        """Successfully proxy POST request with JSON body."""
        mock_response = {
            "status_code": 201,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"id": 123, "name": "test"}').decode(),
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.post(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/users",
            json={"name": "test"},
        )

        assert response.status_code == 201
        assert response.json()["id"] == 123

    def test_invoke_http_not_found(self, client, mock_facade):
        """Device not found returns 404."""
        facade_exception = DeviceFacadeException(
            operation="invoke_http_in_device",
            platform_type="LOCAL",
            template_id=1,
            paas_device_id="device@1",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
        )
        mock_facade["invoke_http_in_device"].side_effect = facade_exception

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health"
        )

        assert response.status_code == 404

    def test_invoke_http_method_not_allowed(self, client, mock_facade):
        """PATCH method is not allowed."""
        mock_response = {
            "status_code": 200,
            "headers": {},
            "body": "",
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.patch(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health"
        )

        # Router only accepts GET, POST, PUT, DELETE - PATCH returns 405
        assert response.status_code == 405


@pytest.mark.unit
class TestUpdateDeviceTtlEndpoint:
    """Test PUT /api/v1/paas/devices/{id}/ttl endpoint."""

    def test_update_ttl_success(self, client, mock_facade):
        """Successfully update device TTL."""
        mock_ttl_info = TTLInfo(
            paas_device_id="device@1",
            old_expiration_time=datetime.now(UTC) + timedelta(hours=1),
            new_expiration_time=datetime.now(UTC) + timedelta(hours=24),
            success=True,
            skipped=False,
            error=None,
        )
        mock_facade["update_device_ttl"].return_value = mock_ttl_info

        response = client.put("/api/v1/paas/devices/device@1/ttl")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is True
        assert data["skipped"] is False

        # Verify facade called correctly
        mock_facade["update_device_ttl"].assert_called_once_with("device@1")

    def test_update_ttl_not_found(self, client, mock_facade):
        """Device not found returns 404."""
        facade_exception = DeviceFacadeException(
            operation="update_device_ttl",
            platform_type="ARCA",
            template_id=1,
            paas_device_id="device@1",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
        )
        mock_facade["update_device_ttl"].side_effect = facade_exception

        response = client.put("/api/v1/paas/devices/device@1/ttl")

        assert response.status_code == 404

    def test_update_ttl_not_implemented(self, client, mock_facade):
        """Platform without TTL support returns 501."""
        mock_facade["update_device_ttl"].side_effect = NotImplementedError(
            "SIGMA platform does not support TTL extension"
        )

        response = client.put("/api/v1/paas/devices/device@1/ttl")

        assert response.status_code == 501
        data = response.json()["detail"]
        assert data["error_code"] == "NOT_IMPLEMENTED"

    def test_update_ttl_unexpected_error(self, client, mock_facade):
        """Unexpected error during TTL update returns 500."""
        mock_facade["update_device_ttl"].side_effect = RuntimeError(
            "Unexpected internal error"
        )

        response = client.put("/api/v1/paas/devices/device@1/ttl")

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert data["detail"]["context"]["operation"] == "update_device_ttl"
        assert data["detail"]["context"]["paas_device_id"] == "device@1"

    def test_update_ttl_skipped(self, client, mock_facade):
        """TTL update skipped (device not expired) returns success with skipped=True."""
        mock_ttl_info = TTLInfo(
            paas_device_id="device@1",
            old_expiration_time=datetime.now(UTC) + timedelta(hours=12),
            new_expiration_time=datetime.now(UTC) + timedelta(hours=24),
            success=True,
            skipped=True,
            error=None,
        )
        mock_facade["update_device_ttl"].return_value = mock_ttl_info

        response = client.put("/api/v1/paas/devices/device@1/ttl")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is True
        assert data["skipped"] is True

    def test_update_ttl_with_error(self, client, mock_facade):
        """TTL update with partial error returns success=False."""
        mock_ttl_info = TTLInfo(
            paas_device_id="device@1",
            old_expiration_time=datetime.now(UTC) + timedelta(hours=1),
            new_expiration_time=None,
            success=False,
            skipped=False,
            error="Platform rate limited",
        )
        mock_facade["update_device_ttl"].return_value = mock_ttl_info

        response = client.put("/api/v1/paas/devices/device@1/ttl")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is False
        assert data["skipped"] is False
        assert data["error"] == "Platform rate limited"
        assert data["new_expiration_time"] is None


@pytest.mark.unit
class TestGetDeviceInfoEndpoint:
    """Test GET /api/v1/paas/devices/{paas_device_id}/info endpoint."""

    def test_get_device_info_success(self, client, mock_facade):
        """Successfully get device info."""
        from secbaas.community.api.device_manage import ArcaDeviceInfo

        mock_info = ArcaDeviceInfo(
            platform="arca",
            status="RUNNING",
            sandbox_id="sandbox-abc123",
            template_id="template-42",
            ip_address="10.0.0.1",
            ttl_seconds=3600,
            ttl_timestamp=None,
            created_at=datetime.now(UTC),
            name="test-device",
            description="A test device",
        )
        mock_facade["get_device_info"].return_value = mock_info

        response = client.get("/api/v1/paas/devices/sandbox-abc123@42/info")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["platform"] == "arca"
        assert data["status"] == "RUNNING"
        assert data["sandbox_id"] == "sandbox-abc123"
        assert data["template_id"] == "template-42"
        assert data["ip_address"] == "10.0.0.1"
        assert data["ttl_seconds"] == 3600
        assert data["name"] == "test-device"
        assert data["description"] == "A test device"
        assert "created_at" in data

        mock_facade["get_device_info"].assert_called_once_with(
            paas_device_id="sandbox-abc123@42"
        )

    def test_get_device_info_minimal_fields(self, client, mock_facade):
        """Device info with minimal DeviceInfo (base class) succeeds."""
        from secbaas.community.api.device_manage._device_info import DeviceInfo

        mock_info = DeviceInfo(
            platform="local",
            status="OFFLINE",
        )
        mock_facade["get_device_info"].return_value = mock_info

        response = client.get("/api/v1/paas/devices/local-dev/info")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["platform"] == "local"
        assert data["status"] == "OFFLINE"
        assert data["sandbox_id"] is None
        assert data["template_id"] is None
        assert data["ip_address"] is None

    def test_get_device_info_not_found(self, client, mock_facade):
        """Device not found returns 404."""
        facade_exception = DeviceFacadeException(
            operation="get_device_info",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-missing@42",
            original_error=PaasError(
                ErrorCode.DEVICE_NOT_FOUND, "Device does not exist"
            ),
        )
        mock_facade["get_device_info"].side_effect = facade_exception

        response = client.get("/api/v1/paas/devices/sandbox-missing@42/info")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "DEVICE_NOT_FOUND"
        assert data["detail"]["context"]["paas_device_id"] == "sandbox-missing@42"

    def test_get_device_info_facade_error(self, client, mock_facade):
        """Generic facade error returns appropriate status code."""
        facade_exception = DeviceFacadeException(
            operation="get_device_info",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-abc@42",
            original_error=PaasError(
                ErrorCode.DEVICE_UNAVAILABLE, "Device is unavailable"
            ),
        )
        mock_facade["get_device_info"].side_effect = facade_exception

        response = client.get("/api/v1/paas/devices/sandbox-abc@42/info")

        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["error_code"] == "DEVICE_UNAVAILABLE"

    def test_get_device_info_unexpected_error(self, client, mock_facade):
        """Unexpected error returns 500 with INTERNAL_ERROR format."""
        mock_facade["get_device_info"].side_effect = RuntimeError(
            "Unexpected database failure"
        )

        response = client.get("/api/v1/paas/devices/sandbox-abc@42/info")

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert data["detail"]["context"]["operation"] == "get_device_info"
        assert data["detail"]["context"]["paas_device_id"] == "sandbox-abc@42"


@pytest.mark.unit
class TestUpdateOutboundOperationRuleEndpoint:
    """Test PUT /api/v1/paas/devices/{id}/outbound-rule endpoint."""

    def test_update_outbound_rule_success(self, client, mock_facade):
        """Successfully update outbound operation rule."""
        mock_facade["update_outbound_operation_rule"].return_value = None

        response = client.put(
            "/api/v1/paas/devices/sandbox-abc@42/outbound-rule",
            json={"header_operation_rules": []},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["success"] is True
        assert (
            "outbound operation rule updated successfully"
            in data["data"]["message"].lower()
        )

        mock_facade["update_outbound_operation_rule"].assert_called_once()
        call_kwargs = mock_facade["update_outbound_operation_rule"].call_args.kwargs
        assert call_kwargs["paas_device_id"] == "sandbox-abc@42"
        assert call_kwargs["outbound_operation_rule"].header_operation_rules == []

    def test_update_outbound_rule_not_found(self, client, mock_facade):
        """Device not found returns 404."""
        facade_exception = DeviceFacadeException(
            operation="update_outbound_operation_rule",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-missing@42",
            original_error=PaasError(
                ErrorCode.DEVICE_NOT_FOUND, "Device does not exist"
            ),
        )
        mock_facade["update_outbound_operation_rule"].side_effect = facade_exception

        response = client.put(
            "/api/v1/paas/devices/sandbox-missing@42/outbound-rule",
            json={"header_operation_rules": []},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error_code"] == "DEVICE_NOT_FOUND"

    def test_update_outbound_rule_device_unavailable(self, client, mock_facade):
        """Device unavailable returns 503."""
        facade_exception = DeviceFacadeException(
            operation="update_outbound_operation_rule",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="sandbox-abc@42",
            original_error=PaasError(
                ErrorCode.DEVICE_UNAVAILABLE, "Device is not responding"
            ),
        )
        mock_facade["update_outbound_operation_rule"].side_effect = facade_exception

        response = client.put(
            "/api/v1/paas/devices/sandbox-abc@42/outbound-rule",
            json={"header_operation_rules": []},
        )

        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["error_code"] == "DEVICE_UNAVAILABLE"

    def test_update_outbound_rule_unexpected_error(self, client, mock_facade):
        """Unexpected error returns 500."""
        mock_facade["update_outbound_operation_rule"].side_effect = RuntimeError(
            "Internal error"
        )

        response = client.put(
            "/api/v1/paas/devices/sandbox-abc@42/outbound-rule",
            json={"header_operation_rules": []},
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert (
            data["detail"]["context"]["operation"] == "update_outbound_operation_rule"
        )
        assert data["detail"]["context"]["paas_device_id"] == "sandbox-abc@42"


@pytest.mark.unit
class TestInvokeHttpExtendedEndpoint:
    """Extended test cases for invoke-http endpoint covering uncovered paths."""

    def test_invoke_http_put_method(self, client, mock_facade):
        """PUT method is proxied correctly to internal service."""
        mock_response = {
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"updated": true}').decode(),
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.put(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/resource/1",
            json={"name": "updated"},
        )

        assert response.status_code == 200
        assert response.json() == {"updated": True}

        call_kwargs = mock_facade["invoke_http_in_device"].call_args.kwargs
        assert call_kwargs["method"] == "PUT"
        assert call_kwargs["port"] == 8080
        assert call_kwargs["path"] == "/api/resource/1"

    def test_invoke_http_delete_method(self, client, mock_facade):
        """DELETE method is proxied correctly to internal service."""
        mock_response = {
            "status_code": 204,
            "headers": {},
            "body": "",
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.delete(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/resource/1",
        )

        assert response.status_code == 204

        call_kwargs = mock_facade["invoke_http_in_device"].call_args.kwargs
        assert call_kwargs["method"] == "DELETE"

    def test_invoke_http_with_query_string(self, client, mock_facade):
        """Query string is forwarded to internal service."""
        mock_response = {
            "status_code": 200,
            "headers": {},
            "body": base64.b64encode(b'{"results": []}').decode(),
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/search?q=test&page=1",
        )

        assert response.status_code == 200
        call_kwargs = mock_facade["invoke_http_in_device"].call_args.kwargs
        assert "q=test&page=1" in call_kwargs["query_string"]

    def test_invoke_http_path_without_leading_slash(self, client, mock_facade):
        """Path without leading slash is normalized (subpath captures without slash)."""
        mock_response = {
            "status_code": 200,
            "headers": {},
            "body": base64.b64encode(b"ok").decode(),
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health",
        )

        assert response.status_code == 200
        call_kwargs = mock_facade["invoke_http_in_device"].call_args.kwargs
        # The path "api/health" (from {path:path}) should become "/api/health"
        assert call_kwargs["path"] == "/api/health"

    def test_invoke_http_base64_decode_error(self, client, mock_facade):
        """Invalid base64 in response body returns 500 error."""
        mock_response = {
            "status_code": 200,
            "headers": {},
            "body": "!!!not-valid-base64!!!",
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/badresponse",
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert "INVALID_RESPONSE" in data["detail"]["message"]

    def test_invoke_http_empty_body(self, client, mock_facade):
        """Empty base64 body in response returns empty bytes."""
        mock_response = {
            "status_code": 204,
            "headers": {},
            "body": "",
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/nocontent",
        )

        assert response.status_code == 204

    def test_invoke_http_not_implemented_error(self, client, mock_facade):
        """NotImplementedError from facade returns 501."""
        mock_facade["invoke_http_in_device"].side_effect = NotImplementedError(
            "LOCAL platform does not support HTTP invocation"
        )

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health",
        )

        assert response.status_code == 501
        data = response.json()
        assert data["detail"]["error"] == "NOT_IMPLEMENTED"

    def test_invoke_http_unexpected_exception(self, client, mock_facade):
        """Unexpected exception during invoke returns 500."""
        mock_facade["invoke_http_in_device"].side_effect = RuntimeError(
            "Unexpected proxy error"
        )

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health",
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert data["detail"]["context"]["operation"] == "invoke_http_in_device"

    def test_invoke_http_hop_by_hop_headers_filtered(self, client, mock_facade):
        """Hop-by-hop headers are filtered from the forwarded request."""
        mock_response = {
            "status_code": 200,
            "headers": {"Content-Type": "text/plain", "Transfer-Encoding": "chunked"},
            "body": base64.b64encode(b"ok").decode(),
        }
        mock_facade["invoke_http_in_device"].return_value = mock_response

        response = client.get(
            "/api/v1/paas/devices/device@1/invoke-http/8080/api/health",
            headers={
                "Connection": "keep-alive",
                "Keep-Alive": "timeout=5",
                "X-Custom": "test-value",
                "Host": "evil.example.com",
            },
        )

        assert response.status_code == 200
        call_kwargs = mock_facade["invoke_http_in_device"].call_args.kwargs

        # Hop-by-hop headers should be filtered
        forwarded_headers = {k.lower(): v for k, v in call_kwargs["headers"].items()}
        assert "connection" not in forwarded_headers
        assert "keep-alive" not in forwarded_headers
        assert "host" not in forwarded_headers
        # Custom headers should pass through
        assert forwarded_headers.get("x-custom") == "test-value"


@pytest.mark.unit
class TestGenericExceptionHandlers:
    """Test generic Exception handlers that were previously uncovered."""

    def test_destroy_device_unexpected_error(self, client, mock_facade):
        """Unexpected error during device destruction returns 500."""
        mock_facade["destroy_device"].side_effect = RuntimeError(
            "Unexpected system error"
        )

        response = client.delete("/api/v1/paas/devices/sandbox-abc@42")

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert data["detail"]["context"]["operation"] == "destroy_device"
        assert data["detail"]["context"]["paas_device_id"] == "sandbox-abc@42"

    def test_execute_command_unexpected_error(self, client, mock_facade):
        """Unexpected error during command execution returns 500."""
        mock_facade["execute_command"].side_effect = RuntimeError(
            "Unexpected system error"
        )

        request_data = {"cmd": "ls"}
        response = client.post(
            "/api/v1/paas/devices/sandbox-abc@42/commands",
            json=request_data,
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "INTERNAL_ERROR"
        assert data["detail"]["context"]["operation"] == "execute_command"
        assert data["detail"]["context"]["paas_device_id"] == "sandbox-abc@42"

    def test_get_ws_connection_info_facade_error(self, client, mock_facade):
        """DeviceFacadeException in ws-info (with original_error) returns 500."""
        facade_exception = DeviceFacadeException(
            operation="resolve_ws_conn_info",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="ARCA-123@42",
            original_error=PaasError(
                ErrorCode.COMMAND_FAILED, "Token generation failed"
            ),
        )
        mock_facade["resolve_ws_conn_info"].side_effect = facade_exception

        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=8080&path=/api/openclaw/ws"
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error"] == "COMMAND_FAILED"

    def test_get_ws_connection_info_general_facade_error(self, client, mock_facade):
        """General DeviceFacadeException in ws-info returns 500 with error code."""
        facade_exception = DeviceFacadeException(
            operation="resolve_ws_conn_info",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="ARCA-123@42",
            original_error=PaasError(ErrorCode.RATE_LIMITED, "Too many requests"),
        )
        mock_facade["resolve_ws_conn_info"].side_effect = facade_exception

        response = client.get(
            "/api/v1/paas/devices/ARCA-123@42/ws-info?port=8080&path=/api/openclaw/ws"
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error"] == "RATE_LIMITED"
