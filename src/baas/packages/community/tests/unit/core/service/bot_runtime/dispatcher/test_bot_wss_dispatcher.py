# mypy: disable-error-code="arg-type"
"""Unit tests for DefaultBotWssDispatcher.

Tests the bot WebSocket connection resolution service including:
- Bot lookup by UUID
- Device selection from bot's associated devices
- Random device selection strategy
- PaasServiceFacade integration for WSS resolution
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import (
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
    WsConnectionInfo,
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
    """Mock PaasServiceFacade using AsyncMock with spec for type-safe mocking (D-18.3-07, D-18.3-16)."""
    facade = AsyncMock(spec=PaasServiceFacade)
    facade.resolve_ws_conn_info.return_value = WsConnectionInfo(
        ws_url="wss://agentclawproxy-prod.alipay.com/proxypass/ARCA_test-sandbox@10000:20003/api/openclaw/ws",
        token="test-jwt-token",
        target="ARCA_test-sandbox@10000:20003",
        expires_at=datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC),
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


# ==================== Test Helper Functions ====================


class TestSelectActiveDevice:
    """Test select_active_device helper - random selection from ACTIVE devices."""

    def test_select_single_active_device(self, mock_active_device):
        """When only one ACTIVE device exists, return it."""
        from secbaas.core.service.bot_runtime.dispatcher._device_selector import (
            select_active_device,
        )

        devices = [mock_active_device]
        selected = select_active_device(devices)
        assert selected == mock_active_device

    def test_select_random_from_multiple_active(self):
        """When multiple ACTIVE devices exist, randomly select one."""
        from secbaas.core.service.bot_runtime.dispatcher._device_selector import (
            select_active_device,
        )

        # Create multiple ACTIVE devices
        active_devices = []
        for i in range(3):
            device = MagicMock()
            device.id = i + 1
            device.device_uuid = f"device-uuid-{i}"
            device.status = "ACTIVE"
            active_devices.append(device)

        # Run selection multiple times and verify randomness
        results = set()
        for _ in range(50):
            selected = select_active_device(active_devices)
            assert selected is not None
            assert selected.status == "ACTIVE"
            results.add(selected.id)

        # With 3 devices and 50 runs, we should see more than 1 device selected
        # (probabilistic test - could theoretically fail but extremely unlikely)
        assert len(results) > 1, "Random selection should distribute across devices"

    def test_select_filters_non_active_devices(
        self, mock_active_device, mock_pending_device, mock_failed_device
    ):
        """Only ACTIVE devices are eligible for selection."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices = [mock_pending_device, mock_active_device, mock_failed_device]
        selected = select_active_device(devices)
        assert selected is not None
        assert selected.status == "ACTIVE"
        assert selected.id == mock_active_device.id

    def test_select_returns_none_no_active_devices(
        self, mock_pending_device, mock_failed_device
    ):
        """Returns None when no ACTIVE devices exist."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices = [mock_pending_device, mock_failed_device]
        selected = select_active_device(devices)
        assert selected is None

    def test_select_returns_none_empty_list(self):
        """Returns None when device list is empty."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        selected = select_active_device([])
        assert selected is None


# ==================== Test Consistent Hashing ====================


