"""Tests for LocalPaasService with mocked dependencies.

Covers:
- Same-instance routing path (direct WebSocket via ConnectionManager)
- Cross-instance routing path (HTTP forwarding via InstanceRouter)
- Error scenarios from mng daemon
- Timeout handling

All tests use mocked dependencies to isolate the service under test.
"""

import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.device_manage import (
    DeviceCredentials,
    LocalCreateConfig,
    LocalCreationResult,
    LocalCredentials,
)
from secbaas.community.core.service.paas import (
    DeviceCreationError,
    LocalPaasService,
)
from secbaas.community.core.service.paas._local_paas_service import _normalize_message
from secbaas.community.core.service.paas.desktop.instance_router._exceptions import (
    ForwardHTTPError,
)


@pytest.fixture
def local_credentials():
    """Create test local credentials."""
    return LocalCredentials(
        template_id=1,
        template_uuid="tpl-local-001",
        tenant_name="test-tenant",
    )


@pytest.fixture
def local_create_config():
    """Create test local create config with all optional fields."""
    return LocalCreateConfig(
        user_id="user-001",
        machine_id="machine-001",
        tc_bot_id="bot-001",
        agent_code="agent-001",
        name="test-device",
        description="Test device",
        envs={"KEY": "value"},
    )


@pytest.fixture
def mock_repository():
    """Create a mock LocalUserMachineRepository."""
    mock = MagicMock()
    return mock


@pytest.fixture
def mock_connection_manager():
    """Create a mock ConnectionManager with async send_command."""
    # Use AsyncMock with MagicMock mixed in for call assertions
    mock = MagicMock()
    mock.send_command = AsyncMock()
    mock.send_command.return_value = {"status": "success", "data": {}}
    # is_connected is SYNC method (not async)
    mock.is_connected = MagicMock(return_value=True)
    # WR-01: production code now reads REQUEST_ID_DELIMITER from the
    # ConnectionManager instance instead of hardcoding "|". Match the
    # real class-level constant so request_id-format assertions still
    # see the canonical delimiter.
    mock.REQUEST_ID_DELIMITER = "|"
    return mock


@pytest.fixture
def mock_instance_router():
    """Create a mock InstanceRouter with async route_to_instance."""
    mock = MagicMock()
    mock.route_to_instance = AsyncMock()
    return mock


@pytest.fixture
def mock_device_template_repository():
    """Create a mock DeviceTemplateRepository."""
    mock = MagicMock()
    mock.get_default_local_template_id.return_value = 42  # Default template_id
    return mock


@pytest.fixture
def mock_desktop_sandbox_plugin():
    """Create a mock DesktopSandboxPlugin with resolve_ws_conn_info stub."""
    from datetime import UTC, datetime, timedelta

    from secbaas.community.api.bot_runtime._ws_connection_info import WsConnectionInfo

    mock = MagicMock()
    mock.resolve_ws_conn_info.return_value = WsConnectionInfo(
        ws_url="wss://agentclawproxy-dev.service.test/wsrelay/test-session",
        token="mock-jwt-token",
        target="LOCAL_ctr--mach--user@1:8080:test-session",
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    return mock


@pytest.fixture
def mock_relay_repository():
    """Create a mock WsRelaySessionRepository for relay init row insertion."""
    mock = MagicMock()
    mock.insert_init = MagicMock()
    return mock


@pytest.fixture
def local_paas_service(
    local_credentials,
    mock_repository,
    mock_connection_manager,
    mock_instance_router,
    mock_device_template_repository,
    mock_desktop_sandbox_plugin,
):
    """Create a LocalPaasService instance with mocked dependencies."""
    return LocalPaasService(
        credentials=local_credentials,
        repository=mock_repository,
        connection_manager=mock_connection_manager,
        instance_router=mock_instance_router,
        server_ip="test-instance",
        desktop_sandbox_plugin=mock_desktop_sandbox_plugin,
        env="test",
        device_template_repository=mock_device_template_repository,
    )


@pytest.fixture
def local_paas_service_with_relay(
    local_credentials,
    mock_repository,
    mock_connection_manager,
    mock_instance_router,
    mock_device_template_repository,
    mock_desktop_sandbox_plugin,
    mock_relay_repository,
):
    """Create a LocalPaasService instance with relay_repository injected."""
    return LocalPaasService(
        credentials=local_credentials,
        repository=mock_repository,
        connection_manager=mock_connection_manager,
        instance_router=mock_instance_router,
        server_ip="test-instance",
        desktop_sandbox_plugin=mock_desktop_sandbox_plugin,
        env="test",
        device_template_repository=mock_device_template_repository,
        relay_repository=mock_relay_repository,
    )


class TestCreateDeviceSameInstance:
    """Test same-instance routing path via ConnectionManager."""

    @pytest.mark.asyncio
    async def test_create_device_same_instance_success(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """Successful device creation on same instance returns LocalCreationResult."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123", "container_name": "my-container"},
        }

        # Execute
        result = await local_paas_service.create_device(local_create_config)

        # Assert: Result is LocalCreationResult with correct fields
        assert isinstance(result, LocalCreationResult)
        assert result.container_id == "abc123--machine-001--user-001"
        assert result.platform == "local"
        assert result.status == "RUNNING"

        # Assert: send_command called with correct params
        mock_connection_manager.send_command.assert_called_once()
        call_args = mock_connection_manager.send_command.call_args
        assert call_args[0][0] == "machine-001"  # machine_id
        command = call_args[0][1]
        assert command["action"] == "create_device"
        assert command["params"]["tc_bot_id"] == "bot-001"
        assert command["params"]["agent_code"] == "agent-001"
        assert "credentials" in command["params"]
        assert command["params"]["credentials"] is None

    @pytest.mark.asyncio
    async def test_create_device_same_instance_minimal_response(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """Success response with minimal data (only container_id)."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success with minimal data
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        # Execute
        result = await local_paas_service.create_device(local_create_config)

        # Assert: Result has only container_id
        assert isinstance(result, LocalCreationResult)
        assert result.container_id == "abc123--machine-001--user-001"
        assert result.platform == "local"
        assert result.status == "RUNNING"

    @pytest.mark.asyncio
    async def test_create_device_same_instance_connection_error(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """ConnectionError from ConnectionManager propagates."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager raises ConnectionError
        mock_connection_manager.send_command.side_effect = ConnectionError(
            "Machine not connected"
        )

        # Execute & Assert: ConnectionError propagates
        with pytest.raises(ConnectionError, match="Machine not connected"):
            await local_paas_service.create_device(local_create_config)

    @pytest.mark.asyncio
    async def test_create_device_same_instance_timeout(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """TimeoutError from ConnectionManager propagates."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager raises TimeoutError
        mock_connection_manager.send_command.side_effect = TimeoutError(
            "Command timeout"
        )

        # Execute & Assert: TimeoutError propagates
        with pytest.raises(TimeoutError, match="Command timeout"):
            await local_paas_service.create_device(local_create_config)

    @pytest.mark.asyncio
    async def test_send_command_called_with_correct_params(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """Verify command params contain all required fields."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        # Execute
        await local_paas_service.create_device(local_create_config)

        # Assert: Verify command structure
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        params = command["params"]

        assert params["name"] == "test-device"
        assert params["description"] == "Test device"
        assert params["user_id"] == "user-001"
        assert params["machine_id"] == "machine-001"
        assert params["tc_bot_id"] == "bot-001"
        assert params["agent_code"] == "agent-001"
        assert params["envs"] == {"KEY": "value"}

    @pytest.mark.asyncio
    async def test_create_device_same_instance_with_credentials(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
    ):
        """Credentials with populated fields appear uppercase in command params."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            name="test-device",
            description="Test device",
            envs={"KEY": "value"},
            credentials=DeviceCredentials(token="tok", client_id="cid"),
        )

        result = await local_paas_service.create_device(config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["credentials"] == {
            "TOKEN": "tok",
            "CLIENT_ID": "cid",
        }

    @pytest.mark.asyncio
    async def test_create_device_same_instance_credentials_all_none(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
    ):
        """All-None DeviceCredentials produces None in command params."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            name="test-device",
            description="Test device",
            envs={"KEY": "value"},
            credentials=DeviceCredentials(),
        )

        result = await local_paas_service.create_device(config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["credentials"] is None


class TestCreateDeviceCrossInstance:
    """Test cross-instance routing path via InstanceRouter."""

    @pytest.mark.asyncio
    async def test_create_device_cross_instance_success(
        self,
        local_paas_service,
        mock_repository,
        mock_instance_router,
        local_create_config,
    ):
        """Successful device creation on different instance via InstanceRouter."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns success
        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"container_id": "xyz789", "container_name": "remote-container"},
        }

        # Execute
        result = await local_paas_service.create_device(local_create_config)

        # Assert: Result is LocalCreationResult with correct fields
        assert isinstance(result, LocalCreationResult)
        assert result.container_id == "xyz789--machine-001--user-001"
        assert result.platform == "local"
        assert result.status == "RUNNING"

        # Assert: route_to_instance called with correct params
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "create_device"
        assert call_args.kwargs["params"]["tc_bot_id"] == "bot-001"
        assert call_args.kwargs["params"]["agent_code"] == "agent-001"

    @pytest.mark.asyncio
    async def test_create_device_cross_instance_request_id_format(
        self,
        local_paas_service,
        mock_repository,
        mock_instance_router,
        local_create_config,
    ):
        """Verify request_id format is machine_id|uuid."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns success
        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"container_id": "xyz789"},
        }

        # Execute
        await local_paas_service.create_device(local_create_config)

        # Assert: Capture and verify request_id format
        call_args = mock_instance_router.route_to_instance.call_args
        request_id = call_args.kwargs["request_id"]

        # Verify format: machine_id|uuid_hex
        assert request_id.startswith("machine-001|")
        uuid_part = request_id.split("|")[1]
        assert len(uuid_part) == 32  # UUID hex is 32 chars
        # Verify it's valid hex
        int(uuid_part, 16)

    @pytest.mark.asyncio
    async def test_create_device_cross_instance_forward_http_error(
        self,
        local_paas_service,
        mock_repository,
        mock_instance_router,
        local_create_config,
    ):
        """ForwardHTTPError from InstanceRouter propagates."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter raises ForwardHTTPError
        mock_instance_router.route_to_instance.side_effect = ForwardHTTPError(
            target_instance="other-instance",
            status_code=503,
            response_body="Service Unavailable",
        )

        # Execute & Assert: ForwardHTTPError propagates
        with pytest.raises(ForwardHTTPError):
            await local_paas_service.create_device(local_create_config)

    @pytest.mark.asyncio
    async def test_create_device_cross_instance_with_credentials(
        self,
        local_paas_service,
        mock_repository,
        mock_instance_router,
    ):
        """Cross-instance: populated credentials appear uppercase in params."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"container_id": "xyz789"},
        }

        config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            name="test-device",
            description="Test device",
            envs={"KEY": "value"},
            credentials=DeviceCredentials(token="tok", client_id="cid"),
        )

        result = await local_paas_service.create_device(config)

        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["params"]["credentials"] == {
            "TOKEN": "tok",
            "CLIENT_ID": "cid",
        }

    @pytest.mark.asyncio
    async def test_create_device_cross_instance_credentials_all_none(
        self,
        local_paas_service,
        mock_repository,
        mock_instance_router,
    ):
        """Cross-instance: all-None DeviceCredentials produces None in params."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"container_id": "xyz789"},
        }

        config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            name="test-device",
            description="Test device",
            envs={"KEY": "value"},
            credentials=DeviceCredentials(),
        )

        result = await local_paas_service.create_device(config)

        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["params"]["credentials"] is None


