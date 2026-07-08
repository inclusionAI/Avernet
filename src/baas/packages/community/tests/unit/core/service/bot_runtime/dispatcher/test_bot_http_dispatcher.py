# mypy: disable-error-code="arg-type"
"""Unit tests for DefaultBotHttpDispatcher.

Tests the bot HTTP invocation resolution service including:
- Bot lookup by UUID
- Device selection from bot's associated devices
- Random device selection strategy
- Consistent hashing for sticky device selection
- PaasServiceFacade integration for HTTP invocation
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import (
    BotNotFoundError,
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
    facade.invoke_http_in_device.return_value = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": "eyJyZXN1bHQiOiAib2sifQ==",
    }
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


# ==================== Test Main Service Method ====================


class TestDispatchBotHttpInvoke:
    """Test dispatch_bot_http_invoke main method."""

    @pytest.fixture(autouse=True)
    def _mock_env(self):
        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._http_dispatcher.get_current_env",
            return_value="prod",
        ):
            yield

    @pytest.mark.asyncio
    async def test_successful_invocation(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully invoke HTTP on bot with ACTIVE device."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_http_invoke(
            bot_uuid="bot-uuid-001",
            method="GET",
            port=8080,
            path="/api/health",
            query_string=None,
            headers={},
            body=b"",
            tenant="test_tenant",
        )

        # Verify result
        assert result["status_code"] == 200
        assert result["body"] == "eyJyZXN1bHQiOiAib2sifQ=="

        # Verify facade was called with provider_device_id directly
        mock_paas_facade.invoke_http_in_device.assert_awaited_once()
        call_args = mock_paas_facade.invoke_http_in_device.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"
        assert call_args.kwargs["method"] == "GET"
        assert call_args.kwargs["port"] == 8080
        assert call_args.kwargs["path"] == "/api/health"

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_exception(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """BotNotFoundError raised when bot UUID not found."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_http_invoke(
                bot_uuid="nonexistent-bot",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_devices_found_raises_exception(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """NoDevicesFoundError raised when bot has no devices."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = []

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
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
            DefaultBotHttpDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [
            mock_pending_device,
            mock_failed_device,
        ]

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
                tenant="test_tenant",
            )

        assert "No active devices" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_random_selection_with_multiple_active_devices(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Random device selection when multiple ACTIVE devices exist."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
        )

        # Create multiple ACTIVE devices
        active_devices = []
        for i in range(3):
            device = MagicMock()
            device.id = i + 1
            device.device_uuid = f"device-uuid-{i}"
            device.provider_type = "LOCAL"
            device.provider_device_id = f"container{i}--machine--user"
            device.status = "ACTIVE"
            device.tenant = "test_tenant"
            active_devices.append(device)

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = active_devices

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        # Call multiple times and track which device IDs were used
        device_ids_seen = set()
        for _ in range(30):
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
                tenant="test_tenant",
            )
            call_args = mock_paas_facade.invoke_http_in_device.call_args
            device_ids_seen.add(call_args.kwargs["paas_device_id"])
            mock_paas_facade.invoke_http_in_device.reset_mock()

        # With random selection, we should see multiple different devices
        assert len(device_ids_seen) > 1, (
            "Random selection should distribute across devices"
        )

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
            DefaultBotHttpDispatcher,
        )
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.invoke_http_in_device.side_effect = DeviceFacadeException(
            operation="invoke_http_in_device",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
        )

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException):
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
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
            DefaultBotHttpDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._http_dispatcher.get_current_env",
            return_value="test",
        ):
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
                tenant="custom-tenant",
            )

        mock_bot_repo.get_active_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test"
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
        from unittest.mock import MagicMock, patch

        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
        )

        device_no_provider = MagicMock()
        device_no_provider.id = 1
        device_no_provider.device_uuid = "device-no-provider"
        device_no_provider.provider_device_id = None
        device_no_provider.status = "ACTIVE"
        device_no_provider.tenant = "test_tenant"

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [device_no_provider]
        device_no_provider.provider_device_id = None

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
                tenant="test_tenant",
            )

        assert "provider_device_id" in str(exc_info.value)


class TestDispatchBotHttpInvokeWithAffinity:
    """Test dispatch_bot_http_invoke with device_affinity parameter."""

    @pytest.fixture(autouse=True)
    def _mock_env(self):
        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._http_dispatcher.get_current_env",
            return_value="prod",
        ):
            yield

    @pytest.mark.asyncio
    async def test_affinity_returns_same_device(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Same device_affinity returns same device across calls."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
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

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_http_invoke(
            bot_uuid="bot-uuid-001",
            method="GET",
            port=8080,
            path="/api/test",
            query_string=None,
            headers={},
            body=b"",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        first_paas_id = mock_paas_facade.invoke_http_in_device.call_args.kwargs[
            "paas_device_id"
        ]
        mock_paas_facade.invoke_http_in_device.reset_mock()

        await service.dispatch_bot_http_invoke(
            bot_uuid="bot-uuid-001",
            method="GET",
            port=8080,
            path="/api/test",
            query_string=None,
            headers={},
            body=b"",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        second_paas_id = mock_paas_facade.invoke_http_in_device.call_args.kwargs[
            "paas_device_id"
        ]

        assert first_paas_id == second_paas_id, (
            "Same affinity should select same device"
        )

    @pytest.mark.asyncio
    async def test_affinity_none_still_random(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """device_affinity=None falls back to random selection at service level."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
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

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        paas_ids = set()
        for _ in range(30):
            await service.dispatch_bot_http_invoke(
                bot_uuid="bot-uuid-001",
                method="GET",
                port=8080,
                path="/api/test",
                query_string=None,
                headers={},
                body=b"",
                tenant="test_tenant",
            )
            paas_ids.add(
                mock_paas_facade.invoke_http_in_device.call_args.kwargs[
                    "paas_device_id"
                ]
            )
            mock_paas_facade.invoke_http_in_device.reset_mock()

        assert len(paas_ids) > 1, "Without affinity, random selection should distribute"

    @pytest.mark.asyncio
    async def test_passes_request_data_correctly(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Service correctly passes all HTTP request fields to facade."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotHttpDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotHttpDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_http_invoke(
            bot_uuid="bot-uuid-001",
            method="POST",
            port=443,
            path="/api/command",
            query_string="?verbose=true",
            headers={"authorization": "Bearer token123"},
            body=b'{"command": "test"}',
            tenant="test_tenant",
        )

        call_args = mock_paas_facade.invoke_http_in_device.call_args
        assert call_args.kwargs["method"] == "POST"
        assert call_args.kwargs["port"] == 443
        assert call_args.kwargs["path"] == "/api/command"
        assert call_args.kwargs["query_string"] == "?verbose=true"
        assert call_args.kwargs["headers"] == {"authorization": "Bearer token123"}
        assert call_args.kwargs["body"] == b'{"command": "test"}'
