"""Wiring tests for the dormant bot module."""

from unittest.mock import MagicMock
from agentclaw.community.core.repository.protocols.bot.recycle import (
    BotRecycleRepositoryProtocol,
)

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

    bot_service.is_teclaw_bot.return_value = True
    assert adapter.is_teclaw_bot("teclaw") is True
    bot_service.is_teclaw_bot.assert_called_once_with("teclaw")


def test_recycle_operation_and_lifecycle_are_bound_to_the_same_service(test_injector):
    from agentclaw.community.api.bot_dormant_service import (
        BotDormantRecycleOperationProtocol,
    )
    from agentclaw.community.core.bot_management.engines.aicoding.recycle_task import (
        AicodingRecycleOperations,
        AicodingRecycleLifecycle,
    )
    from agentclaw.community.core.repository.implementations.bot.recycle import (
        RecycleRepository,
    )

    service = test_injector.get(AicodingRecycleOperations)
    from agentclaw.community.core.bot_dormant.recycle_service import RecycleBotService
    from agentclaw.community.core.bot_management.engines.recycle_contract import RecycleServices
    assert test_injector.get(BotDormantRecycleOperationProtocol) is test_injector.get(RecycleBotService)
    assert test_injector.get(RecycleServices).operations() is service
    assert isinstance(
        test_injector.get(BotRecycleRepositoryProtocol), RecycleRepository
    )
    assert test_injector.get(AicodingRecycleLifecycle).handler.service is service
