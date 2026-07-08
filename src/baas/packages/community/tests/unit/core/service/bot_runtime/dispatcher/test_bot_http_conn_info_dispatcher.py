# mypy: disable-error-code="arg-type"
"""Unit tests for DefaultBotHttpConnInfoDispatcher.

Tests the bot HTTP connection info resolution service including:
- Bot lookup by UUID
- Device selection from bot's associated devices
- Random device selection strategy
- PaasServiceFacade integration for HTTP connection info resolution
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import (
    BotNotFoundError,
    HttpConnectionInfo,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.core.service.paas import PaasServiceFacade

# ==================== Fixtures ====================


@pytest.fixture
def mock_bot_repo():
    """Mock BotRepository."""
    repo = MagicMock()
    repo.get_by_bot_uuid = MagicMock(return_value=None)
    repo.get_active_by_bot_uuid = MagicMock(return_value=None)
    return repo


@pytest.fixture
def mock_device_repo():
    """Mock DeviceRepository."""
    repo = MagicMock()
    repo.list_by_bot_id = MagicMock(return_value=[])
    return repo


@pytest.fixture
def mock_paas_facade():
    """Mock PaasServiceFacade using AsyncMock with spec for type-safe mocking."""
    facade = AsyncMock(spec=PaasServiceFacade)
    facade.resolve_invoke_http_info.return_value = HttpConnectionInfo(
        http_url="http://agentclawproxy-prod.alipay.com/proxypass/ARCA_test-sandbox@10000:20003/api/health",
        token="test-jwt-token",
    )
    return facade


@pytest.fixture
def mock_active_device():
    """Create a mock ACTIVE device record."""
    device = MagicMock()
    device.id = 1
    device.device_uuid = "device-uuid-001"
    device.provider_type = "ARCA"
    device.provider_device_id = "ARCA-SANDBOX-test-001"
    device.status = "ACTIVE"
    device.tenant = "test_tenant"
    device.env = "prod"
    return device


@pytest.fixture
def mock_pending_device():
    """Create a mock PENDING device record."""
    device = MagicMock()
    device.id = 2
    device.device_uuid = "device-uuid-002"
    device.provider_type = "ARCA"
    device.provider_device_id = "ARCA-SANDBOX-test-002"
    device.status = "PENDING"
    device.tenant = "test_tenant"
    device.env = "prod"
    return device


@pytest.fixture
def mock_failed_device():
    """Create a mock FAILED device record."""
    device = MagicMock()
    device.id = 3
    device.device_uuid = "device-uuid-003"
    device.provider_type = "ARCA"
    device.provider_device_id = "ARCA-SANDBOX-test-003"
    device.status = "FAILED"
    device.tenant = "test_tenant"
    device.env = "prod"
    return device


@pytest.fixture
def mock_bot():
    """Create a mock bot record."""
    bot = MagicMock()
    bot.id = 1
    bot.bot_uuid = "bot-uuid-001"
    bot.tenant = "test_tenant"
    bot.env = "prod"
    bot.status = "ACTIVE"
    return bot


# ==================== Test Main Service Method ====================


class TestDispatchBotHttpConnInfo:
    """Test dispatch_bot_http_conn_info main method."""

    @pytest.mark.asyncio
    async def test_successful_resolution_with_active_device(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully resolve HTTP connection info for bot with ACTIVE device."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
        )

        # Verify result
        assert result.http_url.startswith("http://")
        assert result.token == "test-jwt-token"

        # Verify facade was called with provider_device_id directly
        mock_paas_facade.resolve_invoke_http_info.assert_awaited_once()
        call_args = mock_paas_facade.resolve_invoke_http_info.call_args
        assert call_args.kwargs["paas_device_id"] == "ARCA-SANDBOX-test-001"
        assert call_args.kwargs["port"] == 20003
        assert call_args.kwargs["path"] == "/api/health"

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_exception(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """BotNotFoundError raised when bot UUID not found."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="nonexistent-bot",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_devices_found_raises_exception(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """NoDevicesFoundError raised when bot has no devices."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = []

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
            )

        assert "No devices found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_active_devices_raises_exception(
        self,
        mock_bot,
        mock_pending_device,
        mock_failed_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """NoActiveDevicesError raised when no ACTIVE devices exist."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [
            mock_pending_device,
            mock_failed_device,
        ]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
            )

        assert "No active devices" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_facade_exception_propagates(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Exception from PaasServiceFacade propagates correctly."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.resolve_invoke_http_info.side_effect = DeviceFacadeException(
            operation="resolve_invoke_http_info",
            platform_type="ARCA",
            template_id=10000,
            paas_device_id="ARCA-SANDBOX-test-001",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
        )

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException):
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
            )

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Service passes tenant and env for isolation."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._http_conn_info_dispatcher.get_current_env",
            return_value="test",
        ):
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/health",
                tenant="custom-tenant",
            )

        mock_bot_repo.get_active_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test"
        )

    @pytest.mark.asyncio
    async def test_different_port_and_path(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Service correctly passes different port and path values."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=8080,
            path="/custom/api",
            tenant="test_tenant",
        )

        call_args = mock_paas_facade.resolve_invoke_http_info.call_args
        assert call_args.kwargs["port"] == 8080
        assert call_args.kwargs["path"] == "/custom/api"

    @pytest.mark.asyncio
    async def test_device_affinity_passthrough(
        self,
        mock_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """device_affinity is passed through to _resolve_bot_device."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        active_devices = []
        for i in range(3):
            d = MagicMock()
            d.id = i + 1
            d.device_uuid = f"device-uuid-{i}"
            d.provider_type = "ARCA"
            d.provider_device_id = f"ARCA-SANDBOX-test-{i}"
            d.status = "ACTIVE"
            d.tenant = "test_tenant"
            active_devices.append(d)

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = active_devices

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )

        first_paas_id = mock_paas_facade.resolve_invoke_http_info.call_args.kwargs[
            "paas_device_id"
        ]
        mock_paas_facade.resolve_invoke_http_info.reset_mock()

        await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        second_paas_id = mock_paas_facade.resolve_invoke_http_info.call_args.kwargs[
            "paas_device_id"
        ]

        assert first_paas_id == second_paas_id, (
            "Same affinity should select same device"
        )

    @pytest.mark.asyncio
    async def test_provider_device_id_none_raises_runtime_error(
        self,
        mock_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """RuntimeError raised when device has no provider_device_id."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        device_no_provider = MagicMock()
        device_no_provider.id = 1
        device_no_provider.device_uuid = "device-no-provider"
        device_no_provider.provider_device_id = None
        device_no_provider.status = "ACTIVE"
        device_no_provider.tenant = "test_tenant"

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [device_no_provider]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=8080,
                path="/api/health",
                tenant="test_tenant",
            )

        assert "provider_device_id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_returns_http_connection_info_type(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Result is an instance of HttpConnectionInfo."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
        )

        assert isinstance(result, HttpConnectionInfo)


# ==================== Test Device UUID Targeting ====================


class TestDispatchBotHttpConnInfoWithDeviceUuid:
    """Test dispatch_bot_http_conn_info with device_uuid parameter."""

    @pytest.mark.asyncio
    async def test_specific_device_resolved(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully resolve HTTP connection for a specific device UUID."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
            device_uuid="device-uuid-001",
        )

        assert result.http_url.startswith("http://")
        assert result.token == "test-jwt-token"
        mock_paas_facade.resolve_invoke_http_info.assert_awaited_once_with(
            paas_device_id="ARCA-SANDBOX-test-001",
            port=20003,
            path="/api/health",
        )

    @pytest.mark.asyncio
    async def test_specific_device_not_in_bot_devices_raises_error(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """NoDevicesFoundError when device_uuid not associated with the bot."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
                device_uuid="device-uuid-999",
            )

        assert "device-uuid-999" in str(exc_info.value)
        assert "bot-uuid-001" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_specific_device_not_active_raises_error(
        self,
        mock_bot,
        mock_pending_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """NoActiveDevicesError when device_uuid exists but status is not ACTIVE."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_pending_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
                device_uuid="device-uuid-002",
            )

        assert "device-uuid-002" in str(exc_info.value)
        assert "PENDING" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_device_uuid_none_falls_back_to_auto_select(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """When device_uuid is None, auto-select behavior is preserved."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
        )

        assert result.http_url.startswith("http://")
        assert result.token == "test-jwt-token"
        mock_bot_repo.get_active_by_bot_uuid.assert_called_once()
        mock_device_repo.list_by_bot_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_specific_device_preferred_over_affinity(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """When device_uuid is provided, it takes precedence over device_affinity."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_http_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/health",
            tenant="test_tenant",
            device_affinity="some-affinity",
            device_uuid="device-uuid-001",
        )

        assert result.http_url.startswith("http://")
        mock_paas_facade.resolve_invoke_http_info.assert_awaited_once_with(
            paas_device_id="ARCA-SANDBOX-test-001",
            port=20003,
            path="/api/health",
        )

    @pytest.mark.asyncio
    async def test_specific_device_not_found_bot_not_found(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """BotNotFoundError when bot does not exist even with device_uuid."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpConnInfoDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotHttpConnInfoDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_http_conn_info(
                bot_uuid="nonexistent-bot",
                port=20003,
                path="/api/health",
                tenant="test_tenant",
                device_uuid="device-uuid-001",
            )

        assert "nonexistent-bot" in str(exc_info.value)
