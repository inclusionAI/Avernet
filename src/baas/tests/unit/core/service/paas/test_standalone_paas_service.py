"""Unit tests for StandalonePaasService (DOCKER PaaS adapter).

Post-Plan-11-04 refactoring: StandalonePaasService now delegates to
DockerSandboxPlugin/DockerSandbox. Tests use a mock plugin instead of
direct docker.DockerClient mocks. Replaced methods:
  - _map_docker_error → moved to RealDockerSandboxPlugin (Plan 02)
    tested via contract tests (Plan 07)
  - _poll_health → moved to RealDockerSandboxPlugin (create_device internal)
  - Constructor fail-fast ping → moved to RealDockerSandboxPlugin._get_client()
  - All sync wrapper methods → RealDockerSandboxPlugin.create_device() internal

Architecture:
    StandalonePaasService  →  DockerSandboxPlugin  →  DockerSandbox
    (test verifies)           (MagicMock)             (MagicMock)
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    CommandResult,
    DockerCreateConfig,
    DockerCreationResult,
    DockerCredentials,
    DockerDeviceInfo,
    ErrorCode,
    PaasError,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas._standalone_paas_service import (
    StandalonePaasService,
)

# ---------------------------------------------------------------------------
# Module-level helper factories
# ---------------------------------------------------------------------------


def make_docker_credentials(
    template_id: int = 50,
    template_uuid: str = "tpl-test",
    tenant_name: str = "test-tenant",
) -> DockerCredentials:
    """Create test DockerCredentials with default values."""
    return DockerCredentials(
        template_id=template_id,
        template_uuid=template_uuid,
        tenant_name=tenant_name,
    )


def make_docker_create_config(
    image: str = "alpine:latest",
    container_port: int = 8080,
) -> DockerCreateConfig:
    """Create test DockerCreateConfig with default values."""
    return DockerCreateConfig(image=image, container_port=container_port)


def _make_mock_exec_result(
    exit_code: int = 0,
    stdout: str = "hello world",
    stderr: str = "",
    elapsed_time: int = 42,
) -> MagicMock:
    """Build a mock CommandResult-like namespace for sandbox.exec_command."""
    result = MagicMock()
    result.exit_code = exit_code
    result.stdout = stdout
    result.stderr = stderr
    result.elapsed_time = elapsed_time
    return result


def make_mock_plugin(connect_device_return: MagicMock | None = None) -> MagicMock:
    """Create a mock DockerSandboxPlugin with a working create_device flow.

    Returns a MagicMock plugin configured so that:
    - create_device() returns a DockerSandbox with is_ready=True
    - get_info() returns container attrs dict (for DockerDeviceInfo construction)
    - exec_command() returns a stub CommandResult with exit_code=0
    - restart() / destroy() return True
    - resolve_ws_conn_info / resolve_invoke_http_info return localhost URLs
    - invoke_http_in_device returns HTTP 200 dict

    When connect_device_return is provided, it is used as the return value
    for both create_device and connect_device WITHOUT overwriting its
    attributes — the caller is responsible for configuring the sandbox mock.
    """
    plugin = MagicMock()

    if connect_device_return is None:
        # Default sandbox for most tests
        mock_sandbox = MagicMock()
        mock_sandbox.is_ready = True
        mock_sandbox.sandbox_id = "abc123"
        mock_sandbox.get_info.return_value = {
            "sandbox_id": "abc123",
            "status": "running",
            "container_id": "abc123",
            "host_port": 18080,
            "image": "alpine:latest",
        }
        mock_sandbox.exec_command.return_value = _make_mock_exec_result()
        mock_sandbox.restart.return_value = True
        mock_sandbox.destroy.return_value = True
    else:
        # Use caller-provided sandbox without overwriting any of its attributes
        mock_sandbox = connect_device_return

    plugin.create_device.return_value = mock_sandbox
    plugin.connect_device.return_value = mock_sandbox
    plugin.destroy_device.return_value = True
    plugin.resolve_ws_conn_info.return_value = WsConnectionInfo(
        ws_url="ws://127.0.0.1:8080/ws",
        token="",
        target="test",
        expires_at=datetime.max,
    )
    plugin.resolve_invoke_http_info.return_value = HttpConnectionInfo(
        http_url="http://127.0.0.1:8080/",
        token="",
        target="test",
    )
    plugin.invoke_http_in_device.return_value = {
        "status_code": 200,
        "headers": {},
        "body": "",
    }
    return plugin


# ============================================================================
# Note: TestMapDockerError class removed.
# _map_docker_error moved to RealDockerSandboxPlugin (Plan 02) —
# tested via contract tests (Plan 07).
# ============================================================================


# ============================================================================
# Constructor tests
# ============================================================================


class TestConstructor:
    """Constructor validation for StandalonePaasService.

    Post-refactor: Constructor takes (plugin, credentials, health_endpoint,
    health_timeout_seconds). No docker_client, image_pull_policy, or fail-fast
    ping — those are Plugin internals.
    """

    def test_plugin_is_required(self):
        """WHEN plugin is None, THEN constructor raises ValueError."""
        with pytest.raises(ValueError, match="plugin is required"):
            StandalonePaasService(
                plugin=None,
                credentials=make_docker_credentials(),
            )

    def test_credentials_is_required(self):
        """WHEN credentials is None, THEN constructor raises ValueError."""
        with pytest.raises(ValueError, match="credentials is required"):
            StandalonePaasService(
                plugin=MagicMock(),
                credentials=None,
            )

    def test_default_health_params(self):
        """WHEN no health params specified, THEN defaults are /health and 120."""
        service = StandalonePaasService(
            plugin=MagicMock(),
            credentials=make_docker_credentials(),
        )
        assert service._health_endpoint == "/health"
        assert service._health_timeout_seconds == 120

    def test_custom_health_params(self):
        """WHEN custom health params, THEN they are stored."""
        service = StandalonePaasService(
            plugin=MagicMock(),
            credentials=make_docker_credentials(),
            health_endpoint="/api/health",
            health_timeout_seconds=60,
        )
        assert service._health_endpoint == "/api/health"
        assert service._health_timeout_seconds == 60


# ============================================================================
# create_device tests
# ============================================================================


class TestCreateDevice:
    """create_device with mock DockerSandboxPlugin."""

    @pytest.mark.asyncio
    async def test_image_is_required(self):
        """WHEN config.image is None, THEN ValueError is raised BEFORE plugin call."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )
        config = DockerCreateConfig(image=None, container_port=8080)

        with pytest.raises(ValueError, match="config.image is required"):
            await service.create_device(config)

        # Plugin should NOT have been called
        plugin.create_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_container_port_is_required(self):
        """WHEN config.container_port is None, THEN ValueError is raised."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )
        config = DockerCreateConfig(image="alpine:latest", container_port=None)

        with pytest.raises(ValueError, match="config.container_port is required"):
            await service.create_device(config)

        plugin.create_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_wrong_config_type_raises_paas_error(self):
        """WHEN config is not DockerCreateConfig, THEN PaasError(CONFIG_INVALID)."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        # Use a non-DockerCreateConfig object
        class FakeConfig:
            pass

        with pytest.raises(PaasError) as exc_info:
            await service.create_device(FakeConfig())

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        assert "DockerCreateConfig" in str(exc_info.value)
        plugin.create_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_create_returns_docker_creation_result(self):
        """WHEN plugin.create_device returns a sandbox, THEN
        create_device returns DockerCreationResult with correct fields."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )
        config = make_docker_create_config()

        result = await service.create_device(config)

        assert isinstance(result, DockerCreationResult)
        assert result.platform == "docker"
        assert result.container_id == "abc123"
        assert result.host_port == 18080
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_plugin_create_device_called_with_correct_params(self):
        """WHEN create_device is called, THEN plugin.create_device receives
        expected arguments (image, container_port, container_name pattern, etc.)."""
        plugin = make_mock_plugin()
        creds = make_docker_credentials(
            template_id=50, template_uuid="tpl-test", tenant_name="test-tenant"
        )
        service = StandalonePaasService(plugin=plugin, credentials=creds)
        config = make_docker_create_config(image="ubuntu:22.04", container_port=3000)

        await service.create_device(config)

        plugin.create_device.assert_called_once()
        call_kwargs = plugin.create_device.call_args[1]
        assert call_kwargs["template_id"] == 50
        assert call_kwargs["template_uuid"] == "tpl-test"
        assert call_kwargs["tenant_name"] == "test-tenant"
        assert call_kwargs["container_name"].startswith("baas-agent-")
        assert call_kwargs["image"] == "ubuntu:22.04"
        assert call_kwargs["container_port"] == 3000


# ============================================================================
# destroy_device tests
# ============================================================================


class TestDestroyDevice:
    """destroy_device delegates to plugin.destroy_device."""

    @pytest.mark.asyncio
    async def test_destroy_delegates_to_plugin(self):
        """WHEN destroy_device is called, THEN plugin.destroy_device is called
        with the paas_device_id."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.destroy_device("abc123")

        assert result is True
        plugin.destroy_device.assert_called_once_with("abc123")

    @pytest.mark.asyncio
    async def test_destroy_returns_plugin_result(self):
        """WHEN plugin.destroy_device returns False, THEN destroy_device
        returns False."""
        plugin = make_mock_plugin()
        plugin.destroy_device.return_value = False
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.destroy_device("abc123")

        assert result is False


