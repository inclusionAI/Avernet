"""Wiring tests for the dormant bot module."""

from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError as BotManagementNotFoundError,
)
from agentclaw.community.di.modules.bot_dormant_module import _DormantBotServiceAdapter


def test_dormant_bot_service_adapter_normalizes_not_found_and_forwards_operations():
    """The dormant subsystem receives its own stable BotService contract."""
    bot_service = MagicMock()
    adapter = _DormantBotServiceAdapter(bot_service)

    bot_service.get_bot.side_effect = BotManagementNotFoundError("missing")
    assert adapter.get_bot(bot_id="bot-1", user_id="user-1") is None

    bot_service.get_bot.side_effect = None
    bot_service.get_bot.return_value = {"bot_id": "bot-1"}
    assert adapter.get_bot(bot_id="bot-1", user_id="user-1") == {"bot_id": "bot-1"}

    bot_service.update_status.return_value = {"status": "RECYCLED"}
    assert adapter.update_status(bot_id="bot-1", status="RECYCLED") == {
        "status": "RECYCLED"
    }

    bot_service.stop_bot.return_value = {"status": "STOPPED"}
    assert adapter.stop_bot(bot_id="bot-1") == {"status": "STOPPED"}

    bot_service.start_bot.return_value = {"status": "RUNNING"}
    assert adapter.start_bot(bot_id="bot-1") == {"status": "RUNNING"}