class TestCreateDeviceErrors:
    """Test mng daemon error responses raise DeviceCreationError."""

    @pytest.mark.asyncio
    async def test_create_device_container_limit_exceeded(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """CONTAINER_LIMIT_EXCEEDED error raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "CONTAINER_LIMIT_EXCEEDED",
            "message": "Maximum container limit reached",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "CONTAINER_LIMIT_EXCEEDED"
        assert "Maximum container limit reached" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_create_device_image_not_found(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """IMAGE_NOT_FOUND error raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "IMAGE_NOT_FOUND",
            "message": "Docker image not available",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "IMAGE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_create_device_resource_exhausted(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """RESOURCE_EXHAUSTED error raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "RESOURCE_EXHAUSTED",
            "message": "Insufficient CPU",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "RESOURCE_EXHAUSTED"

    @pytest.mark.asyncio
    async def test_create_device_creation_failed(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """CREATION_FAILED error raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "CREATION_FAILED",
            "message": "Generic creation failure",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "CREATION_FAILED"

    @pytest.mark.asyncio
    async def test_create_device_error_default_message(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """Error without message field uses default message."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error without message
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "UNKNOWN_ERROR",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "UNKNOWN_ERROR"
        assert exc_info.value.message == "Device creation failed"

    @pytest.mark.asyncio
    async def test_create_device_error_same_instance_path(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """Error handling works with same-instance path (ConnectionManager)."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "CONTAINER_LIMIT_EXCEEDED",
            "message": "Limit reached",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "CONTAINER_LIMIT_EXCEEDED"

    @pytest.mark.asyncio
    async def test_create_device_error_cross_instance_path(
        self,
        local_paas_service,
        mock_repository,
        mock_instance_router,
        local_create_config,
    ):
        """Error handling works with cross-instance path (InstanceRouter)."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns error
        mock_instance_router.route_to_instance.return_value = {
            "status": "error",
            "error": "IMAGE_NOT_FOUND",
            "message": "Image missing on remote",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "IMAGE_NOT_FOUND"
        assert "Image missing on remote" in exc_info.value.message


class TestCreateDeviceWithCredentials:
    """Tests for credentials serialization in create_device() WS command params."""

    @pytest.mark.asyncio
    async def test_credentials_serialized_in_command_params(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """When config.credentials is populated, command params include uppercase keys."""

        local_create_config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            credentials=DeviceCredentials(token="tok", client_id="cid"),
        )
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        await local_paas_service.create_device(local_create_config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert "credentials" in command["params"]
        credentials_dump = command["params"]["credentials"]
        assert credentials_dump == {"TOKEN": "tok", "CLIENT_ID": "cid"}

    @pytest.mark.asyncio
    async def test_credentials_is_none_when_not_set(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """When config.credentials is None (default), credentials is None in params."""
        local_create_config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
        )
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        await local_paas_service.create_device(local_create_config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert "credentials" in command["params"]
        assert command["params"]["credentials"] is None

    @pytest.mark.asyncio
    async def test_credentials_is_none_when_all_fields_are_none(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """When config.credentials is DeviceCredentials() (all-None), params is None."""

        local_create_config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            credentials=DeviceCredentials(),  # all-None
        )
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        await local_paas_service.create_device(local_create_config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert "credentials" in command["params"]
        assert command["params"]["credentials"] is None

    @pytest.mark.asyncio
    async def test_credentials_partial_fields_only_non_none(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Only non-None fields appear in credentials dict (exclude_none behavior)."""

        local_create_config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            credentials=DeviceCredentials(token="tok"),  # only token
        )
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        await local_paas_service.create_device(local_create_config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert "credentials" in command["params"]
        assert command["params"]["credentials"] == {"TOKEN": "tok"}

    @pytest.mark.asyncio
    async def test_existing_command_params_unchanged(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Existing command params (name, tc_bot_id, etc.) are unchanged by new field."""

        local_create_config = LocalCreateConfig(
            user_id="user-001",
            machine_id="machine-001",
            tc_bot_id="bot-001",
            agent_code="agent-001",
            name="test-device",
            description="Test device",
            envs={"KEY": "value"},
            credentials=DeviceCredentials(token="tok"),
        )
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"container_id": "abc123"},
        }

        await local_paas_service.create_device(local_create_config)

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["name"] == "test-device"
        assert command["params"]["description"] == "Test device"
        assert command["params"]["user_id"] == "user-001"
        assert command["params"]["machine_id"] == "machine-001"
        assert command["params"]["tc_bot_id"] == "bot-001"
        assert command["params"]["agent_code"] == "agent-001"
        assert command["params"]["envs"] == {"KEY": "value"}


class TestDestroyDeviceSameInstance:
    """Test destroy_device same-instance routing via ConnectionManager."""

    @pytest.mark.asyncio
    async def test_destroy_device_same_instance_success(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Successful device destruction on same instance returns True."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {"status": "success"}

        # Execute
        result = await local_paas_service.destroy_device(
            "container--machine-001--user-001"
        )

        # Assert: Result is True
        assert result is True

        # Assert: send_command called with correct params
        mock_connection_manager.send_command.assert_called_once()
        call_args = mock_connection_manager.send_command.call_args
        assert call_args[0][0] == "machine-001"
        command = call_args[0][1]
        assert command["action"] == "destroy_device"
        assert command["params"]["container_id"] == "container"

    @pytest.mark.asyncio
    async def test_destroy_device_connection_error_propagates(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """ConnectionError from ConnectionManager propagates."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager raises ConnectionError
        mock_connection_manager.send_command.side_effect = ConnectionError(
            "Machine not connected"
        )

        # Execute & Assert
        with pytest.raises(ConnectionError, match="Machine not connected"):
            await local_paas_service.destroy_device("container--machine-001--user-001")

    @pytest.mark.asyncio
    async def test_destroy_device_timeout_propagates(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """TimeoutError from ConnectionManager propagates."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager raises TimeoutError
        mock_connection_manager.send_command.side_effect = TimeoutError(
            "Command timeout"
        )

        # Execute & Assert
        with pytest.raises(TimeoutError, match="Command timeout"):
            await local_paas_service.destroy_device("container--machine-001--user-001")


class TestDestroyDeviceCrossInstance:
    """Test destroy_device cross-instance routing via InstanceRouter."""

    @pytest.mark.asyncio
    async def test_destroy_device_cross_instance_success(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Successful device destruction on different instance via InstanceRouter."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns success
        mock_instance_router.route_to_instance.return_value = {"status": "success"}

        # Execute
        result = await local_paas_service.destroy_device(
            "container--machine-001--user-001"
        )

        # Assert: Result is True
        assert result is True

        # Assert: route_to_instance called with correct params
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "destroy_device"
        assert call_args.kwargs["params"]["container_id"] == "container"
        # Verify request_id format
        request_id = call_args.kwargs["request_id"]
        assert request_id.startswith("machine-001|")
        uuid_part = request_id.split("|")[1]
        assert len(uuid_part) == 32

    @pytest.mark.asyncio
    async def test_destroy_device_forward_http_error_propagates(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """ForwardHTTPError from InstanceRouter propagates."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter raises ForwardHTTPError
        mock_instance_router.route_to_instance.side_effect = ForwardHTTPError(
            target_instance="other-instance",
            status_code=503,
            response_body="Service Unavailable",
        )

        # Execute & Assert
        with pytest.raises(ForwardHTTPError):
            await local_paas_service.destroy_device("container--machine-001--user-001")


class TestDestroyDeviceErrors:
    """Test destroy_device error scenarios."""

    @pytest.mark.asyncio
    async def test_destroy_device_container_not_found_idempotent(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """CONTAINER_NOT_FOUND returns True (idempotent) not exception."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns CONTAINER_NOT_FOUND error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "CONTAINER_NOT_FOUND",
            "message": "Container not found",
        }

        # Execute
        result = await local_paas_service.destroy_device(
            "container--machine-001--user-001"
        )

        # Assert: Result is True (idempotent), not exception
        assert result is True

    @pytest.mark.asyncio
    async def test_destroy_device_machine_not_found(
        self, local_paas_service, mock_repository
    ):
        """MACHINE_NOT_FOUND raised when repository returns None."""
        # Setup: Repository returns None
        mock_repository.get_by_machine_id.return_value = None

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.destroy_device("container--machine-001--user-001")

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_destroy_device_destroy_failed_error(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """DESTROY_FAILED error raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns DESTROY_FAILED error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "DESTROY_FAILED",
            "message": "Destroy failed",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.destroy_device("container--machine-001--user-001")

        assert exc_info.value.error_code == "DESTROY_FAILED"

    @pytest.mark.asyncio
    async def test_destroy_device_error_cross_instance(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Error from InstanceRouter raises DeviceCreationError."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns error
        mock_instance_router.route_to_instance.return_value = {
            "status": "error",
            "error": "DESTROY_FAILED",
            "message": "Remote destroy failed",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.destroy_device("container--machine-001--user-001")

        assert exc_info.value.error_code == "DESTROY_FAILED"


class TestExecuteCommandSameInstance:
    """Test execute_command same-instance routing via ConnectionManager."""

    @pytest.mark.asyncio
    async def test_execute_command_success(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Successful command execution returns CommandResult with all fields."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success with execution data
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {
                "exit_code": 0,
                "stdout": "hello output",
                "stderr": "",
                "execution_time_ms": 100,
            },
        }

        # Execute
        result = await local_paas_service.execute_command(
            "container--machine-001--user-001", "echo hello"
        )

        # Assert: Result is CommandResult with correct fields
        from secbaas.community.api.device_manage import CommandResult

        assert isinstance(result, CommandResult)
        assert result.exit_code == 0
        assert result.stdout == "hello output"
        assert result.stderr == ""
        assert result.execution_time_ms == 100
        assert result.command == "echo hello"

    @pytest.mark.asyncio
    async def test_execute_command_non_zero_exit_returns_normally(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Non-zero exit_code returns CommandResult (not exception)."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns non-zero exit
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "error message",
                "execution_time_ms": 50,
            },
        }

        # Execute (should NOT raise exception)
        result = await local_paas_service.execute_command(
            "container--machine-001--user-001", "false"
        )

        # Assert: Result has non-zero exit_code but no exception
        assert result.exit_code == 1
        assert result.stderr == "error message"

    @pytest.mark.asyncio
    async def test_execute_command_timeout_clamping(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Timeout seconds > 30 are clamped to 30."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"exit_code": 0, "stdout": "", "stderr": ""},
        }

        # Execute with timeout=60 (should be clamped to 30)
        await local_paas_service.execute_command(
            "container--machine-001--user-001", "cmd", timeout_seconds=60
        )

        # Assert: Verify clamping in params
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["timeout_seconds"] == 30

    @pytest.mark.asyncio
    async def test_execute_command_timeout_under_max(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Timeout seconds <= 30 are used as-is."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"exit_code": 0, "stdout": "", "stderr": ""},
        }

        # Execute with timeout=10 (should stay 10)
        await local_paas_service.execute_command(
            "container--machine-001--user-001", "cmd", timeout_seconds=10
        )

        # Assert: Verify no clamping for under-max value
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["timeout_seconds"] == 10

    @pytest.mark.asyncio
    async def test_execute_command_with_env(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Environment variables are passed in command params."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"exit_code": 0, "stdout": "", "stderr": ""},
        }

        # Execute with custom env
        await local_paas_service.execute_command(
            "container--machine-001--user-001", "cmd", env={"KEY": "value"}
        )

        # Assert: Verify env in params
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["env"] == {"KEY": "value"}

    @pytest.mark.asyncio
    async def test_execute_command_env_none_defaults_empty(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """None env defaults to empty dictionary."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns success
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"exit_code": 0, "stdout": "", "stderr": ""},
        }

        # Execute with env=None
        await local_paas_service.execute_command(
            "container--machine-001--user-001", "cmd", env=None
        )

        # Assert: Verify env is empty dict
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["env"] == {}

    @pytest.mark.asyncio
    async def test_execute_command_connection_error_propagates(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """ConnectionError from ConnectionManager propagates."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager raises ConnectionError
        mock_connection_manager.send_command.side_effect = ConnectionError(
            "Machine not connected"
        )

        # Execute & Assert
        with pytest.raises(ConnectionError, match="Machine not connected"):
            await local_paas_service.execute_command(
                "container--machine-001--user-001", "cmd"
            )


class TestExecuteCommandCrossInstance:
    """Test execute_command cross-instance routing via InstanceRouter."""

    @pytest.mark.asyncio
    async def test_execute_command_cross_instance(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Command execution on different instance via InstanceRouter."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns success
        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {
                "exit_code": 0,
                "stdout": "remote output",
                "stderr": "",
                "execution_time_ms": 200,
            },
        }

        # Execute
        result = await local_paas_service.execute_command(
            "container--machine-001--user-001", "echo remote"
        )

        # Assert
        assert result.exit_code == 0
        assert result.stdout == "remote output"

        # Assert: route_to_instance called with correct params
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "exec_shell"
        assert call_args.kwargs["params"]["cmd"] == "echo remote"
        assert call_args.kwargs["params"]["container_id"] == "container"


class TestExecuteCommandErrors:
    """Test execute_command error scenarios."""

    @pytest.mark.asyncio
    async def test_execute_command_command_failed_error(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """COMMAND_FAILED error from mng raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "COMMAND_FAILED",
            "message": "Command failed",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.execute_command(
                "container--machine-001--user-001", "cmd"
            )

        assert exc_info.value.error_code == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_execute_command_timeout_error(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """TIMEOUT error from mng raises DeviceCreationError with TIMEOUT code."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns TIMEOUT error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "TIMEOUT",
            "message": "Command timed out",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.execute_command(
                "container--machine-001--user-001", "cmd"
            )

        assert exc_info.value.error_code == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_execute_command_container_not_running(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """CONTAINER_NOT_RUNNING error raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "CONTAINER_NOT_RUNNING",
            "message": "Container not running",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.execute_command(
                "container--machine-001--user-001", "cmd"
            )

        assert exc_info.value.error_code == "CONTAINER_NOT_RUNNING"


class TestGetDeviceInfoSameInstance:
    """Test get_device_info same-instance routing via ConnectionManager."""

    @pytest.mark.asyncio
    async def test_get_device_info_running(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng returns RUNNING status -> LocalDeviceInfo with status=RUNNING."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns RUNNING status
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"status": "RUNNING"},
        }

        # Execute
        result = await local_paas_service.get_device_info(
            "container--machine-001--user-001"
        )

        # Assert: Result is LocalDeviceInfo with correct status
        from secbaas.community.api.device_manage import LocalDeviceInfo

        assert isinstance(result, LocalDeviceInfo)
        assert result.status == "RUNNING"
        assert result.container_id == "container"
        assert result.machine_id == "machine-001"
        assert result.user_id == "user-001"
        assert result.platform == "local"

    @pytest.mark.asyncio
    async def test_get_device_info_stopped(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng returns STOPPED status -> LocalDeviceInfo with status=STOPPED."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns STOPPED status
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"status": "STOPPED"},
        }

        # Execute
        result = await local_paas_service.get_device_info(
            "container--machine-001--user-001"
        )

        # Assert
        assert result.status == "STOPPED"

    @pytest.mark.asyncio
    async def test_get_device_info_unknown_status(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng returns empty data -> LocalDeviceInfo with status=UNKNOWN."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns empty data
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        # Execute
        result = await local_paas_service.get_device_info(
            "container--machine-001--user-001"
        )

        # Assert
        assert result.status == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_get_device_info_connection_error_propagates(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """ConnectionError from ConnectionManager is wrapped as DeviceCreationError.

        Per WR-05 fix: get_device_info now wraps ConnectionError into
        DeviceCreationError(MACHINE_OFFLINE) for consistency with
        get_machine_info and get_machine_res_dirs.
        """
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager raises ConnectionError
        mock_connection_manager.send_command.side_effect = ConnectionError(
            "Machine not connected"
        )

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_device_info("container--machine-001--user-001")
        assert exc_info.value.error_code == "MACHINE_OFFLINE"
        assert "machine-001" in exc_info.value.message


class TestGetDeviceInfoCrossInstance:
    """Test get_device_info cross-instance routing via InstanceRouter."""

    @pytest.mark.asyncio
    async def test_get_device_info_cross_instance(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Device info query on different instance via InstanceRouter."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns success
        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"status": "RUNNING"},
        }

        # Execute
        result = await local_paas_service.get_device_info(
            "container--machine-001--user-001"
        )

        # Assert
        from secbaas.community.api.device_manage import LocalDeviceInfo

        assert isinstance(result, LocalDeviceInfo)
        assert result.status == "RUNNING"

        # Assert: route_to_instance called with correct params
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "get_device_info"
        assert call_args.kwargs["params"]["container_id"] == "container"
        # Verify request_id format
        request_id = call_args.kwargs["request_id"]
        assert request_id.startswith("machine-001|")
        uuid_part = request_id.split("|")[1]
        assert len(uuid_part) == 32


class TestGetDeviceInfoErrors:
    """Test get_device_info error scenarios."""

    @pytest.mark.asyncio
    async def test_get_device_info_device_not_found(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """DEVICE_NOT_FOUND error from mng raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "DEVICE_NOT_FOUND",
            "message": "Device not found",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_device_info("container--machine-001--user-001")

        assert exc_info.value.error_code == "DEVICE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_device_info_query_failed(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """QUERY_FAILED error from mng raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "QUERY_FAILED",
            "message": "Query failed",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_device_info("container--machine-001--user-001")

        assert exc_info.value.error_code == "QUERY_FAILED"

    @pytest.mark.asyncio
    async def test_get_device_info_machine_not_found(
        self, local_paas_service, mock_repository
    ):
        """Repository returns None -> raises DeviceCreationError MACHINE_NOT_FOUND."""
        # Setup: Repository returns None
        mock_repository.get_by_machine_id.return_value = None

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_device_info("container--machine-001--user-001")

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"


class TestGetMachineInfoSameInstance:
    """Test get_machine_info same-instance routing via ConnectionManager."""

    @pytest.mark.asyncio
    async def test_get_machine_info_success(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng returns cpu/memory/disk data -> returns dict with these fields."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns machine resources
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"cpu_cores": 8, "memory_gb": 32, "disk_gb": 500},
        }

        # Execute
        result = await local_paas_service.get_machine_info("machine-001")

        # Assert
        assert result == {"cpu_cores": 8, "memory_gb": 32, "disk_gb": 500}

        # Assert: send_command called with correct params
        mock_connection_manager.send_command.assert_called_once()
        call_args = mock_connection_manager.send_command.call_args
        assert call_args[0][0] == "machine-001"
        command = call_args[0][1]
        assert command["action"] == "get_machine_info"
        assert command["params"]["machine_id"] == "machine-001"

    @pytest.mark.asyncio
    async def test_get_machine_info_empty_data(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng returns empty data -> returns empty dict."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns empty data
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        # Execute
        result = await local_paas_service.get_machine_info("machine-001")

        # Assert
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_machine_info_websocket_not_connected(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """TOCTOU: DB shows ONLINE but WebSocket disconnected (fast-fail path)."""
        # Setup: Repository returns same instance, status ONLINE
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_record.last_heartbeat = None
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager reports not connected (WebSocket race condition)
        mock_connection_manager.is_connected.return_value = False

        # Execute & Assert: Should raise DeviceCreationError at routing layer
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "MACHINE_NOT_CONNECTED"
        assert "WebSocket not connected" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_get_machine_info_status_offline_fast_fail(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """TOCTOU: DB re-query shows OFFLINE - fast-fail before WebSocket check."""
        # First call (outer get_machine_info query): returns ONLINE
        # Second call (_route_command re-query): returns OFFLINE (simulated race)
        online_record = MagicMock()
        online_record.connected_server_instance = "test-instance"
        online_record.status = "ONLINE"
        online_record.last_heartbeat = None

        offline_record = MagicMock()
        offline_record.connected_server_instance = ""  # Cleared on disconnect
        offline_record.status = "OFFLINE"
        offline_record.last_heartbeat = None

        # Setup repository to return OFFLINE on re-query
        mock_repository.get_by_machine_id.return_value = offline_record

        # Execute & Assert: Should fail fast at DB check, never reach WebSocket
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "MACHINE_OFFLINE"
        assert "is OFFLINE" in exc_info.value.message
        # Verify is_connected was never called (fast-fail path)
        mock_connection_manager.is_connected.assert_not_called()


class TestGetMachineInfoCrossInstance:
    """Test get_machine_info cross-instance routing via InstanceRouter."""

    @pytest.mark.asyncio
    async def test_get_machine_info_cross_instance(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Machine info query on different instance via InstanceRouter."""
        # Setup: Repository returns different instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: InstanceRouter returns success
        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"cpu_cores": 16},
        }

        # Execute
        result = await local_paas_service.get_machine_info("machine-001")

        # Assert
        assert result == {"cpu_cores": 16}

        # Assert: route_to_instance called with correct params
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "get_machine_info"
        assert call_args.kwargs["params"]["machine_id"] == "machine-001"
        # Verify request_id format
        request_id = call_args.kwargs["request_id"]
        assert request_id.startswith("machine-001|")
        uuid_part = request_id.split("|")[1]
        assert len(uuid_part) == 32


class TestGetMachineInfoErrors:
    """Test get_machine_info error scenarios."""

    @pytest.mark.asyncio
    async def test_get_machine_info_machine_not_found(
        self, local_paas_service, mock_repository
    ):
        """Repository returns None -> raises DeviceCreationError MACHINE_NOT_FOUND."""
        # Setup: Repository returns None
        mock_repository.get_by_machine_id.return_value = None

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_machine_info_query_failed(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """QUERY_FAILED error from mng raises DeviceCreationError."""
        # Setup: Repository returns same instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Setup: ConnectionManager returns error
        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "QUERY_FAILED",
            "message": "Query failed",
        }

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "QUERY_FAILED"

    @pytest.mark.asyncio
    async def test_get_machine_info_empty_instance_assigned(
        self, local_paas_service, mock_repository
    ):
        """Empty connected_server_instance raises DeviceCreationError INSTANCE_NOT_ASSIGNED."""
        # Setup: Repository returns record with empty connected_server_instance
        mock_record = MagicMock()
        mock_record.connected_server_instance = ""  # Empty string - data inconsistency
        mock_record.status = "ONLINE"
        mock_record.last_heartbeat = None
        mock_repository.get_by_machine_id.return_value = mock_record

        # Execute & Assert
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "INSTANCE_NOT_ASSIGNED"
        assert "machine-001" in exc_info.value.message
        assert exc_info.value.context is not None
        assert exc_info.value.context["action"] == "get_machine_info"


class TestGetDefaultLocalTemplateId:
    """Tests for _get_default_local_template_id fallback logic."""

    def test_returns_template_id_from_repository(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """When repository available and returns template_id, use it."""
        mock_device_template_repository.get_default_local_template_id.return_value = 100

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
        )

        result = service._get_default_local_template_id()

        assert result == 100
        mock_device_template_repository.get_default_local_template_id.assert_called_once()

    def test_raises_when_repository_none(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """WR-05: When repository is None, raise LOCAL_TEMPLATE_NOT_CONFIGURED."""
        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=None,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            service._get_default_local_template_id()

        assert exc_info.value.error_code == "LOCAL_TEMPLATE_NOT_CONFIGURED"
        assert "test" in exc_info.value.message

    def test_raises_when_no_template_found(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """WR-05: When query returns None, raise LOCAL_TEMPLATE_NOT_CONFIGURED."""
        mock_device_template_repository.get_default_local_template_id.return_value = (
            None
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            service._get_default_local_template_id()

        assert exc_info.value.error_code == "LOCAL_TEMPLATE_NOT_CONFIGURED"
        assert "test" in exc_info.value.message


class TestHandleMngRegister:
    """Tests for handle_mng_register with dynamic template ID."""

    @pytest.mark.asyncio
    async def test_new_machine_uses_dynamic_template_id(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """New machine registration uses template ID from repository query."""
        mock_device_template_repository.get_default_local_template_id.return_value = 42
        mock_repository.get_by_machine_id.return_value = None  # New machine

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
        )

        await service.handle_mng_register(
            machine_id="test-machine-001",
            user_id="user-001",
            machine_name="Test Machine",
        )

        # Verify insert_machine was called with template_id=42
        mock_repository.insert_machine.assert_called_once()
        call_kwargs = mock_repository.insert_machine.call_args.kwargs
        assert call_kwargs["template_id"] == 42

    @pytest.mark.asyncio
    async def test_new_machine_raises_when_no_template_configured(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """WR-05: New machine registration fails fast with
        LOCAL_TEMPLATE_NOT_CONFIGURED when no Local template is configured
        (previously silently inserted template_id=0, violating FK/NOT-NULL)."""
        mock_device_template_repository.get_default_local_template_id.return_value = (
            None
        )
        mock_repository.get_by_machine_id.return_value = None  # New machine

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service.handle_mng_register(
                machine_id="test-machine-002",
                user_id="user-002",
            )

        assert exc_info.value.error_code == "LOCAL_TEMPLATE_NOT_CONFIGURED"
        # No insert_machine call when template lookup raises.
        mock_repository.insert_machine.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_machine_no_template_query(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """Existing machine update doesn't query template ID (only new machines need it)."""
        # Existing machine
        mock_record = MagicMock()
        mock_repository.get_by_machine_id.return_value = mock_record

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
        )

        await service.handle_mng_register(
            machine_id="existing-machine",
            user_id="user-001",
            machine_name="Existing Machine",
        )

        # Verify template repository was NOT queried
        mock_device_template_repository.get_default_local_template_id.assert_not_called()
        # Verify update methods were called instead of insert
        mock_repository.update_machine_info.assert_called_once()
        mock_repository.update_status.assert_called_once()


# =============================================================================
# Tests for untested methods: get_credentials, get_platform_type,
# resolve_ws_conn_info, invoke_http_in_device, update_outbound_operation_rule,
# _validate_relative_dir_path, _validate_mount_path, get_machine_res_dirs,
# update_device_ttl, handle_mng_heartbeat, handle_mng_disconnect,
# list_machines_by_user, restart_device, handle_callback,
# _handle_container_ready
# =============================================================================


class TestGetCredentials:
    """Tests for get_credentials method."""

    @pytest.mark.asyncio
    async def test_returns_stored_credentials(
        self, local_paas_service, local_credentials
    ):
        """get_credentials returns the credentials passed at construction."""
        result = await local_paas_service.get_credentials()
        assert result is local_credentials
        assert result.template_id == 1
        assert result.template_uuid == "tpl-local-001"
        assert result.tenant_name == "test-tenant"


class TestGetPlatformType:
    """Tests for get_platform_type method."""

    @pytest.mark.asyncio
    async def test_returns_local_tenant_type(self, local_paas_service):
        """get_platform_type returns TenantType.LOCAL."""
        from secbaas.community.api.tenant_manage import TenantType

        result = await local_paas_service.get_platform_type()
        assert result == TenantType.LOCAL


class TestResolveWsConnInfoRelay:
    """Tests for resolve_ws_conn_info method with WS relay mode.

    Per D-02 (mixed mode): Plugin constructs token/target/ws_url/expires_at;
    Service handles session_id generation, DB lookup, insert_init, _route_command.
    Per D-04: Plugin SandboxPluginError converts to DeviceCreationError.

    Coverage:
    - Success path: Plugin delegation returns WsConnectionInfo; _route_command called
    - Plugin params verification: session_id, container_id, machine_id, user_id,
      port, path, template_id correctly passed
    - Timeout: send_command timeout maps to RELAY_TIMEOUT (plugin called first)
    - Connection error: ConnectionError maps to MACHINE_OFFLINE
    - mng error response: status: "error" passthrough / fallback to RELAY_SETUP_FAILED
    - DB errors: machine not found / machine offline
    - Command format: open_ws_relay params include token/target from Plugin
    - Path parameter passed through to Plugin
    - insert_init called (Service owns DB)
    - relay_repository None-safe
    - SandboxPluginError -> DeviceCreationError conversion (D-04)
    """

    @pytest.mark.asyncio
    async def test_relay_success_returns_ws_connection_info(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        mock_desktop_sandbox_plugin,
    ):
        """Successful relay: Plugin returns WsConnectionInfo, Service delegates."""
        from datetime import UTC, datetime, timedelta

        from secbaas.community.api.bot_runtime._ws_connection_info import (
            WsConnectionInfo,
        )

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        result = await local_paas_service._resolve_ws_conn_info_relay(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        # Plugin was delegated to
        mock_desktop_sandbox_plugin.resolve_ws_conn_info.assert_called_once()
        assert isinstance(result, WsConnectionInfo)
        assert result.token == "mock-jwt-token"
        assert "localhost" not in result.ws_url
        assert "agentclawproxy" in result.ws_url
        assert "/wsrelay/" in result.ws_url
        assert result.target.startswith("LOCAL_")
        assert result.ws_url.startswith("wss://")
        assert result.expires_at > datetime.now(UTC)
        assert result.expires_at < datetime.now(UTC) + timedelta(seconds=130)
        # _route_command was called (Service routing preserved)
        mock_connection_manager.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_relay_success_ws_url_contains_session_id(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """ws_url contains /wsrelay/ prefix — Plugin-constructed value."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        result = await local_paas_service._resolve_ws_conn_info_relay(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        assert "/wsrelay/" in result.ws_url

    @pytest.mark.asyncio
    async def test_plugin_resolve_ws_conn_info_params(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        mock_desktop_sandbox_plugin,
    ):
        """Service passes correct params to Plugin.resolve_ws_conn_info."""
        import re

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        await local_paas_service._resolve_ws_conn_info_relay(
            paas_device_id="abc123--machine-001--user-001",
            port=3000,
            path="/api/openclaw/ws",
        )

        mock_desktop_sandbox_plugin.resolve_ws_conn_info.assert_called_once()
        call_kwargs = mock_desktop_sandbox_plugin.resolve_ws_conn_info.call_args.kwargs

        assert call_kwargs["container_id"] == "abc123"
        assert call_kwargs["machine_id"] == "machine-001"
        assert call_kwargs["user_id"] == "user-001"
        assert call_kwargs["port"] == 3000
        assert call_kwargs["path"] == "/api/openclaw/ws"
        assert call_kwargs["template_id"] == 1  # from local_credentials fixture
        # session_id is 32-char hex
        assert len(call_kwargs["session_id"]) == 32
        assert re.match(r"^[0-9a-f]{32}$", call_kwargs["session_id"]) is not None

    @pytest.mark.asyncio
    async def test_timeout_raises_relay_timeout(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """send_command timeout maps to RELAY_TIMEOUT; Plugin called first."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.side_effect = TimeoutError(
            "Command timed out after 30s"
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "RELAY_TIMEOUT"
        assert "timed out" in str(exc_info.value.message).lower()

    @pytest.mark.asyncio
    async def test_connection_error_raises_relay_command_failed(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """ConnectionError maps to RELAY_COMMAND_FAILED error code."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.side_effect = ConnectionError(
            "Machine not connected"
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "RELAY_COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_mng_error_passthrough(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng status: error with error field is passthrough to caller."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "INVALID_CONTAINER",
            "message": "Container not found",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "INVALID_CONTAINER"
        assert "Container not found" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_mng_error_fallback_to_relay_setup_failed(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """mng status: error without error field falls back to RELAY_SETUP_FAILED."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "RELAY_SETUP_FAILED"

    @pytest.mark.asyncio
    async def test_machine_not_found_in_db(self, local_paas_service, mock_repository):
        """DB has no record for machine_id raises MACHINE_NOT_FOUND."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.resolve_ws_conn_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_machine_offline_in_db(self, local_paas_service, mock_repository):
        """DB record shows OFFLINE raises MACHINE_OFFLINE."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "OFFLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.resolve_ws_conn_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "MACHINE_OFFLINE"

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_accepts_path_param(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """resolve_ws_conn_info accepts and passes path parameter to Plugin."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        result = await local_paas_service.resolve_ws_conn_info(
            paas_device_id="abc123--machine-001--user-001",
            port=3000,
            path="/api/some/ws/path",
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_open_ws_relay_command_params_exclude_port(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """open_ws_relay command params include token/target/port from Plugin, exclude container_id."""
        import re

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        await local_paas_service._resolve_ws_conn_info_relay(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        # Extract the command dict sent to send_command (arg[0] = machine_id, arg[1] = command)
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]

        assert isinstance(command, dict)
        assert command["action"] == "open_ws_relay"
        assert "params" in command
        assert "port" in command["params"]
        assert command["params"]["port"] == 8080
        assert "container_id" not in command["params"]
        session_id = command["params"]["session_id"]
        assert len(session_id) == 32
        assert re.match(r"^[0-9a-f]{32}$", session_id) is not None
        # Phase 64: token and target must exist in command params (from Plugin)
        assert "token" in command["params"]
        assert "target" in command["params"]
        assert isinstance(command["params"]["token"], str)
        assert len(command["params"]["token"]) > 0
        assert command["params"]["token"] == "mock-jwt-token"
        assert isinstance(command["params"]["target"], str)
        assert command["params"]["target"].startswith("LOCAL_")

    @pytest.mark.asyncio
    async def test_insert_init_called_with_correct_params(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_connection_manager,
        mock_relay_repository,
    ):
        """insert_init() is called with correct session_id, machine_id, operator."""
        import re

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        await local_paas_service_with_relay._resolve_ws_conn_info_relay(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        mock_relay_repository.insert_init.assert_called_once()
        call_kwargs = mock_relay_repository.insert_init.call_args.kwargs
        session_id = call_kwargs["session_id"]
        assert len(session_id) == 32
        assert re.match(r"^[0-9a-f]{32}$", session_id) is not None
        assert call_kwargs["machine_id"] == "machine-001"
        assert call_kwargs["operator"] == "user-001"

    @pytest.mark.asyncio
    async def test_insert_init_called_before_send_command(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_connection_manager,
        mock_relay_repository,
    ):
        """insert_init() is called before send_command."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        await local_paas_service_with_relay._resolve_ws_conn_info_relay(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        assert mock_relay_repository.insert_init.call_count == 1
        assert mock_connection_manager.send_command.call_count == 1

    @pytest.mark.asyncio
    async def test_relay_success_without_relay_repository(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """resolve_ws_conn_info succeeds without relay_repository (None-safe)."""
        from secbaas.community.api.bot_runtime._ws_connection_info import (
            WsConnectionInfo,
        )

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        result = await local_paas_service.resolve_ws_conn_info(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        assert isinstance(result, WsConnectionInfo)

    @pytest.mark.asyncio
    async def test_insert_init_failure_propagates(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_connection_manager,
        mock_relay_repository,
    ):
        """insert_init() failure raises DeviceCreationError, send_command not called."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_relay_repository.insert_init.side_effect = DeviceCreationError(
            error_code="DB_ERROR",
            message="insert failed",
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service_with_relay._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert exc_info.value.error_code == "DB_ERROR"
        # send_command must NOT be called after insert_init failure
        mock_connection_manager.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_sandbox_plugin_error_converted_to_device_creation_error(
        self,
        local_paas_service,
        mock_repository,
        mock_desktop_sandbox_plugin,
    ):
        """Per D-04: Plugin SandboxPluginError converts to DeviceCreationError."""
        from secbaas.community.spi.sandbox.desktop._errors import SandboxPluginError

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_desktop_sandbox_plugin.resolve_ws_conn_info.side_effect = (
            SandboxPluginError(
                error_code="RELAY_TIMEOUT",
                message="Plugin timeout",
            )
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/ws",
            )

        assert exc_info.value.error_code == "RELAY_TIMEOUT"
        assert "Plugin timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_insert_init_generic_exception_converts_to_relay_db_error(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_relay_repository,
    ):
        """insert_init() generic Exception converts to RELAY_DB_ERROR DeviceCreationError."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_relay_repository.insert_init.side_effect = RuntimeError(
            "DB connection lost"
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service_with_relay._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/ws",
            )

        assert exc_info.value.error_code == "RELAY_DB_ERROR"
        assert "DB connection lost" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_route_command_generic_exception_converts_to_relay_setup_failed(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_relay_repository,
    ):
        """_route_command generic Exception converts to RELAY_SETUP_FAILED."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Plugin resolves successfully, but _route_command raises unexpected error
        local_paas_service_with_relay._route_command = AsyncMock(
            side_effect=RuntimeError("Unexpected routing error")
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service_with_relay._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/ws",
            )

        assert exc_info.value.error_code == "RELAY_SETUP_FAILED"
        assert "Unexpected routing error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_relay_session_cleanup_update_closed_on_plugin_error(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_relay_repository,
        mock_desktop_sandbox_plugin,
    ):
        """update_closed() is called for cleanup when Plugin raises after insert_init."""
        from secbaas.community.spi.sandbox.desktop._errors import SandboxPluginError

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_desktop_sandbox_plugin.resolve_ws_conn_info.side_effect = (
            SandboxPluginError(
                error_code="PLUGIN_FAIL",
                message="Plugin crashed",
            )
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service_with_relay._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/ws",
            )

        assert exc_info.value.error_code == "PLUGIN_FAIL"
        # Cleanup: update_closed must be called after insert_init succeeded
        mock_relay_repository.update_closed.assert_called_once()
        call_kwargs = mock_relay_repository.update_closed.call_args.kwargs
        assert call_kwargs["session_id"] is not None

    @pytest.mark.asyncio
    async def test_relay_session_cleanup_swallows_update_closed_exception(
        self,
        local_paas_service_with_relay,
        mock_repository,
        mock_relay_repository,
        mock_desktop_sandbox_plugin,
    ):
        """Exception in update_closed() during cleanup is silently swallowed."""
        from secbaas.community.spi.sandbox.desktop._errors import SandboxPluginError

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_desktop_sandbox_plugin.resolve_ws_conn_info.side_effect = (
            SandboxPluginError(
                error_code="PLUGIN_FAIL",
                message="Plugin crashed",
            )
        )
        # update_closed itself also fails — should be silently swallowed
        mock_relay_repository.update_closed.side_effect = RuntimeError(
            "Cleanup also failed"
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service_with_relay._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/ws",
            )

        # Original error propagates, not the cleanup error
        assert exc_info.value.error_code == "PLUGIN_FAIL"
        mock_relay_repository.update_closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_ws_url_from_plugin_raises_plugin_error(
        self,
        local_paas_service,
        mock_repository,
        mock_desktop_sandbox_plugin,
    ):
        """Empty ws_url from Plugin raises PLUGIN_ERROR DeviceCreationError."""
        from datetime import UTC, datetime, timedelta

        from secbaas.community.api.bot_runtime._ws_connection_info import (
            WsConnectionInfo,
        )

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        # Plugin returns a WsConnectionInfo with empty ws_url
        mock_desktop_sandbox_plugin.resolve_ws_conn_info.return_value = (
            WsConnectionInfo(
                ws_url="",
                token="mock-token",
                target="LOCAL_ctr--mach--user@1:8080:test-session",
                expires_at=datetime.now(UTC) + timedelta(seconds=120),
            )
        )
        # _route_command must succeed to reach the ws_url check below
        local_paas_service._route_command = AsyncMock(
            return_value={"status": "success", "data": {}}
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._resolve_ws_conn_info_relay(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/ws",
            )

        assert exc_info.value.error_code == "PLUGIN_ERROR"
        assert "empty" in str(exc_info.value).lower()

    # ── Direct mode regression tests ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_direct_mode_returns_localhost_url(
        self, local_paas_service, mock_connection_manager
    ):
        """Direct mode returns ws://localhost URL with empty token."""
        from secbaas.community.api.bot_runtime._ws_connection_info import (
            WsConnectionInfo,
        )
        from secbaas.community.api.device_manage import LocalDeviceInfo

        local_paas_service._ws_conn_mode = "direct"

        # Mock get_device_info → _route_command → send_command → device info
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"port": 12345, "container_id": "abc123"},
        }

        local_paas_service._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="test-instance",
            status="ONLINE",
        )

        result = await local_paas_service.resolve_ws_conn_info(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/api/openclaw/ws",
        )

        assert isinstance(result, WsConnectionInfo)
        assert result.ws_url.startswith("ws://localhost:")
        assert "/api/openclaw/ws" in result.ws_url
        assert result.token == ""
        assert result.target.startswith("localhost:")

    @pytest.mark.asyncio
    async def test_direct_mode_ignores_port_parameter(
        self, local_paas_service, mock_connection_manager
    ):
        """Direct mode ignores the port parameter, uses mng-returned port."""
        local_paas_service._ws_conn_mode = "direct"
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"port": 99999, "container_id": "abc123"},
        }

        local_paas_service._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="test-instance",
            status="ONLINE",
        )

        result = await local_paas_service.resolve_ws_conn_info(
            paas_device_id="abc123--machine-001--user-001",
            port=1234,  # This is intentionally ignored
            path="/ws",
        )

        # URL should use mng-returned port 99999, NOT 1234
        assert ":99999" in result.ws_url
        assert ":1234" not in result.ws_url

    @pytest.mark.asyncio
    async def test_direct_mode_24h_expiry(
        self, local_paas_service, mock_connection_manager
    ):
        """Direct mode returns 24-hour expiry."""
        from datetime import UTC, datetime, timedelta

        local_paas_service._ws_conn_mode = "direct"

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"port": 12345, "container_id": "abc123"},
        }

        local_paas_service._repository.get_by_machine_id.return_value = MagicMock(
            connected_server_instance="test-instance",
            status="ONLINE",
        )

        result = await local_paas_service.resolve_ws_conn_info(
            paas_device_id="abc123--machine-001--user-001",
            port=8080,
            path="/ws",
        )

        expected_max = datetime.now(UTC) + timedelta(hours=25)
        assert result.expires_at > datetime.now(UTC)
        assert result.expires_at < expected_max


class TestResolveInvokeHttpInfo:
    """Tests for resolve_invoke_http_info method with proxypass mode."""

    @pytest.mark.asyncio
    async def test_returns_proxypass_url_not_localhost(self, local_paas_service):
        """resolve_invoke_http_info returns proxypass URL (not localhost)."""
        # Setup: mock _build_proxypass_url and _generate_proxypass_jwt
        with (
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.build_proxypass_url",
                return_value="https://agentclawproxy-dev.service.test/proxypass/LOCAL_abc123--machine-001--user-001@1:8080/api/health",
            ),
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.generate_proxypass_jwt",
                return_value="eyJhbGciOiJIUzI1NiIs...",
            ),
            mock.patch("secbaas.community.bootstrap.get_container"),
        ):
            result = await local_paas_service.resolve_invoke_http_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/health",
            )

        from secbaas.community.api.bot_runtime._http_connection_info import (
            HttpConnectionInfo,
        )

        # Assert
        assert isinstance(result, HttpConnectionInfo)
        assert "localhost" not in result.http_url
        assert "agentclawproxy" in result.http_url
        assert result.token != ""
        assert result.target.startswith("LOCAL_")

    @pytest.mark.asyncio
    async def test_token_generated_via_proxypass_jwt(self, local_paas_service):
        """Token is generated via _generate_proxypass_jwt."""
        # Setup: mock with known JWT token
        with (
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.build_proxypass_url",
                return_value="https://agentclawproxy-dev.service.test/proxypass/LOCAL_abc123--machine-001--user-001@1:8080/",
            ),
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.generate_proxypass_jwt",
                return_value="mock-jwt-token-xyz",
            ) as mock_jwt,
            mock.patch("secbaas.community.bootstrap.get_container"),
        ):
            result = await local_paas_service.resolve_invoke_http_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
            )

        # Assert
        assert result.token == "mock-jwt-token-xyz"
        mock_jwt.assert_called_once()
        target_arg = mock_jwt.call_args[0][0]
        assert target_arg.startswith("LOCAL_")

    @pytest.mark.asyncio
    async def test_target_format_includes_template_id(self, local_paas_service):
        """Target format includes @template_id suffix."""
        # Setup
        with (
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.build_proxypass_url",
                return_value="https://agentclawproxy-dev.service.test/proxypass/LOCAL_abc123--machine-001--user-001@1:8080/",
            ),
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.generate_proxypass_jwt",
                return_value="mock-jwt-token",
            ) as mock_url,
            mock.patch("secbaas.community.bootstrap.get_container"),
        ):
            await local_paas_service.resolve_invoke_http_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
            )

        # Assert: target passed to _build_proxypass_url includes @1
        target_arg = mock_url.call_args[0][0]
        assert target_arg == "LOCAL_abc123--machine-001--user-001@1:8080"
        assert "@1" in target_arg

    @pytest.mark.asyncio
    async def test_target_appends_path_when_non_default(self, local_paas_service):
        """Non-default path is passed to _build_proxypass_url, not embedded in target."""
        # Setup
        with (
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.build_proxypass_url",
                return_value="https://agentclawproxy-dev.service.test/proxypass/LOCAL_abc123--machine-001--user-001@1:8080/api/v1/invoke",
            ) as mock_url,
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.generate_proxypass_jwt",
                return_value="mock-jwt-token",
            ),
            mock.patch("secbaas.community.bootstrap.get_container"),
        ):
            await local_paas_service.resolve_invoke_http_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/api/v1/invoke",
            )

        # Assert: target does NOT include the path (aligned with TeClaw pattern)
        target_arg = mock_url.call_args[0][0]
        assert target_arg == "LOCAL_abc123--machine-001--user-001@1:8080"
        # Path is passed as second argument to _build_proxypass_url
        path_arg = mock_url.call_args[0][1]
        assert path_arg == "/api/v1/invoke"

    @pytest.mark.asyncio
    async def test_default_path_omits_target_path_suffix(self, local_paas_service):
        """Default path "/" does not append path segment to target."""
        # Setup
        with (
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.build_proxypass_url",
                return_value="https://agentclawproxy-dev.service.test/proxypass/LOCAL_abc123--machine-001--user-001@1:8080/",
            ),
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.generate_proxypass_jwt",
                return_value="mock-jwt-token",
            ) as mock_url,
            mock.patch("secbaas.community.bootstrap.get_container"),
        ):
            await local_paas_service.resolve_invoke_http_info(
                paas_device_id="abc123--machine-001--user-001",
                port=8080,
                path="/",
            )

        # Assert: target does not end with :/ (no path suffix for default)
        target_arg = mock_url.call_args[0][0]
        assert not target_arg.endswith(":/")

    @pytest.mark.asyncio
    async def test_http_connection_info_three_fields(self, local_paas_service):
        """HttpConnectionInfo returned has all three fields populated."""
        # Setup
        with (
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.build_proxypass_url",
                return_value="https://agentclawproxy-dev.service.test/proxypass/LOCAL_abc123--machine-001--user-001@1:3000/",
            ),
            mock.patch(
                "secbaas.community.core.utils.proxypass_utils.generate_proxypass_jwt",
                return_value="mock-jwt-token",
            ),
            mock.patch("secbaas.community.bootstrap.get_container"),
        ):
            result = await local_paas_service.resolve_invoke_http_info(
                paas_device_id="abc123--machine-001--user-001",
                port=3000,
                path=None,
            )

        from secbaas.community.api.bot_runtime._http_connection_info import (
            HttpConnectionInfo,
        )

        # Assert: all three fields present
        assert isinstance(result, HttpConnectionInfo)
        assert result.http_url is not None and result.http_url != ""
        assert result.token is not None and result.token != ""
        assert result.target is not None and result.target != ""
        assert result.target.startswith("LOCAL_")