# ============================================================================
# restart_device tests
# ============================================================================


class TestRestartDevice:
    """restart_device uses plugin.connect_device + sandbox.restart."""

    @pytest.mark.asyncio
    async def test_restart_connects_and_restarts_sandbox(self):
        """WHEN restart_device is called, THEN plugin.connect_device is called,
        then sandbox.restart is called."""
        mock_sandbox = MagicMock()
        mock_sandbox.restart.return_value = True
        plugin = make_mock_plugin(connect_device_return=mock_sandbox)
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.restart_device("abc123")

        assert result is True
        plugin.connect_device.assert_called_once_with("abc123")
        mock_sandbox.restart.assert_called_once()


# ============================================================================
# execute_command tests
# ============================================================================


class TestExecuteCommand:
    """execute_command uses plugin.connect_device + sandbox.exec_command."""

    @pytest.mark.asyncio
    async def test_execute_command_returns_command_result(self):
        """WHEN sandbox.exec_command returns exit_code=0 with stdout,
        THEN execute_command returns CommandResult with decoded fields."""
        mock_sandbox = MagicMock()
        mock_result = _make_mock_exec_result(
            exit_code=0, stdout="hello world", stderr=""
        )
        mock_sandbox.exec_command.return_value = mock_result
        plugin = make_mock_plugin(connect_device_return=mock_sandbox)
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.execute_command("abc123", "echo hello")

        assert isinstance(result, CommandResult)
        assert result.exit_code == 0
        assert result.stdout == "hello world"
        assert result.stderr == ""
        assert result.execution_time_ms == 42
        assert result.command == "echo hello"

        # Verify plugin.connect_device was called
        plugin.connect_device.assert_called_once_with("abc123")
        mock_sandbox.exec_command.assert_called_once()


