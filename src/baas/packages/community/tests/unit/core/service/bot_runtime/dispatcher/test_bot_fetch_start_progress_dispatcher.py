# mypy: disable-error-code="arg-type"
"""Unit tests for DefaultBotFetchStartProgressDispatcher.

Tests the fetch_start_progress dispatch flow including:
- Bot lookup by UUID (via list_by_bot_uuid with relaxed status filter)
- Device selection from bot's associated devices (via select_available_device)
- PaasServiceFacade.fetch_start_progress delegation with correct paas_device_id
- Mapping of FetchStartProgressResult to BotStartProgressResponse (via model_dump passthrough)
- Error propagation (BotNotFoundError, NoDevicesFoundError, NoActiveDevicesError)
- Facade error propagation (DeviceFacadeException including PLATFORM_ERROR)
- Tenant isolation and device affinity
- PENDING/UPDATING status acceptance for bot creation and publish flows
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
    repo.list_by_bot_uuid = MagicMock(return_value=[])
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
    facade.fetch_start_progress.return_value = MagicMock()
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
def mock_updating_device():
    """Create a mock UPDATING device record."""
    device = MagicMock()
    device.id = 4
    device.device_uuid = "device-uuid-004"
    device.provider_type = "LOCAL"
    device.provider_device_id = "container-updating--machine--user"
    device.status = "UPDATING"
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
def mock_released_device():
    """Create a mock RELEASED device record."""
    device = MagicMock()
    device.id = 5
    device.device_uuid = "device-uuid-005"
    device.provider_type = "LOCAL"
    device.provider_device_id = "container5--machine5--user5"
    device.status = "RELEASED"
    device.tenant = "test_tenant"
    device.env = "prod"
    return device


@pytest.fixture
def mock_stopped_device():
    """Create a mock STOPPED device record."""
    device = MagicMock()
    device.id = 6
    device.device_uuid = "device-uuid-006"
    device.provider_type = "LOCAL"
    device.provider_device_id = "container6--machine6--user6"
    device.status = "STOPPED"
    device.tenant = "test_tenant"
    device.env = "prod"
    return device


@pytest.fixture
def mock_bot():
    """Create a mock ACTIVE bot record."""
    bot = MagicMock()
    bot.id = 1
    bot.bot_uuid = "bot-uuid-001"
    bot.tenant = "test_tenant"
    bot.env = "prod"
    bot.status = "ACTIVE"
    return bot


@pytest.fixture
def mock_pending_bot():
    """Create a mock PENDING bot record."""
    bot = MagicMock()
    bot.id = 2
    bot.bot_uuid = "bot-uuid-001"
    bot.tenant = "test_tenant"
    bot.env = "prod"
    bot.status = "PENDING"
    return bot


@pytest.fixture
def mock_released_bot():
    """Create a mock RELEASED bot record."""
    bot = MagicMock()
    bot.id = 3
    bot.bot_uuid = "bot-uuid-released"
    bot.tenant = "test_tenant"
    bot.env = "prod"
    bot.status = "RELEASED"
    return bot


@pytest.fixture
def mock_failed_bot():
    """Create a mock FAILED bot record."""
    bot = MagicMock()
    bot.id = 4
    bot.bot_uuid = "bot-uuid-failed"
    bot.tenant = "test_tenant"
    bot.env = "prod"
    bot.status = "FAILED"
    return bot


@pytest.fixture
def fetch_start_progress_result():
    """Create a mock FetchStartProgressResult."""
    from secbaas.api.bot_manage import FetchStartProgressResult

    return FetchStartProgressResult(
        progress="in_progress",
        status="downloading",
        message=None,
    )


# ==================== Test dispatch_bot_fetch_start_progress ====================


class TestDispatchBotFetchStartProgress:
    """Test dispatch_bot_fetch_start_progress method."""

    @pytest.mark.asyncio
    async def test_successful_fetch_start_progress_in_progress(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """Successfully fetch start progress with in_progress status."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "in_progress"
        assert result.status == "downloading"  # type: ignore[attr-defined]
        assert result.message is None  # type: ignore[attr-defined]

        mock_paas_facade.fetch_start_progress.assert_awaited_once()
        call_args = mock_paas_facade.fetch_start_progress.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"

    @pytest.mark.asyncio
    async def test_successful_fetch_start_progress_completed(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully fetch start progress with completed status."""
        from secbaas.api.bot_manage import FetchStartProgressResult
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        completed_result = FetchStartProgressResult(
            progress="completed",
            status="ready",
            message=None,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = completed_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "completed"
        assert result.status == "ready"  # type: ignore[attr-defined]
        assert result.message is None  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_successful_fetch_start_progress_failed(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully fetch start progress with failed status and error message."""
        from secbaas.api.bot_manage import FetchStartProgressResult
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        failed_result = FetchStartProgressResult(
            progress="failed",
            status="error",
            message="Container exited with code 1",
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = failed_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "failed"
        assert result.status == "error"  # type: ignore[attr-defined]
        assert result.message == "Container exited with code 1"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_dispatcher_model_dump_passthrough(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Dispatcher returns BotStartProgressResponse with all fields from FetchStartProgressResult
        (including extra fields via model_dump passthrough)."""
        from secbaas.api.bot_manage import FetchStartProgressResult
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        result_with_extra = FetchStartProgressResult(
            progress="completed",
            status="ready",
            message=None,
            custom_field=42,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = result_with_extra

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "completed"
        assert result.status == "ready"  # type: ignore[attr-defined]
        assert result.message is None  # type: ignore[attr-defined]
        assert result.custom_field == 42  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_exception(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """BotNotFoundError raised when bot UUID not found (empty list)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = []

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="nonexistent-bot",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)
        mock_paas_facade.fetch_start_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_devices_found_raises_exception(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """NoDevicesFoundError raised when bot has no devices."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = []

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert "No devices found" in str(exc_info.value)
        mock_paas_facade.fetch_start_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_active_devices_raises_exception(
        self,
        mock_bot,
        mock_failed_device,
        mock_stopped_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """NoActiveDevicesError raised when all devices are in excluded states
        (FAILED + STOPPED — both excluded by select_available_device)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [
            mock_failed_device,
            mock_stopped_device,
        ]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert exc_info.value.bot_uuid == "bot-uuid-001"
        mock_paas_facade.fetch_start_progress.assert_not_called()

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
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.fetch_start_progress.side_effect = DeviceFacadeException(
            operation="fetch_start_progress",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "fetch_start_progress not supported on ARCA platform",
            ),
        )

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert exc_info.value.operation == "fetch_start_progress"
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
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.fetch_start_progress.side_effect = DeviceFacadeException(
            operation="fetch_start_progress",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(
                ErrorCode.DEVICE_UNAVAILABLE, "Device is not available"
            ),
        )

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
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
            DefaultBotFetchStartProgressDispatcher,
        )

        device_no_provider = MagicMock()
        device_no_provider.id = 1
        device_no_provider.device_uuid = "device-no-provider"
        device_no_provider.provider_device_id = None
        device_no_provider.status = "ACTIVE"
        device_no_provider.tenant = "test_tenant"

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [device_no_provider]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert "provider_device_id" in str(exc_info.value)
        mock_paas_facade.fetch_start_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """Service passes tenant and env for isolation via list_by_bot_uuid."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._fetch_start_progress_dispatcher.get_current_env",
            return_value="test",
        ):
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="custom-tenant",
            )

        mock_bot_repo.list_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test"
        )

    @pytest.mark.asyncio
    async def test_device_affinity_returns_same_device(
        self,
        mock_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """Same device_affinity returns same device across calls."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
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

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = active_devices
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        first_paas_id = mock_paas_facade.fetch_start_progress.call_args.kwargs[
            "paas_device_id"
        ]
        mock_paas_facade.fetch_start_progress.reset_mock()

        await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        second_paas_id = mock_paas_facade.fetch_start_progress.call_args.kwargs[
            "paas_device_id"
        ]

        assert first_paas_id == second_paas_id, (
            "Same affinity should select same device"
        )

    # ============ NEW TESTS (CURRENTLY FAILING — RED PHASE) ============

    @pytest.mark.asyncio
    async def test_pending_bot_pending_device_success(
        self,
        mock_pending_bot,
        mock_pending_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """PENDING bot + PENDING device → success (CREATE publish scenario)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_pending_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_pending_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "in_progress"
        mock_paas_facade.fetch_start_progress.assert_awaited_once()
        call_args = mock_paas_facade.fetch_start_progress.call_args
        assert call_args.kwargs["paas_device_id"] == "container2--machine2--user2"

    @pytest.mark.asyncio
    async def test_active_bot_updating_device_success(
        self,
        mock_bot,
        mock_updating_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """ACTIVE bot + UPDATING device → success (UPDATE/RESTART publish scenario)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_updating_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "in_progress"
        mock_paas_facade.fetch_start_progress.assert_awaited_once()
        call_args = mock_paas_facade.fetch_start_progress.call_args
        assert call_args.kwargs["paas_device_id"] == "container-updating--machine--user"

    @pytest.mark.asyncio
    async def test_bot_not_found_list_empty(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """BotNotFoundError when list_by_bot_uuid returns empty list."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = []

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="nonexistent-bot",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_all_devices_excluded_raises_no_active_devices(
        self,
        mock_bot,
        mock_failed_device,
        mock_released_device,
        mock_stopped_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """NoActiveDevicesError when all devices are in excluded states
        (RELEASED, FAILED, STOPPED)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [
            mock_failed_device,
            mock_released_device,
            mock_stopped_device,
        ]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        assert exc_info.value.bot_uuid == "bot-uuid-001"
        mock_paas_facade.fetch_start_progress.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_bot_active_device_success(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """ACTIVE bot + ACTIVE device → success (normal operation regression guard)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
        )

        assert result.progress == "in_progress"
        mock_paas_facade.fetch_start_progress.assert_awaited_once()
        call_args = mock_paas_facade.fetch_start_progress.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"

    @pytest.mark.asyncio
    async def test_released_bot_excluded(
        self,
        mock_released_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """RELEASED bot excluded — BotNotFoundError raised."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_released_bot]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError):
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-released",
                tenant="test_tenant",
            )

    @pytest.mark.asyncio
    async def test_failed_bot_excluded(
        self,
        mock_failed_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """FAILED bot excluded — BotNotFoundError raised."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_failed_bot]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError):
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-failed",
                tenant="test_tenant",
            )

    @pytest.mark.asyncio
    async def test_multiple_bots_picks_latest(
        self,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """Multiple bot records → picks the one with highest id (latest)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        # Old bot (RELEASED, lower id) and current bot (ACTIVE, higher id)
        mock_old_bot = MagicMock()
        mock_old_bot.id = 1
        mock_old_bot.bot_uuid = "bot-uuid-001"
        mock_old_bot.tenant = "test_tenant"
        mock_old_bot.env = "prod"
        mock_old_bot.status = "RELEASED"

        mock_latest_bot = MagicMock()
        mock_latest_bot.id = 2
        mock_latest_bot.bot_uuid = "bot-uuid-001"
        mock_latest_bot.tenant = "test_tenant"
        mock_latest_bot.env = "prod"
        mock_latest_bot.status = "ACTIVE"

        # Return in ID-ascending order — code sorts by id descending
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_old_bot, mock_latest_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._fetch_start_progress_dispatcher.get_current_env",
            return_value="prod",
        ):
            result = await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
            )

        # Should succeed because the latest bot (id=2) is ACTIVE
        assert result.progress == "in_progress"

        # Verify list_by_bot_id was called with latest bot's id
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=2, tenant="test_tenant", env="prod"
        )

    @pytest.mark.asyncio
    async def test_list_by_bot_uuid_tenant_env(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """list_by_bot_uuid is called with correct tenant and env parameters."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._fetch_start_progress_dispatcher.get_current_env",
            return_value="test-env",
        ):
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-001",
                tenant="custom-tenant",
            )

        mock_bot_repo.list_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test-env"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test-env"
        )

    @pytest.mark.asyncio
    async def test_device_affinity_passthrough_to_select_available(
        self,
        mock_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        fetch_start_progress_result,
    ):
        """device_affinity is passed through to select_available_device
        and consistently selects the same paas_device_id."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        available_devices = []
        for i in range(3):
            d = MagicMock()
            d.id = i + 1
            d.device_uuid = f"device-uuid-{i}"
            d.provider_type = "LOCAL"
            d.provider_device_id = f"container{i}--machine--user"
            d.status = "PENDING" if i == 2 else "ACTIVE"
            d.tenant = "test_tenant"
            available_devices.append(d)

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]
        mock_device_repo.list_by_bot_id.return_value = available_devices
        mock_paas_facade.fetch_start_progress.return_value = fetch_start_progress_result

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        # First call with affinity
        await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            device_affinity="sticky-key",
        )
        first_paas_id = mock_paas_facade.fetch_start_progress.call_args.kwargs[
            "paas_device_id"
        ]
        mock_paas_facade.fetch_start_progress.reset_mock()

        # Second call with same affinity — must return same device
        await service.dispatch_bot_fetch_start_progress(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            device_affinity="sticky-key",
        )
        second_paas_id = mock_paas_facade.fetch_start_progress.call_args.kwargs[
            "paas_device_id"
        ]

        assert first_paas_id == second_paas_id, (
            "Same affinity should select same device with select_available_device"
        )

    # ============ NEW TESTS: bot_status in BotNotFoundError ============

    @pytest.mark.asyncio
    async def test_bot_not_found_excluded_status_released_sets_bot_status(
        self,
        mock_released_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Dispatcher raises BotNotFoundError with bot_status='RELEASED' for RELEASED bot."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_released_bot]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-released",
                tenant="test_tenant",
            )

        assert exc_info.value.bot_status == "RELEASED"
        assert "bot-uuid-released" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_bot_not_found_excluded_status_failed_sets_bot_status(
        self,
        mock_failed_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Dispatcher raises BotNotFoundError with bot_status='FAILED' for FAILED bot."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = [mock_failed_bot]

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="bot-uuid-failed",
                tenant="test_tenant",
            )

        assert exc_info.value.bot_status == "FAILED"
        assert "bot-uuid-failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_bot_not_found_no_status_when_truly_missing(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Dispatcher raises BotNotFoundError with bot_status=None when bot genuinely not found."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotFetchStartProgressDispatcher,
        )

        mock_bot_repo.list_by_bot_uuid.return_value = []

        service = DefaultBotFetchStartProgressDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_fetch_start_progress(
                bot_uuid="nonexistent-bot",
                tenant="test_tenant",
            )

        assert exc_info.value.bot_status is None
        assert "nonexistent-bot" in str(exc_info.value)
