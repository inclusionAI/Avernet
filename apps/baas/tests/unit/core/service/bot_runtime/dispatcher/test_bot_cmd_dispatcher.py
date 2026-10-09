# mypy: disable-error-code="arg-type"
"""Unit tests for DefaultBotCmdDispatcher.

Tests the bot command resolver service including:
- Bot lookup by UUID
- Device selection from bot's associated devices
- Random device selection strategy
- Consistent hashing for sticky device selection
- PaasServiceFacade integration for command execution
- Facade error propagation (failure, timeout, etc.)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_runtime import (
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.api.device_manage import CommandResult
from secbaas.community.core.service.paas import (
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
    facade.execute_command.return_value = CommandResult(
        exit_code=0,
        stdout="deploy complete",
        stderr="",
        execution_time_ms=1500,
        command="echo hello",
    )
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


class TestDispatchBotExecuteCommand:
    """Test dispatch_bot_execute_command main method."""

    @pytest.mark.asyncio
    async def test_successful_execution(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Successfully execute command on bot with ACTIVE device."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_execute_command(
            bot_uuid="bot-uuid-001",
            cmd="echo hello",
            tenant="test_tenant",
        )

        # Verify result
        assert result.exit_code == 0
        assert result.stdout == "deploy complete"
        assert result.stderr == ""

        # Verify facade was called with provider_device_id directly
        mock_paas_facade.execute_command.assert_awaited_once()
        call_args = mock_paas_facade.execute_command.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"
        assert call_args.kwargs["cmd"] == "echo hello"
        assert call_args.kwargs["timeout_seconds"] == 30
        assert call_args.kwargs["env"] is None

    @pytest.mark.asyncio
    async def test_successful_execution_with_env_and_timeout(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Custom cmd_env and timeout_seconds are passed to facade."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_execute_command(
            bot_uuid="bot-uuid-001",
            cmd="deploy.sh",
            tenant="test_tenant",
            cmd_env={"MODE": "prod"},
            timeout_seconds=120,
        )

        assert result.exit_code == 0
        mock_paas_facade.execute_command.assert_awaited_once()
        call_args = mock_paas_facade.execute_command.call_args
        assert call_args.kwargs["env"] == {"MODE": "prod"}
        assert call_args.kwargs["timeout_seconds"] == 120

    @pytest.mark.asyncio
    async def test_facade_returns_non_zero_exit_code(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Non-zero exit code from facade is returned as-is."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.execute_command.return_value = CommandResult(
            exit_code=1,
            stdout="",
            stderr="permission denied",
            execution_time_ms=200,
            command="rm -rf /",
        )

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await service.dispatch_bot_execute_command(
            bot_uuid="bot-uuid-001",
            cmd="rm -rf /",
            tenant="test_tenant",
        )

        assert result.exit_code == 1
        assert result.stderr == "permission denied"

    @pytest.mark.asyncio
    async def test_bot_not_found_raises_exception(
        self, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """BotNotFoundError raised when bot UUID not found."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = None

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(BotNotFoundError) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="nonexistent-bot",
                cmd="test",
                tenant="test_tenant",
            )

        assert "nonexistent-bot" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_devices_found_raises_exception(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """NoDevicesFoundError raised when bot has no devices."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = []

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoDevicesFoundError) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
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
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [
            mock_pending_device,
            mock_failed_device,
        ]

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(NoActiveDevicesError) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
                tenant="test_tenant",
            )

        assert "No active devices" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_random_selection_with_multiple_active_devices(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Random device selection when multiple ACTIVE devices exist."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
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

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        # Call multiple times and track which device IDs were used
        device_ids_seen = set()
        for _ in range(30):
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
                tenant="test_tenant",
            )
            call_args = mock_paas_facade.execute_command.call_args
            device_ids_seen.add(call_args.kwargs["paas_device_id"])
            mock_paas_facade.execute_command.reset_mock()

        # With random selection, we should see multiple different devices
        assert len(device_ids_seen) > 1, (
            "Random selection should distribute across devices"
        )

    @pytest.mark.asyncio
    async def test_facade_error_propagates(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """DeviceFacadeException from PaasServiceFacade propagates correctly."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.execute_command.side_effect = DeviceFacadeException(
            operation="execute_command",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(ErrorCode.COMMAND_FAILED, "Command failed"),
        )

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
                tenant="test_tenant",
            )

        assert exc_info.value.operation == "execute_command"

    @pytest.mark.asyncio
    async def test_facade_timeout_propagates(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Timeout error from PaasServiceFacade propagates as DeviceFacadeException."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.execute_command.side_effect = DeviceFacadeException(
            operation="execute_command",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(ErrorCode.COMMAND_TIMEOUT, "Command timed out"),
        )

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="sleep 100",
                tenant="test_tenant",
                timeout_seconds=5,
            )

        assert exc_info.value.original_error.code == ErrorCode.COMMAND_TIMEOUT

    @pytest.mark.asyncio
    async def test_facade_device_unavailable_propagates(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
    ):
        """Device unavailable error from facade propagates correctly."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        mock_paas_facade.execute_command.side_effect = DeviceFacadeException(
            operation="execute_command",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(
                ErrorCode.DEVICE_UNAVAILABLE, "Device is not available"
            ),
        )

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(DeviceFacadeException) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
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
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        device_no_provider = MagicMock()
        device_no_provider.id = 1
        device_no_provider.device_uuid = "device-no-provider"
        device_no_provider.provider_device_id = None
        device_no_provider.status = "ACTIVE"
        device_no_provider.tenant = "test_tenant"

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [device_no_provider]

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError) as exc_info:
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
                tenant="test_tenant",
            )

        assert "provider_device_id" in str(exc_info.value)
        mock_paas_facade.execute_command.assert_not_called()

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
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with patch(
            "secbaas.community.core.service.bot_runtime.dispatcher._cmd_dispatcher.get_current_env",
            return_value="test",
        ):
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
                tenant="custom-tenant",
            )

        mock_bot_repo.get_active_by_bot_uuid.assert_called_once_with(
            "bot-uuid-001", "custom-tenant", "test"
        )
        mock_device_repo.list_by_bot_id.assert_called_once_with(
            bot_id=mock_bot.id, tenant="custom-tenant", env="test"
        )


# ==================== Test Consistent Hashing for Affinity ====================


class TestDispatchBotExecuteCommandWithAffinity:
    """Test dispatch_bot_execute_command with device_affinity parameter."""

    @pytest.mark.asyncio
    async def test_affinity_returns_same_device(
        self, mock_bot, mock_bot_repo, mock_device_repo, mock_paas_facade
    ):
        """Same device_affinity returns same device across calls."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
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

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_execute_command(
            bot_uuid="bot-uuid-001",
            cmd="test",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        first_paas_id = mock_paas_facade.execute_command.call_args.kwargs[
            "paas_device_id"
        ]
        mock_paas_facade.execute_command.reset_mock()

        await service.dispatch_bot_execute_command(
            bot_uuid="bot-uuid-001",
            cmd="test",
            tenant="test_tenant",
            device_affinity="session-sticky",
        )
        second_paas_id = mock_paas_facade.execute_command.call_args.kwargs[
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
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
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

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        paas_ids = set()
        for _ in range(30):
            await service.dispatch_bot_execute_command(
                bot_uuid="bot-uuid-001",
                cmd="test",
                tenant="test_tenant",
            )
            paas_ids.add(
                mock_paas_facade.execute_command.call_args.kwargs["paas_device_id"]
            )
            mock_paas_facade.execute_command.reset_mock()

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
        """Service correctly passes all command fields to facade."""
        from secbaas.community.core.service.bot_runtime.dispatcher import (
            DefaultBotCmdDispatcher,
        )

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        service = DefaultBotCmdDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        await service.dispatch_bot_execute_command(
            bot_uuid="bot-uuid-001",
            cmd="deploy.sh --verbose",
            tenant="test_tenant",
            cmd_env={"MODE": "prod", "LOG_LEVEL": "debug"},
            timeout_seconds=60,
            device_affinity="my-affinity",
        )

        call_args = mock_paas_facade.execute_command.call_args
        assert call_args.kwargs["paas_device_id"] == "container--machine--user"
        assert call_args.kwargs["cmd"] == "deploy.sh --verbose"
        assert call_args.kwargs["env"] == {"MODE": "prod", "LOG_LEVEL": "debug"}
        assert call_args.kwargs["timeout_seconds"] == 60