# ============================================================================
# get_device_info tests
# ============================================================================


class TestGetDeviceInfo:
    """get_device_info uses plugin.connect_device + sandbox.get_info."""

    @pytest.mark.asyncio
    async def test_get_device_info_returns_docker_device_info(self):
        """WHEN sandbox.get_info returns container attrs, THEN
        get_device_info returns DockerDeviceInfo with correct fields."""
        mock_sandbox = MagicMock()
        mock_sandbox.get_info.return_value = {
            "sandbox_id": "abc123def456",
            "status": "running",
            "container_id": "abc123def456",
            "host_port": 32768,
            "image": "alpine:latest",
        }
        plugin = make_mock_plugin(connect_device_return=mock_sandbox)
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.get_device_info("abc123")

        assert isinstance(result, DockerDeviceInfo)
        assert result.status == "running"
        assert result.container_id == "abc123def456"
        assert result.host_port == 32768
        assert result.image == "alpine:latest"
        assert result.platform == "docker"

        plugin.connect_device.assert_called_once_with("abc123")


# ============================================================================
# resolve_ws_conn_info tests
# ============================================================================


class TestResolveWsConnInfo:
    """resolve_ws_conn_info delegates to plugin."""

    @pytest.mark.asyncio
    async def test_resolve_ws_conn_info_delegates_to_plugin(self):
        """WHEN resolve_ws_conn_info is called, THEN plugin method is called
        and returns WsConnectionInfo."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.resolve_ws_conn_info("abc123", 32768, "/ws")

        assert isinstance(result, WsConnectionInfo)
        assert result.ws_url == "ws://127.0.0.1:8080/ws"
        plugin.resolve_ws_conn_info.assert_called_once_with("abc123", 32768, "/ws")


# ============================================================================
# resolve_invoke_http_info tests
# ============================================================================


class TestResolveInvokeHttpInfo:
    """resolve_invoke_http_info delegates to plugin."""

    @pytest.mark.asyncio
    async def test_resolve_invoke_http_info_delegates_to_plugin(self):
        """WHEN resolve_invoke_http_info is called, THEN plugin method is called
        and returns HttpConnectionInfo."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.resolve_invoke_http_info("abc123", 32768, "/api")

        assert isinstance(result, HttpConnectionInfo)
        assert result.http_url == "http://127.0.0.1:8080/"
        plugin.resolve_invoke_http_info.assert_called_once_with("abc123", 32768, "/api")


