# mypy: disable-error-code="arg-type"
"""Unit tests for DefaultBotOpenFolderDispatcher.dispatch_bot_open_folder.

Tests the open_folder dispatch flow including:
- Bot lookup by UUID
- Device selection from bot's associated devices
- PaasServiceFacade.open_folder delegation with correct paas_device_id and folder_path
- Error propagation (BotNotFoundError, NoDevicesFoundError, NoActiveDevicesError)
- Facade error propagation (DeviceFacadeException including PLATFORM_ERROR)
- Tenant isolation and device affinity
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import (
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
    PaasServiceFacade,
)

# ==================== Fixtures ====================


@pytest.fixture
def mock_bot_repo():
    """Mock BotRepository."""
    repo = MagicMock()
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
    facade.open_folder.return_value = True
    return facade


@pytest.fixture
def mock_active_device():
    """Create a mock ACTIVE device record."""
    device = MagicMock()
    device.id = 1
    device.device_uuid = "device-uuid-001"
    device.provider_type = "LOCAL"
    device.provider_device_id = "container--machine--user"
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
    device.provider_type = "LOCAL"
    device.provider_device_id = "container2--machine2--user2"
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
    device.provider_type = "LOCAL"
    device.provider_device_id = "container3--machine3--user3"
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


# ==================== Test dispatch_bot_open_folder ====================


class TestDispatchBotOpenFolder:
    """Test dispatch_bot_open_folder method."""

    @pytest.mark.asyncio
    async def test_successful_open_folder_default_path(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully open folder with default (None) folder_path."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_open_folder(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result is True
        mock_paas_facade.open_folder.assert_awaited_once()
        call_args = mock_paas_facade.open_folder.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"
        assert call_args.kwargs["folder_path"] is None

    @pytest.mark.asyncio
    async def test_successful_open_folder_with_custom_path(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Open folder with explicit folder_path passed to facade."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_open_folder(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            folder_path="/home/user/projects",
        )

        assert result is True
        mock_paas_facade.open_folder.assert_awaited_once()
        call_args = mock_paas_facade.open_folder.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"
        assert call_args.kwargs["folder_path"] == "/home/user/projects"

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_exception(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """BotNotFoundError raised when bot UUID not found."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_open_folder(
                bot_uuid="nonexistent-bot",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)
        mock_paas_facade.open_folder.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_devices_found_raises_exception(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """NoDevicesFoundError raised when bot has no devices."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = []

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_open_folder(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert "No devices found" in str(exc_info.value)
        mock_paas_facade.open_folder.assert_not_called()

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
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [
            mock_pending_device,
            mock_failed_device,
        ]

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_open_folder(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert "No active devices" in str(exc_info.value)
        mock_paas_facade.open_folder.assert_not_called()

    @pytest.mark.asyncio
    async def test_facade_platform_error_propagates(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """DeviceFacadeException with PLATFORM_ERROR propagates (non-LOCAL platform)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.open_folder.side_effect = DeviceFacadeException(
            operation="open_folder",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "open_folder not supported on ARCA platform",
            ),
        )

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_open_folder(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert exc_info.value.operation == "open_folder"
        assert exc_info.value.original_error.code == ErrorCode.PLATFORM_ERROR

    @pytest.mark.asyncio
    async def test_facade_generic_error_propagates(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """DeviceFacadeException with generic error propagates."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.open_folder.side_effect = DeviceFacadeException(
            operation="open_folder",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(
                ErrorCode.DEVICE_UNAVAILABLE, "Device is not available"
            ),
        )

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_open_folder(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert exc_info.value.original_error.code == ErrorCode.DEVICE_UNAVAILABLE

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
            DefaultBotOpenFolderDispatcher,
        )

        device_no_provider = MagicMock()
        device_no_provider.id = 1
        device_no_provider.device_uuid = "device-no-provider"
        device_no_provider.provider_device_id = None
        device_no_provider.status = "ACTIVE"
        device_no_provider.tenant = "test_tenant"

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [device_no_provider]

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await service.dispatch_bot_open_folder(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert "provider_device_id" in str(exc_info.value)
        mock_paas_facade.open_folder.assert_not_called()

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
            DefaultBotOpenFolderDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._open_folder_dispatcher.get_current_env",
            return_value="test",
        ):
            await service.dispatch_bot_open_folder(
                bot_uuid="bot-uuid-001",
                tenant="custom-tenant",
            )

        mock_bot_repo.get_active_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test"
        )

    @pytest.mark.asyncio
    async def test_device_affinity_returns_same_device(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Same device_affinity returns same device across calls."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotOpenFolderDispatcher,
        )

        active_devices = []
        for i in range(3):
            d = MagicMock()
            d.id = i + 1
            d.device_uuid = f"device-uuid-{i}"
            d.provider_type = "LOCAL"
            d.provider_device_id = f"container{i}--machine--user"
            d.status = "ACTIVE"
            d.tenant = "test_tenant"
            active_devices.append(d)

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = active_devices

        service = DefaultBotOpenFolderDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_open_folder(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        first_paas_id = mock_paas_facade.open_folder.call_args.kwargs["paas_device_id"]
        mock_paas_facade.open_folder.reset_mock()

        await service.dispatch_bot_open_folder(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        second_paas_id = mock_paas_facade.open_folder.call_args.kwargs["paas_device_id"]

        assert first_paas_id == second_paas_id, (
            "Same affinity should select same device"
        )
