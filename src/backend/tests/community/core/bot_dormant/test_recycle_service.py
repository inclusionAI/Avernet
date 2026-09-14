"""Behavior tests for the shared personal Bot recycle service."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_dormant.recycle_service import (
    RecycleBotService,
    RecycleReleaseFailed,
)
from agentclaw.community.core.bot_dormant.types import BotLifecycleResult
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
)


def _active_personal_bot() -> dict:
    return {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "openclaw",
        "status": "ACTIVE",
    }


def test_recycle_active_personal_bot_releases_resource_before_freezing_passport():
    events: list[str] = []
    bot_service = MagicMock()
    bot_service.get_bot.return_value = _active_personal_bot()
    bot_service.is_teclaw_bot.return_value = False
    bot_service.stop_bot.side_effect = lambda **_: events.append("stop") or True
    bot_service.update_status.side_effect = lambda **_: events.append("status")
    passport = MagicMock()
    passport.freeze_agent_passport.side_effect = lambda **_: events.append("freeze")
    service = RecycleBotService(bot_service=bot_service, passport_plugin=passport)

    result = service.recycle(bot_id="b1", owner_id="u1", owner_name="Tester")

    assert result == BotLifecycleResult(
        bot_id="b1",
        owner_id="u1",
        status="RECYCLED",
        changed=True,
    )
    assert events == ["stop", "status", "freeze"]
    bot_service.stop_bot.assert_called_once_with(
        bot_id="b1",
        user_id="u1",
        nick_name="Tester",
        release_reason="dormant_recycle",
    )
    bot_service.update_status.assert_called_once_with(
        bot_id="b1", user_id="u1", status="RECYCLED"
    )
    passport.freeze_agent_passport.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
        reason="dormant recycle",
    )


def test_recycle_missing_bot_raises_not_found():
    bot_service = MagicMock()
    bot_service.get_bot.return_value = None
    service = RecycleBotService(bot_service=bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotNotFoundError):
        service.recycle(bot_id="missing", owner_id="u1")


def test_recycle_already_recycled_is_idempotent():
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        **_active_personal_bot(),
        "status": "RECYCLED",
    }
    bot_service.is_teclaw_bot.return_value = False
    passport = MagicMock()
    service = RecycleBotService(bot_service=bot_service, passport_plugin=passport)

    result = service.recycle(bot_id="b1", owner_id="u1")

    assert result == BotLifecycleResult(
        bot_id="b1",
        owner_id="u1",
        status="RECYCLED",
        changed=False,
    )
    bot_service.stop_bot.assert_not_called()
    bot_service.update_status.assert_not_called()
    passport.freeze_agent_passport.assert_not_called()


@pytest.mark.parametrize("bot_type", ["desktop", "service"])
def test_recycle_rejects_non_personal_bot(bot_type: str):
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        **_active_personal_bot(),
        "bot_type": bot_type,
    }
    service = RecycleBotService(bot_service=bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotOperationNotAllowedError):
        service.recycle(bot_id="b1", owner_id="u1")

    bot_service.stop_bot.assert_not_called()


def test_recycle_rejects_personal_teclaw_bot():
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        **_active_personal_bot(),
        "active_engine": "teclaw",
    }
    bot_service.is_teclaw_bot.return_value = True
    service = RecycleBotService(bot_service=bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotOperationNotAllowedError):
        service.recycle(bot_id="b1", owner_id="u1")

    bot_service.stop_bot.assert_not_called()


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "REACTIVATING"])
def test_recycle_rejects_non_active_state(status: str):
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        **_active_personal_bot(),
        "status": status,
    }
    bot_service.is_teclaw_bot.return_value = False
    service = RecycleBotService(bot_service=bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotInvalidLifecycleStateError) as exc_info:
        service.recycle(bot_id="b1", owner_id="u1")

    assert exc_info.value.current_status == status
    bot_service.stop_bot.assert_not_called()


def test_recycle_release_failure_does_not_change_status_or_freeze_passport():
    bot_service = MagicMock()
    bot_service.get_bot.return_value = _active_personal_bot()
    bot_service.is_teclaw_bot.return_value = False
    bot_service.stop_bot.return_value = False
    passport = MagicMock()
    service = RecycleBotService(bot_service=bot_service, passport_plugin=passport)

    with pytest.raises(RecycleReleaseFailed):
        service.recycle(bot_id="b1", owner_id="u1")

    bot_service.update_status.assert_not_called()
    passport.freeze_agent_passport.assert_not_called()


def test_recycle_passport_freeze_failure_keeps_completed_recycle():
    bot_service = MagicMock()
    bot_service.get_bot.return_value = _active_personal_bot()
    bot_service.is_teclaw_bot.return_value = False
    bot_service.stop_bot.return_value = True
    passport = MagicMock()
    passport.freeze_agent_passport.side_effect = RuntimeError("tcauth unavailable")
    service = RecycleBotService(bot_service=bot_service, passport_plugin=passport)

    result = service.recycle(bot_id="b1", owner_id="u1")

    assert result.changed is True
    assert result.status == "RECYCLED"
    bot_service.update_status.assert_called_once_with(
        bot_id="b1", user_id="u1", status="RECYCLED"
    )
