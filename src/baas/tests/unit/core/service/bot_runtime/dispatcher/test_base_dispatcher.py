# mypy: disable-error-code="arg-type"
"""Unit tests for BotBaseDispatcher._resolve_bot_device monitor log.

Verifies that the structured `Monitor:` info log is emitted on the success
path with all six key=value fields, that `device_affinity=None` is rendered
as the literal string `None`, and that the monitor log is NOT emitted on
the `provider_device_id=None` failure path.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.bot_runtime.dispatcher._base_dispatcher import (
    BotBaseDispatcher,
)
from secbaas.community.core.service.paas import PaasServiceFacade


# ==================== Fixtures ====================


@pytest.fixture
def mock_bot_repo():
    repo = MagicMock()
    repo.get_active_by_bot_uuid = MagicMock(return_value=None)
    return repo


@pytest.fixture
def mock_device_repo():
    repo = MagicMock()
    repo.list_by_bot_id = MagicMock(return_value=[])
    return repo


@pytest.fixture
def mock_paas_facade():
    return AsyncMock(spec=PaasServiceFacade)


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.id = 1
    bot.bot_uuid = "bot-uuid-001"
    bot.tenant = "test_tenant"
    bot.env = "prod"
    bot.status = "ACTIVE"
    return bot


@pytest.fixture
def mock_active_device():
    device = MagicMock()
    device.id = 1
    device.device_uuid = "device-uuid-001"
    device.provider_device_id = "container--machine--user"
    device.status = "ACTIVE"
    device.tenant = "test_tenant"
    device.env = "prod"
    return device


@pytest.fixture
def enable_core_service_propagation():
    """Restore propagation so caplog can capture core-service logger output."""
    logger = logging.getLogger("core-service")
    original = logger.propagate
    logger.propagate = True
    yield logger
    logger.propagate = original


# ==================== Tests ====================


class TestResolveBotDeviceMonitorLog:
    """Tests for the Monitor info log emitted by _resolve_bot_device."""

    @pytest.mark.asyncio
    async def test_monitor_log_emitted_with_all_fields(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        caplog,
        enable_core_service_propagation,
    ):
        """Success path emits Monitor log with all six key=value fields."""
        caplog.set_level("INFO", logger="core-service")

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        dispatcher = BotBaseDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await dispatcher._resolve_bot_device(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            env="prod",
            device_affinity="session-sticky",
        )

        assert result == (mock_bot, mock_active_device, "container--machine--user")

        monitor_msgs = [m for m in caplog.messages if m.startswith("Monitor:")]
        assert len(monitor_msgs) == 1
        msg = monitor_msgs[0]
        assert "bot_uuid=bot-uuid-001" in msg
        assert "tenant=test_tenant" in msg
        assert "env=prod" in msg
        assert "selection_method=consistent_hashing" in msg
        assert "device_affinity=session-sticky" in msg
        assert "provider_device_id=container--machine--user" in msg

    @pytest.mark.asyncio
    async def test_monitor_log_device_affinity_none(
        self,
        mock_bot,
        mock_active_device,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        caplog,
        enable_core_service_propagation,
    ):
        """device_affinity=None renders as the literal string `None`."""
        caplog.set_level("INFO", logger="core-service")

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [mock_active_device]

        dispatcher = BotBaseDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        result = await dispatcher._resolve_bot_device(
            bot_uuid="bot-uuid-001",
            tenant="test_tenant",
            env="prod",
            device_affinity=None,
        )

        assert result == (mock_bot, mock_active_device, "container--machine--user")

        monitor_msgs = [m for m in caplog.messages if m.startswith("Monitor:")]
        assert len(monitor_msgs) == 1
        msg = monitor_msgs[0]
        assert "selection_method=random" in msg
        assert "device_affinity=None" in msg
        assert "provider_device_id=container--machine--user" in msg

    @pytest.mark.asyncio
    async def test_monitor_log_not_emitted_when_provider_device_id_none(
        self,
        mock_bot,
        mock_bot_repo,
        mock_device_repo,
        mock_paas_facade,
        caplog,
        enable_core_service_propagation,
    ):
        """RuntimeError path does not emit the Monitor log."""
        caplog.set_level("INFO", logger="core-service")

        device_no_provider = MagicMock()
        device_no_provider.device_uuid = "device-no-provider"
        device_no_provider.provider_device_id = None
        device_no_provider.status = "ACTIVE"

        mock_bot_repo.get_active_by_bot_uuid.return_value = mock_bot
        mock_device_repo.list_by_bot_id.return_value = [device_no_provider]

        dispatcher = BotBaseDispatcher(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            paas_facade=mock_paas_facade,
        )

        with pytest.raises(RuntimeError):
            await dispatcher._resolve_bot_device(
                bot_uuid="bot-uuid-001",
                tenant="test_tenant",
                env="prod",
            )

        monitor_msgs = [m for m in caplog.messages if m.startswith("Monitor:")]
        assert monitor_msgs == []