# ============================================================================
# invoke_http_in_device tests
# ============================================================================


class TestInvokeHttpInDevice:
    """invoke_http_in_device delegates to plugin."""

    @pytest.mark.asyncio
    async def test_invoke_http_in_device_delegates_to_plugin(self):
        """WHEN invoke_http_in_device is called, THEN plugin method is called
        and returns HTTP response dict."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.invoke_http_in_device(
            "abc123", "GET", 32768, "/status", None, {}, b""
        )

        assert result["status_code"] == 200
        assert result["headers"] == {}
        assert result["body"] == ""
        plugin.invoke_http_in_device.assert_called_once_with(
            "abc123", "GET", 32768, "/status", None, {}, b""
        )


# ============================================================================
# update_device tests
# ============================================================================


class TestUpdateDevice:
    """update_device validates config + performs destroy+create rebuild."""

    @pytest.mark.asyncio
    async def test_config_none_raises_paas_error(self):
        """WHEN config is None, THEN PaasError(CONFIG_INVALID) is raised."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        with pytest.raises(PaasError) as exc_info:
            await service.update_device("abc123", config=None)

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        assert "None" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_wrong_config_type_raises_paas_error(self):
        """WHEN config is not DockerCreateConfig, THEN PaasError(CONFIG_INVALID)."""

        class FakeConfig:
            pass

        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        with pytest.raises(PaasError) as exc_info:
            await service.update_device("abc123", config=FakeConfig())

        assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        assert "DockerCreateConfig" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_valid_config_destroys_and_creates(self):
        """WHEN valid DockerCreateConfig is provided, THEN destroy_device
        is called, then create_device is called, and result is True."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        mock_destroy = AsyncMock(return_value=True)
        mock_create = AsyncMock()

        with (
            patch.object(service, "destroy_device", mock_destroy),
            patch.object(service, "create_device", mock_create),
        ):
            result = await service.update_device("abc123", make_docker_create_config())

        assert result is True
        mock_destroy.assert_called_once_with("abc123")
        mock_create.assert_called_once()


# ============================================================================
# Smoke tests — platform type, credentials, update_device_ttl
# ============================================================================


class TestSmokeMethods:
    """Smoke coverage: simple accessor / identity methods."""

    @pytest.mark.asyncio
    async def test_restart_device_calls_plugin_connect_and_sandbox_restart(self):
        """WHEN restart_device is called, THEN it delegates to plugin + sandbox."""
        mock_sandbox = MagicMock()
        mock_sandbox.restart.return_value = True
        plugin = make_mock_plugin(connect_device_return=mock_sandbox)
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.restart_device("abc123")

        assert result is True
        plugin.connect_device.assert_called_once_with("abc123")
        mock_sandbox.restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_device_ttl_raises_not_implemented_error(self):
        """WHEN update_device_ttl is called, THEN NotImplementedError is raised."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        with pytest.raises(NotImplementedError):
            await service.update_device_ttl("abc123")

    @pytest.mark.asyncio
    async def test_get_platform_type_returns_docker(self):
        """WHEN get_platform_type is called, THEN TenantType.DOCKER is returned."""
        plugin = make_mock_plugin()
        service = StandalonePaasService(
            plugin=plugin, credentials=make_docker_credentials()
        )

        result = await service.get_platform_type()

        assert result == TenantType.DOCKER

    @pytest.mark.asyncio
    async def test_get_credentials_returns_injected_credentials(self):
        """WHEN get_credentials is called, THEN the stored DockerCredentials
        are returned."""
        creds = make_docker_credentials(
            template_id=42,
            template_uuid="uuid-test-creds",
            tenant_name="my-tenant",
        )
        plugin = make_mock_plugin()
        service = StandalonePaasService(plugin=plugin, credentials=creds)

        result = await service.get_credentials()

        assert result.template_id == 42
        assert result.template_uuid == "uuid-test-creds"
        assert result.tenant_name == "my-tenant"