class TestInvokeHttpInDevice:
    """Tests for invoke_http_in_device method."""

    @pytest.mark.asyncio
    async def test_success_returns_response_data(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """invoke_http_in_device returns response dict on success."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "body": "eyJoZWxsbyI6IndvcmxkIn0=",  # base64 of '{"hello":"world"}'
            },
        }

        result = await local_paas_service.invoke_http_in_device(
            paas_device_id="container--machine-001--user-001",
            method="GET",
            port=8080,
            path="/api/test",
            query_string="?key=value",
            headers={"Accept": "application/json"},
            body=b"",
        )

        assert result["status_code"] == 200
        assert result["headers"] == {"Content-Type": "application/json"}
        assert result["body"] == "eyJoZWxsbyI6IndvcmxkIn0="

    @pytest.mark.asyncio
    async def test_machine_not_found_raises_error(
        self, local_paas_service, mock_repository
    ):
        """Raises MACHINE_NOT_FOUND when repository returns None."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.invoke_http_in_device(
                paas_device_id="container--machine-001--user-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_bad_gateway_when_mng_returns_error(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Non-CONTAINER_NOT_FOUND error maps to BAD_GATEWAY."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "SOME_ERROR",
            "message": "Something went wrong",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.invoke_http_in_device(
                paas_device_id="container--machine-001--user-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.error_code == "BAD_GATEWAY"

    @pytest.mark.asyncio
    async def test_container_not_found_maps_to_container_not_found(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """CONTAINER_NOT_FOUND error maps to CONTAINER_NOT_FOUND (not BAD_GATEWAY)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "CONTAINER_NOT_FOUND",
            "message": "Container not found",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.invoke_http_in_device(
                paas_device_id="container--machine-001--user-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.error_code == "CONTAINER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_invalid_response_format_non_dict_data(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Raises BAD_GATEWAY when response data is not a dict."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": "not a dict",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.invoke_http_in_device(
                paas_device_id="container--machine-001--user-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.error_code == "BAD_GATEWAY"
        assert "Invalid response format" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_invalid_response_missing_status_code(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Raises BAD_GATEWAY when response data is missing status_code."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"headers": {}, "body": ""},
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.invoke_http_in_device(
                paas_device_id="container--machine-001--user-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
            )

        assert exc_info.value.error_code == "BAD_GATEWAY"
        assert "Missing status_code" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_normalizes_missing_query_string_leading_question_mark(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Query string without leading '?' is normalized."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"status_code": 200, "headers": {}, "body": ""},
        }

        await local_paas_service.invoke_http_in_device(
            paas_device_id="container--machine-001--user-001",
            method="GET",
            port=8080,
            path="/api/test",
            query_string="key=value",  # No leading ?
            headers={},
            body=b"",
        )

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["params"]["query_string"] == "?key=value"

    @pytest.mark.asyncio
    async def test_bodys_base64_encoded_in_command(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Request body is base64-encoded in the command params."""
        import base64

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {"status_code": 200, "headers": {}, "body": ""},
        }

        await local_paas_service.invoke_http_in_device(
            paas_device_id="container--machine-001--user-001",
            method="POST",
            port=8080,
            path="/api/test",
            query_string=None,
            headers={},
            body=b'{"hello":"world"}',
        )

        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        decoded = base64.b64decode(command["params"]["body"]).decode("utf-8")
        assert decoded == '{"hello":"world"}'


class TestUpdateOutboundOperationRule:
    """Tests for update_outbound_operation_rule stub."""

    @pytest.mark.asyncio
    async def test_raises_not_implemented_error(self, local_paas_service):
        """update_outbound_operation_rule always raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            await local_paas_service.update_outbound_operation_rule(
                paas_device_id="container--machine-001--user-001",
                outbound_operation_rule=MagicMock(),
            )


class TestValidateRelativeDirPath:
    """Tests for _validate_relative_dir_path method."""

    def test_rejects_dot_dot_path(self, local_paas_service):
        """Rejects paths containing '..' (directory traversal)."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_relative_dir_path("../etc/passwd")

        assert exc_info.value.error_code == "INVALID_PARAMS"
        assert ".." in exc_info.value.message

    def test_rejects_dot_dot_in_middle_of_path(self, local_paas_service):
        """Rejects paths containing '..' anywhere in path."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_relative_dir_path("foo/../bar")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_rejects_absolute_path(self, local_paas_service):
        """Rejects paths starting with '/'."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_relative_dir_path("/etc/hosts")

        assert exc_info.value.error_code == "INVALID_PARAMS"
        assert "relative" in exc_info.value.message.lower()

    def test_allows_valid_relative_path(self, local_paas_service):
        """Allows valid relative paths (no .., no leading /)."""
        # Should not raise
        local_paas_service._validate_relative_dir_path("Desktop/projects")


class TestValidateMountPath:
    """Tests for _validate_mount_path method."""

    def test_none_passes(self, local_paas_service):
        """None mount_path passes validation."""
        # Should not raise
        local_paas_service._validate_mount_path(None)

    def test_rejects_relative_path(self, local_paas_service):
        """Rejects relative paths (must start with /)."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("relative/path")

        assert exc_info.value.error_code == "INVALID_PARAMS"
        assert "must be absolute" in exc_info.value.message.lower()

    def test_rejects_dot_dot_traversal(self, local_paas_service):
        """Rejects paths containing '..' traversal."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/var/../root")

        assert exc_info.value.error_code == "INVALID_PARAMS"
        assert ".." in exc_info.value.message

    def test_rejects_blocked_system_dir_etc(self, local_paas_service):
        """Rejects /etc as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/etc/nginx")

        assert exc_info.value.error_code == "INVALID_PARAMS"
        assert "system directory" in exc_info.value.message.lower()

    def test_rejects_blocked_system_dir_bin(self, local_paas_service):
        """Rejects /bin as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/bin/bash")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_rejects_blocked_system_dir_root(self, local_paas_service):
        """Rejects /root as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/root/.ssh")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_rejects_blocked_system_dir_proc(self, local_paas_service):
        """Rejects /proc as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/proc/cpuinfo")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_rejects_blocked_system_dir_sys(self, local_paas_service):
        """Rejects /sys as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/sys/class")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_allows_valid_absolute_path(self, local_paas_service):
        """Allows valid absolute paths not in blocked list."""
        # Should not raise
        local_paas_service._validate_mount_path("/home/user/projects")
        local_paas_service._validate_mount_path("/tmp/storage")
        local_paas_service._validate_mount_path("/var/lib/data")


class TestGetMachineResDirs:
    """Tests for get_machine_res_dirs method."""

    @pytest.mark.asyncio
    async def test_success_returns_directory_data(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Returns directory tree data from mng daemon."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        expected_data = {
            "name": "Desktop",
            "children": [
                {"name": "projects", "children": []},
                {"name": "readme.txt"},
            ],
        }
        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": expected_data,
        }

        result = await local_paas_service.get_machine_res_dirs("machine-001")

        assert result == expected_data

    @pytest.mark.asyncio
    async def test_machine_not_found_raises_error(
        self, local_paas_service, mock_repository
    ):
        """Raises MACHINE_NOT_FOUND when machine not in repository."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_res_dirs("machine-001")

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_error_from_mng_raises_device_creation_error(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Error response from mng daemon raises DeviceCreationError."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "QUERY_FAILED",
            "message": "Failed to read directory",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_res_dirs("machine-001")

        assert exc_info.value.error_code == "QUERY_FAILED"

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_dict(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """None data field returns empty dict."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": None,
        }

        result = await local_paas_service.get_machine_res_dirs("machine-001")

        assert result == {}

    @pytest.mark.asyncio
    async def test_invalid_path_raises_error(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Directory traversal path raises DeviceCreationError before any command."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_res_dirs("machine-001", dir="../etc")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_invalid_response_non_dict_data(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Non-dict data in response raises INVALID_RESPONSE."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": ["not", "a", "dict"],
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_res_dirs("machine-001")

        assert exc_info.value.error_code == "INVALID_RESPONSE"


class TestUpdateDeviceTtl:
    """Tests for update_device_ttl stub."""

    @pytest.mark.asyncio
    async def test_raises_not_implemented_error(self, local_paas_service):
        """update_device_ttl always raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="does not support TTL"):
            await local_paas_service.update_device_ttl(
                "container--machine-001--user-001"
            )


class TestHandleMngHeartbeat:
    """Tests for handle_mng_heartbeat method."""

    @pytest.mark.asyncio
    async def test_calls_repository_update_heartbeat(
        self, local_paas_service, mock_repository
    ):
        """Calls repository.update_heartbeat with correct params."""
        await local_paas_service.handle_mng_heartbeat("machine-001")

        mock_repository.update_heartbeat.assert_called_once()
        call_kwargs = mock_repository.update_heartbeat.call_args.kwargs
        assert call_kwargs["machine_id"] == "machine-001"
        assert call_kwargs["env"] == "test"

    @pytest.mark.asyncio
    async def test_raises_value_error_for_empty_machine_id(self, local_paas_service):
        """Raises ValueError when machine_id is empty string."""
        with pytest.raises(ValueError, match="machine_id is required"):
            await local_paas_service.handle_mng_heartbeat("")


class TestHandleMngDisconnect:
    """Tests for handle_mng_disconnect method."""

    @pytest.mark.asyncio
    async def test_calls_repository_update_status_offline(
        self, local_paas_service, mock_repository
    ):
        """Calls repository.update_status with OFFLINE status."""
        await local_paas_service.handle_mng_disconnect("machine-001")

        mock_repository.update_status.assert_called_once()
        call_kwargs = mock_repository.update_status.call_args.kwargs
        assert call_kwargs["machine_id"] == "machine-001"
        assert call_kwargs["env"] == "test"
        assert call_kwargs["status"] == "OFFLINE"

    @pytest.mark.asyncio
    async def test_raises_value_error_for_empty_machine_id(self, local_paas_service):
        """Raises ValueError when machine_id is empty."""
        with pytest.raises(ValueError, match="machine_id is required"):
            await local_paas_service.handle_mng_disconnect("")

    # Phase 33: Tests for device status update logic in handle_mng_disconnect

    @pytest.fixture
    def local_paas_service_with_device_repo(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """Create a LocalPaasService instance with mocked device_repository."""
        mock_device_repo = MagicMock()
        return (
            LocalPaasService(
                credentials=local_credentials,
                repository=mock_repository,
                connection_manager=mock_connection_manager,
                instance_router=mock_instance_router,
                server_ip="test-instance",
                desktop_sandbox_plugin=MagicMock(),
                env="test",
                device_template_repository=mock_device_template_repository,
                device_repository=mock_device_repo,
            ),
            mock_repository,
            mock_device_repo,
        )

    @pytest.mark.asyncio
    async def test_handle_mng_disconnect_updates_active_devices(
        self, local_paas_service_with_device_repo
    ):
        """Device update logic: ACTIVE devices found and updated."""
        service, mock_repository, mock_device_repo = local_paas_service_with_device_repo

        # Setup: Machine record with user_id
        mock_machine = MagicMock()
        mock_machine.user_id = "user-123"
        mock_repository.get_by_machine_id.return_value = mock_machine

        # Setup: Two ACTIVE devices found
        device1 = MagicMock()
        device1.id = 101
        device2 = MagicMock()
        device2.id = 102
        mock_device_repo.list_active_local_devices_by_machine_user.return_value = [
            device1,
            device2,
        ]

        # Setup: Batch update returns 2
        mock_device_repo.batch_update_status_to_offline.return_value = 2

        # Execute
        await service.handle_mng_disconnect("machine-001")

        # Assert: Machine status updated first
        mock_repository.update_status.assert_called_once_with(
            machine_id="machine-001",
            env="test",
            status="OFFLINE",
        )

        # Assert: Device query called with correct params
        mock_device_repo.list_active_local_devices_by_machine_user.assert_called_once_with(
            machine_id="machine-001",
            user_id="user-123",
            env="test",
        )

        # Assert: Batch update called with correct device_ids
        mock_device_repo.batch_update_status_to_offline.assert_called_once_with(
            device_ids=[101, 102],
            env="test",
        )

    @pytest.mark.asyncio
    async def test_handle_mng_disconnect_no_active_devices_silent(
        self, local_paas_service_with_device_repo
    ):
        """No ACTIVE devices: silent handling, no batch update called."""
        service, mock_repository, mock_device_repo = local_paas_service_with_device_repo

        # Setup: Machine record with user_id
        mock_machine = MagicMock()
        mock_machine.user_id = "user-123"
        mock_repository.get_by_machine_id.return_value = mock_machine

        # Setup: No devices found
        mock_device_repo.list_active_local_devices_by_machine_user.return_value = []

        # Execute
        await service.handle_mng_disconnect("machine-001")

        # Assert: No batch update (per D-04: silent for empty results)
        mock_device_repo.batch_update_status_to_offline.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_mng_disconnect_device_repository_none(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """DeviceRepository None: defensive behavior, logs warning."""
        # Create service WITHOUT device_repository
        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=None,  # Explicitly None
        )

        # Setup: Machine record exists
        mock_machine = MagicMock()
        mock_machine.user_id = "user-123"
        mock_repository.get_by_machine_id.return_value = mock_machine

        # Execute (should not raise)
        await service.handle_mng_disconnect("machine-001")

        # Assert: Machine status still updated
        mock_repository.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_mng_disconnect_machine_not_found(
        self, local_paas_service_with_device_repo
    ):
        """Machine not found: logs warning, returns early."""
        service, mock_repository, mock_device_repo = local_paas_service_with_device_repo

        # Setup: Machine not found
        mock_repository.get_by_machine_id.return_value = None

        # Execute
        await service.handle_mng_disconnect("machine-999")

        # Assert: No device queries or updates attempted
        mock_device_repo.list_active_local_devices_by_machine_user.assert_not_called()
        mock_device_repo.batch_update_status_to_offline.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_mng_disconnect_partial_update(
        self, local_paas_service_with_device_repo
    ):
        """Some devices fail to update: log shows found vs updated count."""
        service, mock_repository, mock_device_repo = local_paas_service_with_device_repo

        # Setup: Machine record with user_id
        mock_machine = MagicMock()
        mock_machine.user_id = "user-123"
        mock_repository.get_by_machine_id.return_value = mock_machine

        # Setup: Two devices, but only one updated
        device1 = MagicMock()
        device1.id = 101
        device2 = MagicMock()
        device2.id = 102
        mock_device_repo.list_active_local_devices_by_machine_user.return_value = [
            device1,
            device2,
        ]

        # Setup: Batch update returns 1 (one failed)
        mock_device_repo.batch_update_status_to_offline.return_value = 1

        # Execute
        await service.handle_mng_disconnect("machine-001")

        # Assert: Batch update called
        mock_device_repo.batch_update_status_to_offline.assert_called_once_with(
            device_ids=[101, 102],
            env="test",
        )


class TestListMachinesByUser:
    """Tests for list_machines_by_user method."""

    @pytest.mark.asyncio
    async def test_returns_online_machines_only(
        self, local_paas_service, mock_repository
    ):
        """Returns only machines with ONLINE status."""
        online_record = MagicMock()
        online_record.status = "ONLINE"
        online_record.user_id = "user-001"

        mock_repository.list_by_user_id.return_value = [online_record]

        result = await local_paas_service.list_machines_by_user("user-001")

        assert len(result) == 1
        assert result[0].status == "ONLINE"

    @pytest.mark.asyncio
    async def test_filters_out_offline_machines(
        self, local_paas_service, mock_repository
    ):
        """Filters out OFFLINE machines from results."""
        online_record = MagicMock()
        online_record.status = "ONLINE"
        offline_record = MagicMock()
        offline_record.status = "OFFLINE"

        mock_repository.list_by_user_id.return_value = [online_record, offline_record]

        result = await local_paas_service.list_machines_by_user("user-001")

        assert len(result) == 1
        assert result[0].status == "ONLINE"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_all_offline(
        self, local_paas_service, mock_repository
    ):
        """Returns empty list when all machines are OFFLINE."""
        offline_record = MagicMock()
        offline_record.status = "OFFLINE"

        mock_repository.list_by_user_id.return_value = [offline_record]

        result = await local_paas_service.list_machines_by_user("user-001")

        assert result == []

    @pytest.mark.asyncio
    async def test_raises_value_error_for_empty_user_id(self, local_paas_service):
        """Raises ValueError when user_id is empty."""
        with pytest.raises(ValueError, match="user_id is required"):
            await local_paas_service.list_machines_by_user("")


class TestRestartDevice:
    """Tests for restart_device method."""

    @pytest.mark.asyncio
    async def test_success_same_instance(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Successful restart on same instance returns True."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {"status": "success"}

        result = await local_paas_service.restart_device(
            "container--machine-001--user-001"
        )

        assert result is True

        # Verify correct command was sent
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["action"] == "restart_device"
        assert command["params"]["container_id"] == "container"

    @pytest.mark.asyncio
    async def test_success_cross_instance(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Successful restart on cross instance via InstanceRouter."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_instance_router.route_to_instance.return_value = {"status": "success"}

        result = await local_paas_service.restart_device(
            "container--machine-001--user-001"
        )

        assert result is True

        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "restart_device"
        assert call_args.kwargs["params"]["container_id"] == "container"

    @pytest.mark.asyncio
    async def test_machine_not_found_raises_error(
        self, local_paas_service, mock_repository
    ):
        """Raises MACHINE_NOT_FOUND when machine not in repository."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.restart_device("container--machine-001--user-001")

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_restart_failed_error_from_mng(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """RESTART_FAILED error from mng raises DeviceCreationError."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "RESTART_FAILED",
            "message": "Restart operation failed",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.restart_device("container--machine-001--user-001")

        assert exc_info.value.error_code == "RESTART_FAILED"


class TestHandleCallback:
    """Tests for handle_callback method."""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_none(self, local_paas_service):
        """Unknown callback action returns None."""
        result = await local_paas_service.handle_callback(
            machine_id="machine-001",
            action="unknown_action",
            params={},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_container_ready_action_delegates_to_handler(
        self, local_paas_service, mock_repository
    ):
        """container_ready action delegates to _handle_container_ready and returns None."""
        # Setup: Missing container_id will cause DeviceCreationError early
        # but we want to test delegation - so we need to set up the mock properly.
        # For container_ready without container_id, it raises.
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.handle_callback(
                machine_id="machine-001",
                action="container_ready",
                params={},
            )

        assert exc_info.value.error_code == "MISSING_CONTAINER_ID"


class TestHandleContainerReady:
    """Tests for _handle_container_ready method."""

    @pytest.mark.asyncio
    async def test_missing_container_id_raises_error(self, local_paas_service):
        """Raises DeviceCreationError when container_id is missing."""
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._handle_container_ready(
                machine_id="machine-001",
                params={},
            )

        assert exc_info.value.error_code == "MISSING_CONTAINER_ID"

    @pytest.mark.asyncio
    async def test_missing_container_id_empty_string(self, local_paas_service):
        """Raises DeviceCreationError when container_id is empty string."""
        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._handle_container_ready(
                machine_id="machine-001",
                params={"container_id": ""},
            )

        assert exc_info.value.error_code == "MISSING_CONTAINER_ID"

    @pytest.mark.asyncio
    async def test_repositories_not_configured_returns_early(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Returns None early when device_repository or publish_record_repository is None."""
        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=None,
            device_repository=None,  # Not configured
            publish_record_repository=None,  # Not configured
        )

        result = await service._handle_container_ready(
            machine_id="machine-001",
            params={"container_id": "container-001"},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_device_not_found_logs_warning_no_exception(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """Device not found logs a warning and returns None (no exception)."""
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_provider_device_id_prefix.return_value = (
            None  # Device not found
        )

        mock_publish_record_repo = MagicMock()

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=mock_publish_record_repo,
        )

        result = await service._handle_container_ready(
            machine_id="machine-001",
            params={"container_id": "container-001"},
        )

        assert result is None
        mock_device_repo.get_by_provider_device_id_prefix.assert_called_once()

    @pytest.mark.asyncio
    async def test_machine_not_found_in_callback_returns_early(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """Machine not found returns early with warning (no exception)."""
        mock_repository.get_by_machine_id.return_value = None  # Machine not found

        mock_device_repo = MagicMock()
        mock_publish_record_repo = MagicMock()

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=mock_publish_record_repo,
        )

        result = await service._handle_container_ready(
            machine_id="machine-001",
            params={"container_id": "container-001"},
        )

        assert result is None
        # Device repo should NOT be called since we returned early
        mock_device_repo.get_by_provider_device_id_prefix.assert_not_called()


class TestRouteCommandEdgeCases:
    """Edge cases for _route_command TOCTOU and error handling."""

    @pytest.mark.asyncio
    async def test_empty_instance_raises_instancenotassigned(
        self, local_paas_service, mock_repository
    ):
        """When DB record has empty connected_server_instance, raises INSTANCE_NOT_ASSIGNED."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = ""
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="original-target",
            )

        assert exc_info.value.error_code == "INSTANCE_NOT_ASSIGNED"

    @pytest.mark.asyncio
    async def test_route_command_logs_instance_change(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """When DB instance differs from target, logs warning but uses current instance."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "changed-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        # The current instance does NOT match server_ip, so it routes cross-instance
        # But first, it must match the WebSocket check. Since server_ip is "test-instance"
        # and current_instance is "changed-instance", is_connected isn't called.
        mock_connection_manager.is_connected.return_value = True

        # instance_router should handle the cross-instance routing
        mock_instance_router = local_paas_service._instance_router
        mock_instance_router.route_to_instance = AsyncMock()
        mock_instance_router.route_to_instance.return_value = {"status": "success"}

        result = await local_paas_service._route_command(
            machine_id="machine-001",
            command={"action": "test", "params": {}},
            target_instance="original-target",
        )

        assert result == {"status": "success"}
        mock_instance_router.route_to_instance.assert_called_once()
        # Verify it used the changed instance
        assert (
            mock_instance_router.route_to_instance.call_args.kwargs["target_instance"]
            == "changed-instance"
        )


class TestGetMachineInfoNonDictResponse:
    """Test get_machine_info with non-dict response data."""

    @pytest.mark.asyncio
    async def test_non_dict_data_raises_invalid_response(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Non-dict response data raises INVALID_RESPONSE."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": "string instead of dict",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "INVALID_RESPONSE"


class TestGetMachineResDirsConnectionError:
    """Test get_machine_res_dirs ConnectionError handling."""

    @pytest.mark.asyncio
    async def test_connection_error_converts_to_device_creation_error(
        self, local_paas_service, mock_repository
    ):
        """ConnectionError in get_machine_res_dirs converts to MACHINE_OFFLINE."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_record.last_heartbeat = None
        mock_repository.get_by_machine_id.return_value = mock_record

        # _route_command raises ConnectionError
        mock_repository.get_by_machine_id.side_effect = [
            mock_record,  # first call in get_machine_res_dirs
            ConnectionError("WebSocket not connected"),  # second in _route_command
        ]

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_res_dirs("machine-001")

        assert exc_info.value.error_code == "MACHINE_OFFLINE"

    @pytest.mark.asyncio
    async def test_cross_instance_success(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """get_machine_res_dirs cross-instance via InstanceRouter."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.side_effect = [
            mock_record,  # first call
            mock_record,  # second in _route_command
        ]

        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {"name": "Desktop", "children": []},
        }

        result = await local_paas_service.get_machine_res_dirs("machine-001")

        assert result == {"name": "Desktop", "children": []}
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "get_machine_res_dirs"


class TestInvokeHttpInDeviceCrossInstance:
    """Test invoke_http_in_device cross-instance routing."""

    @pytest.mark.asyncio
    async def test_cross_instance_success(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """invoke_http_in_device cross-instance via InstanceRouter."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_instance_router.route_to_instance.return_value = {
            "status": "success",
            "data": {
                "status_code": 200,
                "headers": {},
                "body": "e30=",
            },
        }

        result = await local_paas_service.invoke_http_in_device(
            paas_device_id="container--machine-001--user-001",
            method="POST",
            port=8080,
            path="/api/test",
            query_string=None,
            headers={},
            body=b"{}",
        )

        assert result["status_code"] == 200
        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "invoke_http"


class TestHandleMngRegisterEdgeCases:
    """Edge cases for handle_mng_register."""

    @pytest.mark.asyncio
    async def test_raises_value_error_for_empty_machine_id(self, local_paas_service):
        """Raises ValueError when machine_id is empty."""
        with pytest.raises(ValueError, match="machine_id is required"):
            await local_paas_service.handle_mng_register(
                machine_id="",
                user_id="user-001",
            )

    @pytest.mark.asyncio
    async def test_without_machine_name_creates_null_machine_info(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """New machine without machine_name creates record with None machine_info."""
        mock_repository.get_by_machine_id.return_value = None
        mock_device_template_repository.get_default_local_template_id.return_value = 42

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
        )

        await service.handle_mng_register(
            machine_id="machine-no-name",
            user_id="user-001",
        )

        mock_repository.insert_machine.assert_called_once()
        call_kwargs = mock_repository.insert_machine.call_args.kwargs
        assert call_kwargs["machine_info"] is None


class TestHandleContainerReadyIdempotency:
    """Tests for _handle_container_ready idempotency and duplicate callback."""

    @pytest.mark.asyncio
    async def test_duplicate_callback_returns_early(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """When result_status is not CREATED, returns None (idempotency)."""
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        mock_device_repo = MagicMock()
        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        mock_publish_record_repo = MagicMock()
        record = MagicMock()
        record.result_status = "SUCCESS"  # Already processed
        record.publish_id = 100
        mock_publish_record_repo.get_latest_processing_record_by_device.return_value = (
            record
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=mock_publish_record_repo,
        )

        result = await service._handle_container_ready(
            machine_id="machine-001",
            params={"container_id": "container-001"},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_no_publish_record_returns_early(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """When no CREATED publish record found, returns None (race condition)."""
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        mock_device_repo = MagicMock()
        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        mock_publish_record_repo = MagicMock()
        mock_publish_record_repo.get_latest_processing_record_by_device.return_value = (
            None
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=mock_publish_record_repo,
        )

        result = await service._handle_container_ready(
            machine_id="machine-001",
            params={"container_id": "container-001"},
        )

        assert result is None


class TestNormalizeMessage:
    """Tests for _normalize_message helper function."""

    def test_plain_string_returns_unchanged(self):
        """Plain string message returns unchanged."""
        result = _normalize_message("plain error message")
        assert result == "plain error message"

    def test_list_of_dicts_with_message_field(self):
        """List of dicts with 'message' field extracts messages."""
        raw = [{"message": "error 1"}, {"message": "error 2"}]
        result = _normalize_message(raw)
        assert result == "error 1; error 2"

    def test_list_of_dicts_mixed_with_other_items(self):
        """List with mixed dicts and other items handles gracefully."""
        raw = [{"message": "error message"}, "simple string", 123]
        result = _normalize_message(raw)
        assert result == "error message; simple string; 123"

    def test_list_of_primitives(self):
        """List of primitive types converts to strings."""
        raw = ["error 1", "error 2", "error 3"]
        result = _normalize_message(raw)
        assert result == "error 1; error 2; error 3"

    def test_dict_without_message_key(self):
        """Dict without 'message' key converts to string representation."""
        raw = [{"error": "code", "detail": "info"}]
        result = _normalize_message(raw)
        assert result == "{'error': 'code', 'detail': 'info'}"

    def test_empty_list_returns_empty_string(self):
        """Empty list returns empty string."""
        result = _normalize_message([])
        assert result == ""

    def test_none_returns_string_none(self):
        """None returns string 'None'."""
        result = _normalize_message(None)
        assert result == "None"

    def test_integer_returns_string(self):
        """Integer converts to string."""
        result = _normalize_message(404)
        assert result == "404"

    def test_single_dict_with_message(self):
        """Single dict in list with message field."""
        raw = [
            {"message": "Failed to get device token: agentpass-lic daemon not ready"}
        ]
        result = _normalize_message(raw)
        assert result == "Failed to get device token: agentpass-lic daemon not ready"

    def test_complex_nested_structure(self):
        """Complex nested structure handles gracefully."""
        raw = [
            {"message": "Outer error", "nested": {"inner": "value"}},
            {"other": "key"},
            "plain text",
        ]
        result = _normalize_message(raw)
        assert result == "Outer error; {'other': 'key'}; plain text"

    def test_boolean_input(self):
        """Boolean input converts to string."""
        result = _normalize_message(True)
        assert result == "True"

    def test_empty_string(self):
        """Empty string returns empty string."""
        result = _normalize_message("")
        assert result == ""


# =============================================================================
# Additional tests to cover remaining uncovered lines
# =============================================================================


class TestConstructorEdgeCases:
    """Tests for constructor edge cases."""

    def test_none_credentials_raises_value_error(
        self, mock_repository, mock_connection_manager, mock_instance_router
    ):
        """Passing None credentials raises ValueError (line 153)."""
        with pytest.raises(ValueError, match="credentials is required"):
            LocalPaasService(
                credentials=None,
                repository=mock_repository,
                connection_manager=mock_connection_manager,
                instance_router=mock_instance_router,
                server_ip="test-instance",
                desktop_sandbox_plugin=MagicMock(),
            )

    def test_env_defaults_from_get_current_env_when_none(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """When env is None, it should call get_current_env()."""
        # We won't test the actual env value, just that it doesn't crash
        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
        )
        assert service._env is not None


class TestRouteCommandMachineNotFound:
    """Tests for _route_command when machine not found during TOCTOU re-query."""

    @pytest.mark.asyncio
    async def test_re_query_returns_none_raises_machine_not_found(
        self, local_paas_service, mock_repository
    ):
        """When DB re-query returns None, raises MACHINE_NOT_FOUND (line 241)."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="target-instance",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"
        assert "machine-001" in exc_info.value.message
        assert exc_info.value.context is not None
        assert exc_info.value.context["action"] == "test"


class TestRouteCommandWorkerRouter:
    """Tests for _route_command Phase 32 UDS forwarding path."""

    @pytest.mark.asyncio
    async def test_uds_forward_to_different_worker(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """D-01: UDS forward returns the raw mng response (no `data` unwrap)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        # is_connected is False (WebSocket not directly connected)
        mock_connection_manager.is_connected.return_value = False

        # Mock worker_router. route_info is a TypedDict (subscript access in
        # source); use a real dict so route_info["worker_pid"] yields the int.
        mock_worker_router = MagicMock()
        mock_route_info = {"worker_pid": 99999, "socket_path": "/tmp/worker.sock"}
        mock_worker_router.get_route_for_machine.return_value = mock_route_info
        mock_worker_router.forward_to_worker = AsyncMock()
        mock_worker_router.forward_to_worker.return_value = {
            "status": "success",
            "data": {"result": "ok"},
        }

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        result = await service._route_command(
            machine_id="machine-001",
            command={"action": "test", "params": {}},
            target_instance="test-instance",
        )

        # D-01: raw pass-through — caller receives the WHOLE mng response dict,
        # not just response["data"]. This aligns with internal_router.internal_forward
        # (HTTP path) and ConnectionManager.send_command (local path) contracts.
        assert result == {"status": "success", "data": {"result": "ok"}}
        mock_worker_router.forward_to_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_uds_forward_error_response_maps_to_device_creation_error_with_envelope_code(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """D-03/D-05: UDS envelope error maps to DeviceCreationError preserving envelope.error.

        Previously the test asserted MACHINE_NOT_CONNECTED because the explicit
        DeviceCreationError raised in the UDS try-block was caught by the
        generic `except Exception` and fell through. Per D-04 the new
        `except DeviceCreationError: raise` clause lets the typed error propagate
        with the original envelope.error code intact (D-03).
        """
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_route_info = {"worker_pid": 99999, "socket_path": "/tmp/worker.sock"}
        mock_worker_router.get_route_for_machine.return_value = mock_route_info
        mock_worker_router.forward_to_worker = AsyncMock()
        mock_worker_router.forward_to_worker.return_value = {
            "status": "error",
            "error": "WORKER_OFFLINE",
            "message": "Worker is offline",
        }

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="test-instance",
            )

        # D-03/D-05: envelope.error preserved on DeviceCreationError.error_code.
        assert exc_info.value.error_code == "WORKER_OFFLINE"
        assert exc_info.value.context is not None
        assert exc_info.value.context["original_error"] == "WORKER_OFFLINE"
        assert exc_info.value.context["envelope_message"] == "Worker is offline"
        assert exc_info.value.context["target_worker_pid"] == 99999
        assert exc_info.value.context["socket_path"] == "/tmp/worker.sock"
        assert exc_info.value.context["machine_id"] == "machine-001"
        assert exc_info.value.context["action"] == "test"

    @pytest.mark.asyncio
    async def test_uds_forward_arbitrary_envelope_error_propagates_with_original_code(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """D-04: envelope errors propagated as DeviceCreationError are NOT swallowed.

        Verifies that the ``except DeviceCreationError: raise`` clause in the
        UDS branch lets the typed error reach the caller with its original
        envelope.error code (e.g. CONTAINER_NOT_FOUND) — not WORKER_OFFLINE,
        not MACHINE_NOT_CONNECTED. Exercises the combined fix from PLAN 01
        (envelope error → DeviceCreationError) and PLAN 02 (DeviceCreationError
        pass-through), proving the chain works end-to-end.
        """
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_route_info = {"worker_pid": 99999, "socket_path": "/tmp/worker.sock"}
        mock_worker_router.get_route_for_machine.return_value = mock_route_info
        mock_worker_router.forward_to_worker = AsyncMock()
        mock_worker_router.forward_to_worker.return_value = {
            "status": "error",
            "error": "CONTAINER_NOT_FOUND",
            "message": "Container c1 not found in worker",
        }

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service._route_command(
                machine_id="machine-001",
                command={"action": "destroy", "params": {"container_id": "c1"}},
                target_instance="test-instance",
            )

        # Original envelope code preserved — NOT rewritten to WORKER_OFFLINE
        # or MACHINE_NOT_CONNECTED by the generic except Exception fall-through.
        assert exc_info.value.error_code == "CONTAINER_NOT_FOUND"
        assert "Container c1 not found in worker" in exc_info.value.message
        assert exc_info.value.context is not None
        assert exc_info.value.context["original_error"] == "CONTAINER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_uds_forward_generates_request_id_in_machine_id_pipe_uuid_format(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """D-06: _route_command UDS branch seeds command['request_id'] before forward_to_worker.

        Format must match `{machine_id}|{32-hex-char uuid}` so a single id threads
        through the four hops (upstream gen → envelope → uds_server → mng).
        """
        import re

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_route_info = {"worker_pid": 99999, "socket_path": "/tmp/worker.sock"}
        mock_worker_router.get_route_for_machine.return_value = mock_route_info
        mock_worker_router.forward_to_worker = AsyncMock()
        mock_worker_router.forward_to_worker.return_value = {
            "status": "success",
            "data": {"ok": True},
        }

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        await service._route_command(
            machine_id="machine-001",
            command={"action": "test", "params": {"x": 1}},
            target_instance="test-instance",
        )

        mock_worker_router.forward_to_worker.assert_called_once()
        forwarded_command = mock_worker_router.forward_to_worker.call_args.kwargs[
            "command"
        ]
        assert "request_id" in forwarded_command
        assert re.fullmatch(
            r"^machine-001\|[0-9a-f]{32}$", forwarded_command["request_id"]
        )

    @pytest.mark.asyncio
    async def test_uds_forward_preserves_caller_supplied_request_id(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """D-06: caller-supplied request_id is preserved (not overwritten)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_route_info = {"worker_pid": 99999, "socket_path": "/tmp/worker.sock"}
        mock_worker_router.get_route_for_machine.return_value = mock_route_info
        mock_worker_router.forward_to_worker = AsyncMock()
        mock_worker_router.forward_to_worker.return_value = {
            "status": "success",
            "data": {"ok": True},
        }

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        await service._route_command(
            machine_id="machine-001",
            command={
                "action": "test",
                "params": {},
                "request_id": "upstream-fixed-id-9999",
            },
            target_instance="test-instance",
        )

        forwarded_command = mock_worker_router.forward_to_worker.call_args.kwargs[
            "command"
        ]
        assert forwarded_command["request_id"] == "upstream-fixed-id-9999"

    @pytest.mark.asyncio
    async def test_uds_worker_offline_falls_through_to_not_connected(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Phase 32: WorkerOfflineError caught, falls through to MACHINE_NOT_CONNECTED (lines 359-364)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        from secbaas.community.core.service.paas.desktop.worker_router._exceptions import (
            WorkerOfflineError,
        )

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.side_effect = WorkerOfflineError(
            machine_id="machine-001",
            socket_path="/tmp/worker.sock",
            reason="Worker offline",
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="test-instance",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_uds_route_not_found_falls_through(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Phase 32: RouteNotFoundError caught, falls through to MACHINE_NOT_CONNECTED (lines 359-364)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        from secbaas.community.core.service.paas.desktop.worker_router._exceptions import (
            RouteNotFoundError,
        )

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.side_effect = RouteNotFoundError(
            machine_id="machine-001",
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="test-instance",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_uds_same_pid_race_condition_falls_through(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """CR-01: Same PID but is_connected False - race fall-through must hit the
        real ``[ROUTE_RACE]`` branch (not the generic-exception fallback).

        The previous version of this test used ``MagicMock()`` for
        ``route_info`` and accessed ``.worker_pid`` as an attribute, but the
        production code at ``_route_command`` reads it via subscript
        (``route_info["worker_pid"]``). ``MagicMock.__getitem__`` returns a
        fresh MagicMock — never equal to ``os.getpid()`` — so the race short-
        circuit was bypassed, the UDS-forward branch was entered, and the
        test only passed because ``forward_to_worker`` was an un-awaitable
        ``MagicMock`` whose ``await`` raised ``TypeError``, caught by the
        ``except Exception`` fallthrough. The ``[ROUTE_RACE]`` branch was
        never actually exercised.

        Now the test uses a real ``dict`` (matching the production
        ``WorkerRouteInfo`` shape) and configures ``forward_to_worker`` as
        an ``AsyncMock`` whose ``side_effect`` would fail the test if it
        were ever called.
        """
        import os

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False
        # CR-02: race fallthrough must clear stale route_info.
        mock_connection_manager.clear_stale_route_info = MagicMock()

        mock_worker_router = MagicMock()
        # CR-01: must be a real dict — production reads
        # route_info["worker_pid"] via subscript, not as attribute.
        mock_route_info = {
            "worker_pid": os.getpid(),  # Same PID — triggers race short-circuit
            "socket_path": "/tmp/worker.sock",
        }
        mock_worker_router.get_route_for_machine.return_value = mock_route_info
        # CR-01: forward_to_worker MUST NOT be called on the same-PID race
        # path. If the short-circuit regresses, this AssertionError will
        # surface inside the ``except Exception`` clause and the test would
        # silently pass on the wrong branch — so we ALSO assert_not_called
        # below to defeat that masking.
        mock_worker_router.forward_to_worker = AsyncMock(
            side_effect=AssertionError(
                "forward_to_worker must not be called on same-PID race fallthrough"
            )
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="test-instance",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_CONNECTED"
        # CR-01 regression guard: race short-circuit must run BEFORE forward.
        mock_worker_router.forward_to_worker.assert_not_called()
        # CR-02 regression guard: stale route_info MUST be cleared once.
        mock_connection_manager.clear_stale_route_info.assert_called_once_with(
            "machine-001"
        )

    @pytest.mark.asyncio
    async def test_uds_same_pid_race_clears_stale_route_info(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """CR-02: stale route_info MUST be cleared exactly once during the
        same-PID race fallthrough, so other workers can take over after
        their next heartbeat instead of being stuck routing back to this
        dead worker indefinitely.
        """
        import os

        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False
        mock_connection_manager.clear_stale_route_info = MagicMock()

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.return_value = {
            "worker_pid": os.getpid(),
            "socket_path": "/tmp/worker.sock",
        }
        mock_worker_router.forward_to_worker = AsyncMock(
            side_effect=AssertionError("forward must not run on race fallthrough")
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError):
            await service._route_command(
                machine_id="machine-007",
                command={"action": "test", "params": {}},
                target_instance="test-instance",
            )

        mock_connection_manager.clear_stale_route_info.assert_called_once_with(
            "machine-007"
        )

    @pytest.mark.asyncio
    async def test_uds_generic_exception_falls_through(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Phase 32: Generic exception in UDS block falls through (lines 368-370)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.side_effect = RuntimeError(
            "Unexpected error"
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await service._route_command(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
                target_instance="test-instance",
            )

        assert exc_info.value.error_code == "MACHINE_NOT_CONNECTED"


class TestCreateDeviceValidation:
    """Tests for create_device validation and error paths."""

    @pytest.mark.asyncio
    async def test_wrong_config_type_raises_value_error(self, local_paas_service):
        """Passing non-LocalCreateConfig raises ValueError (line 463)."""
        wrong_config = MagicMock()

        with pytest.raises(ValueError, match="Expected LocalCreateConfig"):
            await local_paas_service.create_device(wrong_config)

    @pytest.mark.asyncio
    async def test_machine_not_found_in_repository(
        self, local_paas_service, mock_repository, local_create_config
    ):
        """Machine not found in repository raises MACHINE_NOT_FOUND (line 474)."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_missing_container_id_in_response_raises_error(
        self,
        local_paas_service,
        mock_repository,
        mock_connection_manager,
        local_create_config,
    ):
        """Response without container_id raises INVALID_RESPONSE (line 512)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": {},
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.create_device(local_create_config)

        assert exc_info.value.error_code == "INVALID_RESPONSE"
        assert "Missing container_id" in exc_info.value.message


class TestExecuteCommandMachineNotFound:
    """Test execute_command outer query MACHINE_NOT_FOUND."""

    @pytest.mark.asyncio
    async def test_machine_not_found_in_outer_query(
        self, local_paas_service, mock_repository
    ):
        """Machine not found at outer query raises MACHINE_NOT_FOUND (line 610)."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.execute_command(
                "container--machine-001--user-001", "cmd"
            )

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"


class TestGetMachineInfoEdgeCases:
    """Edge case tests for get_machine_info."""

    @pytest.mark.asyncio
    async def test_connection_error_caught_and_converted(
        self, local_paas_service, mock_repository
    ):
        """ConnectionError in get_machine_info converts to MACHINE_OFFLINE (lines 876-885)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_record.last_heartbeat = None
        mock_repository.get_by_machine_id.side_effect = [
            mock_record,  # first call in get_machine_info
            mock_record,  # second in _route_command
            ConnectionError("WebSocket not connected"),  # third fails
        ]

        # The ConnectionError comes from send_command via _route_command
        mock_record_for_route = MagicMock()
        mock_record_for_route.connected_server_instance = "test-instance"
        mock_record_for_route.status = "ONLINE"
        mock_record_for_route.last_heartbeat = None

        # _route_command internally calls get_by_machine_id again
        mock_repository.get_by_machine_id.side_effect = [
            mock_record,  # outer get_machine_info
            mock_record_for_route,  # _route_command TOCTOU re-query
        ]

        # send_command raises ConnectionError
        mock_connection_manager = local_paas_service._connection_manager
        mock_connection_manager.send_command.side_effect = ConnectionError(
            "WebSocket not connected"
        )

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.get_machine_info("machine-001")

        assert exc_info.value.error_code == "MACHINE_OFFLINE"
        assert "registered but not connected" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_none_data_returns_empty_dict(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """None data field returns empty dict (line 903)."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_record.status = "ONLINE"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "success",
            "data": None,
        }

        result = await local_paas_service.get_machine_info("machine-001")

        assert result == {}


class TestValidateMountPathNormpath:
    """Tests for _validate_mount_path normalized path handling."""

    def test_normpath_handles_double_slash(self, local_paas_service):
        """Path with double slash normalized by normpath, then validated."""
        local_paas_service._validate_mount_path("/var//log")

    def test_rejects_dot_dot_embedded_in_path(self, local_paas_service):
        """Rejects path where '..' survives normpath embedded in component name."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/home/user../file")

        assert exc_info.value.error_code == "INVALID_PARAMS"
        assert ".." in exc_info.value.message

    def test_rejects_system_sbin(self, local_paas_service):
        """Rejects /sbin as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/sbin/app")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_rejects_system_boot(self, local_paas_service):
        """Rejects /boot as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/boot/config")

        assert exc_info.value.error_code == "INVALID_PARAMS"

    def test_rejects_system_dev(self, local_paas_service):
        """Rejects /dev as blocked system directory."""
        with pytest.raises(DeviceCreationError) as exc_info:
            local_paas_service._validate_mount_path("/dev/null")

        assert exc_info.value.error_code == "INVALID_PARAMS"


class TestDestroyOrphanWithLogging:
    """Tests for _destroy_orphan_with_logging method."""

    @pytest.mark.asyncio
    async def test_exception_during_destroy_is_logged(
        self, local_paas_service, mock_repository
    ):
        """Exception during orphan destruction is caught and logged (lines 1350-1356)."""
        # Setup: make destroy_device fail
        mock_repository.get_by_machine_id.return_value = None

        # Directly call _destroy_orphan_with_logging - it catches exceptions
        # destroy_device will raise DeviceCreationError because machine not found
        result = await local_paas_service._destroy_orphan_with_logging(
            paas_device_id="container--machine-001--user-001",
            triple_id="container--machine-001--user-001",
        )

        # Should not raise - exception is caught and logged
        assert result is None


class TestHandleHeartbeatContainers:
    """Tests for handle_heartbeat_containers method."""

    @pytest.fixture
    def service_with_device_repo_for_heartbeat(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """Create service with device_repository for heartbeat container tests."""
        mock_device_repo = MagicMock()
        return (
            LocalPaasService(
                credentials=local_credentials,
                repository=mock_repository,
                connection_manager=mock_connection_manager,
                instance_router=mock_instance_router,
                server_ip="test-instance",
                desktop_sandbox_plugin=MagicMock(),
                env="test",
                device_template_repository=mock_device_template_repository,
                device_repository=mock_device_repo,
            ),
            mock_repository,
            mock_device_repo,
        )

    @pytest.mark.asyncio
    async def test_no_device_repository_returns_early(self, local_paas_service):
        """Returns early when device_repository is None (line 1386-1391)."""
        result = await local_paas_service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-1", "status": "ok"},
            ],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_missing_container_id(
        self, service_with_device_repo_for_heartbeat
    ):
        """Skips items without container_id (line 1398-1403)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"status": "ok"},  # No container_id
            ],
        )
        assert result is None
        mock_device_repo.get_by_provider_device_id_prefix.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_container_triggers_deletion(
        self, service_with_device_repo_for_heartbeat
    ):
        """Orphan container (no device record) triggers deletion (lines 1413-1423)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        service.destroy_device = AsyncMock(return_value=True)
        mock_device_repo.get_by_provider_device_id_prefix.return_value = None

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-orphan", "status": "ok"},
            ],
        )
        assert result is None
        mock_device_repo.get_by_provider_device_id_prefix.assert_called_once()

    @pytest.mark.asyncio
    async def test_released_device_treated_as_orphan(
        self, service_with_device_repo_for_heartbeat
    ):
        """RELEASED device triggers orphan deletion (lines 1426-1435)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        from secbaas.community.api.device_manage import DeviceStatus

        service.destroy_device = AsyncMock(return_value=True)
        released_device = MagicMock()
        released_device.status = DeviceStatus.RELEASED.value
        mock_device_repo.get_by_provider_device_id_prefix.return_value = released_device

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-released", "status": "ok"},
            ],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_ok_status_updates_device_to_active_only_if_changed(
        self, service_with_device_repo_for_heartbeat
    ):
        """ok status updates device to ACTIVE when status differs (lines 1437-1453)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        from secbaas.community.api.device_manage import DeviceStatus

        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        device.status = DeviceStatus.OFFLINE.value  # Different from ACTIVE
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-ok", "status": "ok"},
            ],
        )
        assert result is None
        mock_device_repo.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_ok_status_does_not_update_if_already_active(
        self, service_with_device_repo_for_heartbeat
    ):
        """ok status does NOT update device when already ACTIVE (line 1443)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        from secbaas.community.api.device_manage import DeviceStatus

        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        device.status = DeviceStatus.ACTIVE.value  # Already ACTIVE
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-ok", "status": "ok"},
            ],
        )
        assert result is None
        # update_status should NOT be called because status hasn't changed
        mock_device_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ok_status_sets_device_to_offline(
        self, service_with_device_repo_for_heartbeat
    ):
        """Non-ok status sets device to OFFLINE (line 1439)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        from secbaas.community.api.device_manage import DeviceStatus

        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        device.status = DeviceStatus.ACTIVE.value
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-error", "status": "error"},
            ],
        )
        assert result is None
        mock_device_repo.update_status.assert_called_once()
        call_kwargs = mock_device_repo.update_status.call_args.kwargs
        assert call_kwargs["status"] == DeviceStatus.OFFLINE.value

    @pytest.mark.asyncio
    async def test_exception_during_processing_is_caught_and_continues(
        self, service_with_device_repo_for_heartbeat
    ):
        """Exception during single item processing logs and continues (lines 1467-1475)."""
        service, mock_repository, mock_device_repo = (
            service_with_device_repo_for_heartbeat
        )

        service.destroy_device = AsyncMock(return_value=True)
        # First item will fail, second should still be processed
        mock_device_repo.get_by_provider_device_id_prefix.side_effect = [
            RuntimeError("Database error"),  # First item fails
            None,  # Second item succeeds (orphan)
        ]

        result = await service.handle_heartbeat_containers(
            machine_id="machine-001",
            user_id="user-001",
            bot_list=[
                {"container_id": "cont-fail", "status": "ok"},
                {"container_id": "cont-ok", "status": "ok"},
            ],
        )
        assert result is None
        # Both items should have been attempted
        assert mock_device_repo.get_by_provider_device_id_prefix.call_count == 2


class TestHandleContainerReadySuccess:
    """Tests for _handle_container_ready success path."""

    @pytest.fixture
    def service_for_callback_success(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
    ):
        """Create service with all repos for callback success path."""
        mock_device_repo = MagicMock()
        mock_publish_record_repo = MagicMock()

        return (
            LocalPaasService(
                credentials=local_credentials,
                repository=mock_repository,
                connection_manager=mock_connection_manager,
                instance_router=mock_instance_router,
                server_ip="test-instance",
                desktop_sandbox_plugin=MagicMock(),
                env="test",
                device_template_repository=mock_device_template_repository,
                device_repository=mock_device_repo,
                publish_record_repository=mock_publish_record_repo,
            ),
            mock_repository,
            mock_device_repo,
            mock_publish_record_repo,
        )

    @pytest.mark.asyncio
    async def test_successful_callback_sends_to_publish_service(
        self, service_for_callback_success
    ):
        """Full success callback path sends to DefaultPublishService (lines 1557-1580)."""
        service, mock_repository, mock_device_repo, mock_publish_record_repo = (
            service_for_callback_success
        )

        # Setup: machine found
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        # Setup: device found
        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        # Setup: publish record found with CREATED status
        record = MagicMock()
        record.result_status = "PROCESSING"
        record.publish_id = 100
        mock_publish_record_repo.get_latest_processing_record_by_device.return_value = (
            record
        )

        # Mock get_container so DI container is not needed
        mock_publish_svc = MagicMock()
        mock_publish_svc.handle_device_callback = AsyncMock(
            return_value={"status": "ok"}
        )
        mock_container = MagicMock()
        mock_container.services.publish_service.return_value = mock_publish_svc
        with mock.patch(
            "secbaas.community.bootstrap.get_container",
            return_value=mock_container,
        ):
            result = await service._handle_container_ready(
                machine_id="machine-001",
                params={"container_id": "container-001"},
            )

            assert result is None
            mock_publish_svc.handle_device_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_exception_is_handled(self, service_for_callback_success):
        """Exception during callback handling is caught and logged (line 1578-1584)."""
        service, mock_repository, mock_device_repo, mock_publish_record_repo = (
            service_for_callback_success
        )

        # Setup: machine found
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        # Setup: device found
        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        # Setup: publish record found with CREATED status
        record = MagicMock()
        record.result_status = "PROCESSING"
        record.publish_id = 100
        mock_publish_record_repo.get_latest_processing_record_by_device.return_value = (
            record
        )

        # Mock get_container with a raising handle_device_callback
        mock_publish_svc = MagicMock()
        mock_publish_svc.handle_device_callback = AsyncMock(
            side_effect=RuntimeError("Callback failed")
        )
        mock_container = MagicMock()
        mock_container.services.publish_service.return_value = mock_publish_svc
        with mock.patch(
            "secbaas.community.bootstrap.get_container",
            return_value=mock_container,
        ):
            result = await service._handle_container_ready(
                machine_id="machine-001",
                params={"container_id": "container-001"},
            )

            assert result is None
            mock_publish_svc.handle_device_callback.assert_called_once()


class TestProcessPublishCallbackForDevice:
    """Tests for LocalPaasService._process_publish_callback_for_device (Phase 34).

    Covers the four behavioral branches of the shared worker:
        1. publish_record_repository is None -> [PUBLISH_CALLBACK_ERROR] + return
        2. get_latest_processing_record_by_device returns None -> [PUBLISH_CALLBACK_SKIP]
        3. success path -> DefaultPublishService.handle_device_callback awaited
           with source-keyed stdout (heartbeat / container_ready)
        4. internal exception -> swallowed + [PUBLISH_CALLBACK_ERROR] with exc_info

    Phase 34 WR-01: There is no separate DUPLICATE branch in this worker.
    The repository's get_latest_processing_record_by_device SQL hardcodes
    WHERE result_status='CREATED', so any record returned is already CREATED
    by contract; concurrent-trigger idempotency lives one layer down in
    DefaultPublishService.handle_device_callback (optimistic lock via
    update_result_if_created).

    All cases must not raise.
    """

    def _make_device(self, *, device_id=1, uuid="dev-001", tenant="test-tenant"):
        device = MagicMock()
        device.id = device_id
        device.device_uuid = uuid
        device.tenant = tenant
        return device

    def _make_service(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        *,
        publish_record_repository,
    ):
        return LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=MagicMock(),
            publish_record_repository=publish_record_repository,
        )

    @pytest.mark.asyncio
    async def test_repository_none_logs_error_and_returns(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """Branch 1: publish_record_repository=None -> warning + early return."""
        # Build service explicitly with publish_record_repository=None
        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            publish_record_repository=None,
        )

        # Patch the lazy-imported callback so we can assert it was NOT called
        fake_handle = AsyncMock(return_value={"ok": True})
        monkeypatch.setattr(
            "secbaas.community.core.service.publish_manage.DefaultPublishService.handle_device_callback",
            fake_handle,
        )

        device = self._make_device()

        caplog.set_level("WARNING")
        # Ensure logger propagates to the root logger so caplog captures it
        import logging

        logging.getLogger("local_paas_service").propagate = True
        await service._process_publish_callback_for_device(device, source="heartbeat")

        assert any(
            "[PUBLISH_CALLBACK_ERROR]" in msg and "dev-001" in msg
            for msg in caplog.messages
        )
        fake_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_processing_record_logs_skip_returns(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """Branch 2: no CREATED record -> [PUBLISH_CALLBACK_SKIP] info log + return."""
        mock_publish_repo = MagicMock()
        mock_publish_repo.get_latest_processing_record_by_device.return_value = None

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            publish_record_repository=mock_publish_repo,
        )

        fake_handle = AsyncMock(return_value={"ok": True})
        monkeypatch.setattr(
            "secbaas.community.core.service.publish_manage.DefaultPublishService.handle_device_callback",
            fake_handle,
        )

        device = self._make_device()

        caplog.set_level("INFO")
        await service._process_publish_callback_for_device(device, source="heartbeat")

        # Repository was queried with correct kwargs
        mock_publish_repo.get_latest_processing_record_by_device.assert_called_once_with(
            device_id=1, tenant="test-tenant", env="test"
        )
        # SKIP log emitted
        assert any("[PUBLISH_CALLBACK_SKIP]" in r.message for r in caplog.records)
        # Callback service NOT called
        fake_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_path_sends_callback_with_heartbeat_stdout(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """Branch 4 (heartbeat): success path sends callback with heartbeat stdout."""
        record = MagicMock()
        record.result_status = "PROCESSING"
        record.publish_id = 100

        mock_publish_repo = MagicMock()
        mock_publish_repo.get_latest_processing_record_by_device.return_value = record

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            publish_record_repository=mock_publish_repo,
        )

        fake_handle = AsyncMock(return_value={"ok": True})
        mock_publish_svc = MagicMock()
        mock_publish_svc.handle_device_callback = fake_handle
        mock_container = MagicMock()
        mock_container.services.publish_service.return_value = mock_publish_svc

        device = self._make_device()

        caplog.set_level("INFO")
        with mock.patch(
            "secbaas.community.bootstrap.get_container", return_value=mock_container
        ):
            await service._process_publish_callback_for_device(
                device, source="heartbeat"
            )

        # Callback invoked exactly once
        fake_handle.assert_awaited_once()
        callback = fake_handle.call_args.args[0]
        # Field-by-field assertions on the DeviceCallbackRequest payload
        assert callback.stdout == "Local platform: heartbeat container_ready processed"
        assert callback.result_status == "SUCCESS"
        assert callback.event_type == "start"
        assert callback.exit_code == 0
        assert callback.stderr == ""
        assert callback.tenant == "test-tenant"
        assert callback.device_uuid == "dev-001"
        assert callback.publish_id == 100

        # SUCCESS log emitted
        assert any("[PUBLISH_CALLBACK_SUCCESS]" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_success_path_uses_container_ready_stdout_when_source_is_container_ready(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """Branch 4 (container_ready): success path uses container_ready stdout."""
        record = MagicMock()
        record.result_status = "PROCESSING"
        record.publish_id = 200

        mock_publish_repo = MagicMock()
        mock_publish_repo.get_latest_processing_record_by_device.return_value = record

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            publish_record_repository=mock_publish_repo,
        )

        fake_handle = AsyncMock(return_value={"ok": True})
        mock_publish_svc = MagicMock()
        mock_publish_svc.handle_device_callback = fake_handle
        mock_container = MagicMock()
        mock_container.services.publish_service.return_value = mock_publish_svc

        device = self._make_device()

        caplog.set_level("INFO")
        with mock.patch(
            "secbaas.community.bootstrap.get_container", return_value=mock_container
        ):
            await service._process_publish_callback_for_device(
                device, source="container_ready"
            )

        fake_handle.assert_awaited_once()
        callback = fake_handle.call_args.args[0]
        assert callback.stdout == "Local platform: container_ready callback processed"
        assert callback.publish_id == 200
        # SUCCESS log emitted
        assert any("[PUBLISH_CALLBACK_SUCCESS]" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_exception_is_swallowed_and_logged(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """Branch 5: handle_device_callback raises -> swallowed + ERROR log."""
        record = MagicMock()
        record.result_status = "PROCESSING"
        record.publish_id = 300

        mock_publish_repo = MagicMock()
        mock_publish_repo.get_latest_processing_record_by_device.return_value = record

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            publish_record_repository=mock_publish_repo,
        )

        fake_handle = AsyncMock(side_effect=RuntimeError("boom"))
        mock_publish_svc = MagicMock()
        mock_publish_svc.handle_device_callback = fake_handle
        mock_container = MagicMock()
        mock_container.services.publish_service.return_value = mock_publish_svc

        device = self._make_device()

        caplog.set_level("ERROR")
        # Must NOT raise
        with mock.patch(
            "secbaas.community.bootstrap.get_container", return_value=mock_container
        ):
            result = await service._process_publish_callback_for_device(
                device, source="heartbeat"
            )
        assert result is None

        # ERROR log emitted and contains RuntimeError marker
        assert any(
            "[PUBLISH_CALLBACK_ERROR]" in r.message and "RuntimeError" in r.message
            for r in caplog.records
        )


class TestHeartbeatPublishTrigger:
    """Tests for Plan 34-02 heartbeat OFFLINE->ACTIVE publish callback trigger.

    Asserts the wiring in handle_heartbeat_containers:

        if device.status != new_status.value:
            ...update_status...
            if new_status == DeviceStatus.ACTIVE and
               self._publish_record_repository is not None:
                asyncio.create_task(
                    self._process_publish_callback_for_device(device, source="heartbeat")
                )
                logger.info("[HEARTBEAT_PUBLISH_TRIGGERED] ...")

    Public method is patched at the class level to isolate the trigger logic
    from the worker logic (already covered by TestProcessPublishCallbackForDevice).
    """

    def _make_device(self, *, status, uuid="dev-001", device_id=1):
        device = MagicMock()
        device.id = device_id
        device.device_uuid = uuid
        device.tenant = "test-tenant"
        device.status = status
        return device

    def _make_service(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        *,
        device,
        publish_record_repository,
    ):
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device
        return LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=publish_record_repository,
        )

    @pytest.mark.asyncio
    async def test_active_transition_schedules_publish_task(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """OFFLINE -> ACTIVE transition schedules _process_publish_callback_for_device."""
        device = self._make_device(status="OFFLINE")
        mock_publish_repo = MagicMock()

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            device=device,
            publish_record_repository=mock_publish_repo,
        )

        # Patch the shared worker at the class level so asyncio.create_task
        # schedules a coroutine produced by AsyncMock, which awaits to None.
        fake_worker = AsyncMock(return_value=None)
        monkeypatch.setattr(
            LocalPaasService, "_process_publish_callback_for_device", fake_worker
        )

        caplog.set_level("INFO")
        await service.handle_heartbeat_containers(
            machine_id="m1",
            user_id="user-001",
            bot_list=[{"container_id": "c1", "status": "ok"}],
        )

        # Let the create_task'd coroutine run to completion
        await asyncio.sleep(0)

        # Worker invoked exactly once with the expected args
        fake_worker.assert_awaited_once()
        call_args = fake_worker.call_args
        # Trigger uses keyword for source; device is positional (self goes via descriptor)
        # AsyncMock bound to the class records (instance, device) as positional args
        assert call_args.kwargs.get("source") == "heartbeat"
        # The mocked attribute is set on the class; calling via self injects the
        # instance as the first positional argument. The device arg is positional.
        assert device in call_args.args

        # [HEARTBEAT_PUBLISH_TRIGGERED] log emitted
        assert any("[HEARTBEAT_PUBLISH_TRIGGERED]" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_status_change_does_not_trigger(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """ACTIVE -> ACTIVE (no change) does NOT schedule the worker."""
        device = self._make_device(status="ACTIVE")
        mock_publish_repo = MagicMock()

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            device=device,
            publish_record_repository=mock_publish_repo,
        )

        fake_worker = AsyncMock(return_value=None)
        monkeypatch.setattr(
            LocalPaasService, "_process_publish_callback_for_device", fake_worker
        )

        caplog.set_level("INFO")
        await service.handle_heartbeat_containers(
            machine_id="m1",
            user_id="user-001",
            bot_list=[{"container_id": "c1", "status": "ok"}],
        )
        await asyncio.sleep(0)

        fake_worker.assert_not_called()
        assert not any(
            "[HEARTBEAT_PUBLISH_TRIGGERED]" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_non_active_new_status_does_not_trigger(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
    ):
        """ACTIVE -> OFFLINE transition does NOT schedule the worker (D-01)."""
        device = self._make_device(status="ACTIVE")
        mock_publish_repo = MagicMock()

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            device=device,
            publish_record_repository=mock_publish_repo,
        )

        fake_worker = AsyncMock(return_value=None)
        monkeypatch.setattr(
            LocalPaasService, "_process_publish_callback_for_device", fake_worker
        )

        # status="error" -> new_status = OFFLINE (not ACTIVE); transition
        # fires update_status but must NOT schedule the publish worker.
        await service.handle_heartbeat_containers(
            machine_id="m1",
            user_id="user-001",
            bot_list=[{"container_id": "c1", "status": "error"}],
        )
        await asyncio.sleep(0)

        fake_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_record_repository_none_does_not_trigger(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """publish_record_repository=None blocks worker scheduling (D-03)."""
        device = self._make_device(status="OFFLINE")

        service = self._make_service(
            local_credentials,
            mock_repository,
            mock_connection_manager,
            mock_instance_router,
            mock_device_template_repository,
            device=device,
            publish_record_repository=None,
        )

        fake_worker = AsyncMock(return_value=None)
        monkeypatch.setattr(
            LocalPaasService, "_process_publish_callback_for_device", fake_worker
        )

        caplog.set_level("INFO")
        await service.handle_heartbeat_containers(
            machine_id="m1",
            user_id="user-001",
            bot_list=[{"container_id": "c1", "status": "ok"}],
        )
        await asyncio.sleep(0)

        fake_worker.assert_not_called()
        assert not any(
            "[HEARTBEAT_PUBLISH_TRIGGERED]" in r.message for r in caplog.records
        )


class TestHandleContainerReadyDelegation:
    """Tests for Plan 34-02 _handle_container_ready delegation to the shared worker.

    Asserts that the happy path inside _handle_container_ready ends with:

        logger.info("[CALLBACK_CONTAINER_READY_TRIGGERED] ...")
        await self._process_publish_callback_for_device(device, source="container_ready")

    and that the device-not-found branch (D-12) still logs
    [CALLBACK_CONTAINER_READY] ERROR without delegating to the worker.
    """

    def _make_device(self):
        device = MagicMock()
        device.id = 1
        device.device_uuid = "dev-001"
        device.tenant = "test-tenant"
        return device

    @pytest.mark.asyncio
    async def test_delegates_to_shared_method(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """Happy path awaits _process_publish_callback_for_device(device, 'container_ready')."""
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        device = self._make_device()
        mock_device_repo = MagicMock()
        mock_device_repo.get_by_provider_device_id_prefix.return_value = device

        mock_publish_repo = MagicMock()

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=mock_publish_repo,
        )

        # Patch the shared worker at the class level. Because container_ready
        # uses `await self.<worker>(...)` (not create_task), no sleep is needed.
        fake_worker = AsyncMock(return_value=None)
        monkeypatch.setattr(
            LocalPaasService, "_process_publish_callback_for_device", fake_worker
        )

        caplog.set_level("INFO")
        result = await service._handle_container_ready(
            machine_id="m1",
            params={"container_id": "c1"},
        )

        assert result is None
        fake_worker.assert_awaited_once()
        call_args = fake_worker.call_args
        assert call_args.kwargs.get("source") == "container_ready"
        assert device in call_args.args

        # Trigger log emitted
        assert any(
            "[CALLBACK_CONTAINER_READY_TRIGGERED]" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_device_not_found_does_not_delegate(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
        mock_device_template_repository,
        monkeypatch,
        caplog,
    ):
        """device-not-found (D-12) logs ERROR without invoking the worker."""
        machine_record = MagicMock()
        machine_record.user_id = "user-001"
        mock_repository.get_by_machine_id.return_value = machine_record

        mock_device_repo = MagicMock()
        mock_device_repo.get_by_provider_device_id_prefix.return_value = None

        mock_publish_repo = MagicMock()

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            device_template_repository=mock_device_template_repository,
            device_repository=mock_device_repo,
            publish_record_repository=mock_publish_repo,
        )

        fake_worker = AsyncMock(return_value=None)
        monkeypatch.setattr(
            LocalPaasService, "_process_publish_callback_for_device", fake_worker
        )

        caplog.set_level("ERROR")
        result = await service._handle_container_ready(
            machine_id="m1",
            params={"container_id": "c1"},
        )

        assert result is None
        fake_worker.assert_not_called()
        # [CALLBACK_CONTAINER_READY] ERROR (D-12) emitted; NOT _TRIGGERED variant
        assert any(
            "[CALLBACK_CONTAINER_READY]" in r.message
            and "[CALLBACK_CONTAINER_READY_TRIGGERED]" not in r.message
            for r in caplog.records
        )


class TestDispatchToLocalConnection:
    """Tests for LocalPaasService.dispatch_to_local_connection.

    Exercises the shared same-instance routing decision now used by both
    ``_route_command`` (same-instance branch) and
    ``adapters.web.routers.internal_router.internal_forward``. The pre-existing
    ``TestRouteCommandWorkerRouter`` class still covers the
    ``_route_command``-level exception-mapping behaviour; these tests cover
    the dispatcher's own raw-dict contract directly.
    """

    @pytest.mark.asyncio
    async def test_same_process_uses_send_command_when_no_request_id(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """is_connected=True + no request_id → ConnectionManager.send_command."""
        mock_connection_manager.is_connected.return_value = True
        mock_connection_manager.send_command = AsyncMock(
            return_value={"status": "ok", "data": {"out": "hello"}}
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-001",
            command={"action": "test", "params": {}},
        )

        assert result == {"status": "ok", "data": {"out": "hello"}}
        mock_connection_manager.send_command.assert_awaited_once_with(
            "machine-001", {"action": "test", "params": {}}
        )

    @pytest.mark.asyncio
    async def test_same_process_uses_send_command_with_request_id_when_supplied(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Caller-supplied request_id threads through send_command_with_request_id."""
        mock_connection_manager.is_connected.return_value = True
        mock_connection_manager.send_command_with_request_id = AsyncMock(
            return_value={"status": "ok", "data": {"ok": True}}
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-001",
            command={"action": "test", "params": {}},
            request_id="caller-supplied-id",
        )

        assert result == {"status": "ok", "data": {"ok": True}}
        # The send_command (no-id) variant must NOT be called when request_id
        # was supplied — the receiver needs the correlation id to thread
        # through to mng for cross-instance tracing.
        mock_connection_manager.send_command_with_request_id.assert_awaited_once_with(
            "machine-001",
            {"action": "test", "params": {}},
            "caller-supplied-id",
        )

    @pytest.mark.asyncio
    async def test_same_process_timeout_propagates(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Same-process TimeoutError must propagate (异常只在外层包装 contract).

        Outer callers (_route_command, internal_forward) wrap to their own
        contract — DeviceCreationError or HTTP error dict respectively.
        Catching here would silently change _route_command's exception
        surface for existing callers.
        """
        mock_connection_manager.is_connected.return_value = True
        mock_connection_manager.send_command = AsyncMock(side_effect=TimeoutError())

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
        )

        with pytest.raises(TimeoutError):
            await service.dispatch_to_local_connection(
                machine_id="machine-001",
                command={"action": "test", "params": {}},
            )

    @pytest.mark.asyncio
    async def test_cross_process_forwards_via_uds_and_returns_raw_response(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """is_connected=False + worker_pid != self → UDS forward, raw pass-through."""
        import os

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.return_value = {
            "worker_pid": os.getpid() + 1,  # different from current
            "socket_path": "/tmp/sibling.sock",
        }
        mock_worker_router.forward_to_worker = AsyncMock(
            return_value={"status": "success", "data": {"result": "ok"}}
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-001",
            command={"action": "test", "params": {"x": 1}},
        )

        assert result == {"status": "success", "data": {"result": "ok"}}
        mock_worker_router.forward_to_worker.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cross_process_envelope_error_passthrough_with_routing_diagnostics(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """UDS envelope error returned as-is, augmented with sender-side routing context.

        The augmentation lets _route_command populate DeviceCreationError.context
        (target_worker_pid, socket_path) without re-fetching route_info, while
        internal_forward sees the same dict shape it already returns.
        """
        import os

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.return_value = {
            "worker_pid": os.getpid() + 1,
            "socket_path": "/tmp/sibling.sock",
        }
        mock_worker_router.forward_to_worker = AsyncMock(
            return_value={
                "status": "error",
                "error": "WORKER_OFFLINE",
                "message": "Worker is offline",
            }
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-001",
            command={"action": "test", "params": {}},
        )

        assert result["status"] == "error"
        assert result["error"] == "WORKER_OFFLINE"
        assert result["message"] == "Worker is offline"
        assert result["data"]["target_worker_pid"] == os.getpid() + 1
        assert result["data"]["socket_path"] == "/tmp/sibling.sock"

    @pytest.mark.asyncio
    async def test_cross_process_request_id_threading_preserves_caller_supplied(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """Priority: command['request_id'] > argument request_id > generated."""
        import os

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.return_value = {
            "worker_pid": os.getpid() + 1,
            "socket_path": "/tmp/sibling.sock",
        }
        mock_worker_router.forward_to_worker = AsyncMock(
            return_value={"status": "success", "data": {}}
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        # Existing command["request_id"] wins over argument
        cmd_a = {
            "action": "t",
            "params": {},
            "request_id": "existing-in-command",
        }
        await service.dispatch_to_local_connection(
            machine_id="m1", command=cmd_a, request_id="arg-id"
        )
        forwarded_a = mock_worker_router.forward_to_worker.call_args.kwargs["command"]
        assert forwarded_a["request_id"] == "existing-in-command"

        # When command lacks request_id, the argument is used
        cmd_b = {"action": "t", "params": {}}
        await service.dispatch_to_local_connection(
            machine_id="m1", command=cmd_b, request_id="arg-id"
        )
        forwarded_b = mock_worker_router.forward_to_worker.call_args.kwargs["command"]
        assert forwarded_b["request_id"] == "arg-id"

    @pytest.mark.asyncio
    async def test_same_pid_race_clears_route_info_and_returns_not_connected(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """CR-02 race: worker_pid == self but is_connected False.

        Must clear stale route_info so other workers can claim the machine
        on their next heartbeat, and must NOT call forward_to_worker (would
        deadlock on self-connect — D-11).
        """
        import os

        mock_connection_manager.is_connected.return_value = False
        mock_connection_manager.clear_stale_route_info = MagicMock()

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.return_value = {
            "worker_pid": os.getpid(),  # same as current — triggers race
            "socket_path": "/tmp/self.sock",
        }
        mock_worker_router.forward_to_worker = AsyncMock(
            side_effect=AssertionError("forward must not run on same-PID race")
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-race",
            command={"action": "test", "params": {}},
        )

        assert result["status"] == "error"
        assert result["error"] == "MACHINE_NOT_CONNECTED"
        mock_connection_manager.clear_stale_route_info.assert_called_once_with(
            "machine-race"
        )
        mock_worker_router.forward_to_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_not_found_falls_through_to_not_connected(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """RouteNotFoundError from worker_router → MACHINE_NOT_CONNECTED dict (not raised)."""
        from secbaas.community.core.service.paas.desktop.worker_router._exceptions import (
            RouteNotFoundError,
        )

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.side_effect = RouteNotFoundError(
            "machine-x"
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-x",
            command={"action": "test", "params": {}},
        )

        assert result["status"] == "error"
        assert result["error"] == "MACHINE_NOT_CONNECTED"
        assert result["data"]["worker_router_available"] is True

    @pytest.mark.asyncio
    async def test_worker_offline_error_falls_through_to_not_connected(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """WorkerOfflineError from get_route_for_machine → MACHINE_NOT_CONNECTED dict."""
        from secbaas.community.core.service.paas.desktop.worker_router._exceptions import (
            WorkerOfflineError,
        )

        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.side_effect = WorkerOfflineError(
            machine_id="machine-x",
            socket_path="/tmp/dead.sock",
            reason="connection_refused",
        )

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-x",
            command={"action": "test", "params": {}},
        )

        assert result["status"] == "error"
        assert result["error"] == "MACHINE_NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_cancellation_propagates(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """asyncio.CancelledError must never be swallowed."""
        mock_connection_manager.is_connected.return_value = False

        mock_worker_router = MagicMock()
        mock_worker_router.get_route_for_machine.side_effect = asyncio.CancelledError()

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=mock_worker_router,
        )

        with pytest.raises(asyncio.CancelledError):
            await service.dispatch_to_local_connection(
                machine_id="m1",
                command={"action": "test", "params": {}},
            )

    @pytest.mark.asyncio
    async def test_no_worker_router_falls_through_to_not_connected(
        self,
        local_credentials,
        mock_repository,
        mock_connection_manager,
        mock_instance_router,
    ):
        """When worker_router not wired, fall through directly to MACHINE_NOT_CONNECTED."""
        mock_connection_manager.is_connected.return_value = False

        service = LocalPaasService(
            credentials=local_credentials,
            repository=mock_repository,
            connection_manager=mock_connection_manager,
            instance_router=mock_instance_router,
            server_ip="test-instance",
            desktop_sandbox_plugin=MagicMock(),
            env="test",
            worker_router=None,  # explicitly not wired
        )

        result = await service.dispatch_to_local_connection(
            machine_id="machine-x",
            command={"action": "test", "params": {}},
        )

        assert result["status"] == "error"
        assert result["error"] == "MACHINE_NOT_CONNECTED"
        assert result["data"]["worker_router_available"] is False


class TestOpenFolder:
    """Tests for open_folder method."""

    @pytest.mark.asyncio
    async def test_success_same_instance_with_folder_path(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Successful open_folder with folder_path returns True."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {"status": "success"}

        result = await local_paas_service.open_folder(
            "container--machine-001--user-001", "/workspace/project"
        )

        assert result is True

        # Verify correct command was sent
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["action"] == "open_folder"
        assert command["params"]["container_id"] == "container"
        assert command["params"]["folder_path"] == "/workspace/project"

    @pytest.mark.asyncio
    async def test_success_same_instance_without_folder_path(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """Successful open_folder without folder_path excludes key from params."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {"status": "success"}

        result = await local_paas_service.open_folder(
            "container--machine-001--user-001"
        )

        assert result is True

        # Verify correct command was sent
        call_args = mock_connection_manager.send_command.call_args
        command = call_args[0][1]
        assert command["action"] == "open_folder"
        assert command["params"]["container_id"] == "container"
        assert "folder_path" not in command["params"]

    @pytest.mark.asyncio
    async def test_open_folder_failed_error_from_mng(
        self, local_paas_service, mock_repository, mock_connection_manager
    ):
        """OPEN_FOLDER_FAILED error from mng raises DeviceCreationError."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "test-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_connection_manager.send_command.return_value = {
            "status": "error",
            "error": "OPEN_FOLDER_FAILED",
            "message": "Open folder failed",
        }

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.open_folder("container--machine-001--user-001")

        assert exc_info.value.error_code == "OPEN_FOLDER_FAILED"

    @pytest.mark.asyncio
    async def test_machine_not_found_raises_error(
        self, local_paas_service, mock_repository
    ):
        """Raises MACHINE_NOT_FOUND when machine not in repository."""
        mock_repository.get_by_machine_id.return_value = None

        with pytest.raises(DeviceCreationError) as exc_info:
            await local_paas_service.open_folder("container--machine-001--user-001")

        assert exc_info.value.error_code == "MACHINE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_success_cross_instance(
        self, local_paas_service, mock_repository, mock_instance_router
    ):
        """Successful open_folder on cross instance via InstanceRouter."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = "other-instance"
        mock_repository.get_by_machine_id.return_value = mock_record

        mock_instance_router.route_to_instance.return_value = {"status": "success"}

        result = await local_paas_service.open_folder(
            "container--machine-001--user-001"
        )

        assert result is True

        mock_instance_router.route_to_instance.assert_called_once()
        call_args = mock_instance_router.route_to_instance.call_args
        assert call_args.kwargs["target_instance"] == "other-instance"
        assert call_args.kwargs["action"] == "open_folder"
        assert call_args.kwargs["params"]["container_id"] == "container"