class TestConsistentHashingSelection:
    """Test consistent hashing for sticky device selection."""

    def test_same_affinity_returns_same_device(self):
        """Same device_affinity value returns the same device across calls."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices = []
        for i in range(3):
            d = MagicMock()
            d.device_uuid = f"device-uuid-00{i}"
            d.status = "ACTIVE"
            devices.append(d)

        result1 = select_active_device(devices, device_affinity="session-abc")
        result2 = select_active_device(devices, device_affinity="session-abc")
        assert result1 is not None
        assert result2 is not None
        assert result1.device_uuid == result2.device_uuid

    def test_different_affinity_may_return_different_device(self):
        """Different device_affinity values may map to different devices."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices = []
        for i in range(4):
            d = MagicMock()
            d.device_uuid = f"device-uuid-00{i}"
            d.status = "ACTIVE"
            devices.append(d)

        results = set()
        for aff in [f"session-{i}" for i in range(50)]:
            sel = select_active_device(devices, device_affinity=aff)
            assert sel is not None
            results.add(sel.device_uuid)

        assert len(results) > 1, "Multiple affinities should distribute across devices"

    def test_affinity_none_falls_back_to_random(self):
        """device_affinity=None keeps random selection unchanged."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices = []
        for i in range(3):
            d = MagicMock()
            d.device_uuid = f"device-uuid-00{i}"
            d.status = "ACTIVE"
            devices.append(d)

        results = set()
        for _ in range(50):
            sel = select_active_device(devices, device_affinity=None)
            assert sel is not None
            results.add(sel.device_uuid)

        assert len(results) > 1, "Random selection should distribute"

    def test_device_removed_from_ring_remaps_clockwise(self):
        """When a device is removed, the affinity maps to the next clockwise device (no crash)."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices_a = []
        for i in range(3):
            d = MagicMock()
            d.device_uuid = f"device-uuid-00{i}"
            d.status = "ACTIVE"
            devices_a.append(d)

        selected = select_active_device(devices_a, device_affinity="test-aff")
        assert selected is not None
        removed_uuid = selected.device_uuid

        devices_b = [d for d in devices_a if d.device_uuid != removed_uuid]
        assert len(devices_b) == 2

        remapped = select_active_device(devices_b, device_affinity="test-aff")
        assert remapped is not None
        assert remapped.device_uuid != removed_uuid

    def test_device_added_limited_reshuffle(self):
        """Adding a device only remaps some affinities, not all."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        def make_device(uuid: str) -> MagicMock:
            d = MagicMock()
            d.device_uuid = uuid
            d.status = "ACTIVE"
            return d

        base_devices = [make_device(f"device-{i}") for i in range(3)]
        affinities = [f"aff-{i}" for i in range(100)]

        base_mapping = {}
        for aff in affinities:
            sel = select_active_device(base_devices, device_affinity=aff)
            assert sel is not None
            base_mapping[aff] = sel.device_uuid

        extended_devices = base_devices + [make_device("device-new")]
        extended_mapping = {}
        for aff in affinities:
            sel = select_active_device(extended_devices, device_affinity=aff)
            assert sel is not None
            extended_mapping[aff] = sel.device_uuid

        changed = sum(
            1 for aff in affinities if base_mapping[aff] != extended_mapping[aff]
        )
        assert changed < len(affinities), "Not all affinities should reshuffle"
        assert changed > 0, "At least one affinity should remap to the new device"

    def test_single_device_always_returns_it(self):
        """With one ACTIVE device, any affinity returns that device."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        d = MagicMock()
        d.device_uuid = "device-solo"
        d.status = "ACTIVE"

        for aff in ["a", "b", "c"]:
            sel = select_active_device([d], device_affinity=aff)
            assert sel is not None
            assert sel.device_uuid == "device-solo"

    def test_filters_non_active_with_affinity(
        self, mock_active_device, mock_pending_device, mock_failed_device
    ):
        """Only ACTIVE devices are eligible for consistent hashing selection."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            select_active_device,
        )

        devices = [mock_pending_device, mock_active_device, mock_failed_device]
        selected = select_active_device(devices, device_affinity="my-session")
        assert selected is not None
        assert selected.status == "ACTIVE"


# ==================== Test Main Service Method ====================


class TestDispatchBotWsConnInfo:
    """Test dispatch_bot_ws_conn_info main method."""

    @pytest.mark.asyncio
    async def test_successful_resolution_with_active_device(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully resolve WSS connection for bot with ACTIVE device."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
        )

        # Setup mocks
        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
        )

        # Verify result
        assert result.ws_url.startswith("wss://")
        assert result.token == "test-jwt-token"
        assert "20003" in result.target

        # Verify facade was called with provider_device_id directly
        mock_paas_facade.resolve_ws_conn_info.assert_awaited_once()
        call_args = mock_paas_facade.resolve_ws_conn_info.call_args
        assert call_args.kwargs["paas_device_id"] == "ARCA-SANDBOX-test-001"
        assert call_args.kwargs["port"] == 20003
        assert call_args.kwargs["path"] == "/api/openclaw/ws"

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_exception(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """BotNotFoundError raised when bot UUID not found."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="nonexistent-bot",
                port=20003,
                path="/api/openclaw/ws",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_devices_found_raises_exception(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """NoDevicesFoundError raised when bot has no devices."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = []

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [
            mock_pending_device,
            mock_failed_device,
        ]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
                tenant="test_tenant",
            )

        assert "No active devices" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_random_selection_with_multiple_active_devices(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Random device selection when multiple ACTIVE devices exist."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
        )

        # Create multiple ACTIVE devices
        active_devices = []
        for i in range(3):
            device = MagicMock()
            device.id = i + 1
            device.device_uuid = f"device-uuid-{i}"
            device.provider_type = "ARCA"
            device.provider_device_id = f"ARCA-SANDBOX-test-{i}"
            device.status = "ACTIVE"
            device.tenant = "test_tenant"
            active_devices.append(device)

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = active_devices

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        # Call multiple times and track which device IDs were used
        device_ids_seen = set()
        for _ in range(30):
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
                tenant="test_tenant",
            )
            # Check the paas_device_id called
            call_args = mock_paas_facade.resolve_ws_conn_info.call_args
            paas_device_id = call_args.kwargs["paas_device_id"]
            # paas_device_id is just the provider_device_id
            device_ids_seen.add(paas_device_id)
            mock_paas_facade.resolve_ws_conn_info.reset_mock()

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
            DefaultBotWssDispatcher,
        )
        from secbaas.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        # Simulate facade error
        mock_paas_facade.resolve_ws_conn_info.side_effect = DeviceFacadeException(
            operation="resolve_ws_conn_info",
            platform_type="ARCA",
            template_id=10000,
            paas_device_id="ARCA-SANDBOX-test-001",
            original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
        )

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException):
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.core.service.bot_runtime.dispatcher._wss_dispatcher.get_current_env",
            return_value="test",
        ):
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
                tenant="custom-tenant",
            )

        # Verify bot lookup used correct tenant/env
        mock_bot_repo.get_active_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test"
        )


# ==================== Test Integration Scenarios ====================


class TestIntegrationScenarios:
    """Integration-like scenarios for the service."""

    @pytest.mark.asyncio
    async def test_full_resolution_flow(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Complete flow from bot UUID to WebSocket connection info."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        # Custom facade response
        mock_paas_facade.resolve_ws_conn_info.return_value = WsConnectionInfo(
            ws_url="wss://agentclawproxy-prod.alipay.com/proxypass/ARCA_test-sandbox@10000:20003/api/openclaw/ws",
            token="eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9...",
            target="ARCA_test-sandbox@10000:20003",
            expires_at=datetime(2026, 4, 24, 15, 0, 0, tzinfo=UTC),
        )

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
        )

        # Verify complete response
        assert (
            result.ws_url
            == "wss://agentclawproxy-prod.alipay.com/proxypass/ARCA_test-sandbox@10000:20003/api/openclaw/ws"
        )
        assert result.token == "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9..."
        assert result.target == "ARCA_test-sandbox@10000:20003"
        assert result.expires_at.year == 2026

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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=8080,
            path="/custom/websocket",
            tenant="test_tenant",
        )

        call_args = mock_paas_facade.resolve_ws_conn_info.call_args
        assert call_args.kwargs["port"] == 8080
        assert call_args.kwargs["path"] == "/custom/websocket"

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
            DefaultBotWssDispatcher,
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

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=8080,
                path="/websocket",
                tenant="test_tenant",
            )

        assert "provider_device_id" in str(exc_info.value)


# ==================== Test Device UUID Targeting ====================


class TestDispatchBotWsConnInfoWithDeviceUuid:
    """Test dispatch_bot_ws_conn_info with device_uuid parameter."""

    @pytest.mark.asyncio
    async def test_specific_device_resolved(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully resolve WS connection for a specific device UUID."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
            device_uuid="device-uuid-001",
        )

        assert result.ws_url.startswith("wss://")
        assert result.token == "test-jwt-token"
        # Verify facade was called with the correct device's provider_device_id
        mock_paas_facade.resolve_ws_conn_info.assert_awaited_once_with(
            paas_device_id="ARCA-SANDBOX-test-001",
            port=20003,
            path="/api/openclaw/ws",
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        # Bot has device-uuid-001, but we request device-uuid-999
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        # Bot has a PENDING device
        mock_device_repo.list_by_bot_id.return_value = [mock_pending_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        # Call without device_uuid (should use existing auto-select flow)
        result = await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
        )

        assert result.ws_url.startswith("wss://")
        assert result.token == "test-jwt-token"

        # Verify the auto-select flow invoked _resolve_bot_device
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
            device_affinity="some-affinity",  # Should be ignored
            device_uuid="device-uuid-001",  # Should take precedence
        )

        assert result.ws_url.startswith("wss://")

        # Verify the device-specific flow was used (not _resolve_bot_device)
        mock_paas_facade.resolve_ws_conn_info.assert_awaited_once_with(
            paas_device_id="ARCA-SANDBOX-test-001",
            port=20003,
            path="/api/openclaw/ws",
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
            DefaultBotWssDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="nonexistent-bot",
                port=20003,
                path="/api/openclaw/ws",
                tenant="test_tenant",
                device_uuid="device-uuid-001",
            )

        assert "nonexistent-bot" in str(exc_info.value)


class TestDispatchBotWsConnInfoWithAffinity:
    """Test dispatch_bot_ws_conn_info with device_affinity parameter."""

    @pytest.mark.asyncio
    async def test_affinity_returns_same_device(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Same device_affinity returns same device across calls."""
        from secbaas.core.service.bot_runtime.dispatcher import (
            DefaultBotWssDispatcher,
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

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        first_paas_id = mock_paas_facade.resolve_ws_conn_info.call_args.kwargs[
            "paas_device_id"
        ]
        mock_paas_facade.resolve_ws_conn_info.reset_mock()

        await service.dispatch_bot_ws_conn_info(
            bot_uuid="bot-uuid-001",
            port=20003,
            path="/api/openclaw/ws",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        second_paas_id = mock_paas_facade.resolve_ws_conn_info.call_args.kwargs[
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
            DefaultBotWssDispatcher,
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

        service = DefaultBotWssDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        paas_ids = set()
        for _ in range(30):
            await service.dispatch_bot_ws_conn_info(
                bot_uuid="bot-uuid-001",
                port=20003,
                path="/api/openclaw/ws",
                tenant="test_tenant",
            )
            paas_ids.add(
                mock_paas_facade.resolve_ws_conn_info.call_args.kwargs["paas_device_id"]
            )
            mock_paas_facade.resolve_ws_conn_info.reset_mock()

        assert len(paas_ids) > 1, "Without affinity, random selection should distribute"
