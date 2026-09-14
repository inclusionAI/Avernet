"""Behavior tests for the shared personal Bot activation service."""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_dormant.activate_service import (
    ActivateBotService,
)
from agentclaw.community.core.bot_dormant.types import BotLifecycleResult
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
)


def test_activate_reports_missing_bot_through_dormant_contract():
    """A protocol implementation returning no bot raises the stable service error."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = None
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotNotFoundError):
        svc.activate(bot_id="missing", owner_id="u1")


def test_activate_active_bot_is_idempotent():
    """ACTIVE returns the current state without dispatching another start."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "openclaw",
        "status": "ACTIVE",
    }
    bot_service.is_teclaw_bot.return_value = False
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())

    result = svc.activate(bot_id="b1", owner_id="u1")

    assert result == BotLifecycleResult(
        bot_id="b1",
        owner_id="u1",
        status="ACTIVE",
        changed=False,
    )
    bot_service.update_status.assert_not_called()
    bot_service.start_bot.assert_not_called()


def test_activate_rejects_personal_teclaw_bot():
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "teclaw",
        "status": "RECYCLED",
    }
    bot_service.is_teclaw_bot.return_value = True
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotOperationNotAllowedError):
        svc.activate(bot_id="b1", owner_id="u1")

    bot_service.update_status.assert_not_called()
    bot_service.start_bot.assert_not_called()


@pytest.mark.parametrize("bot_type", ["desktop", "service"])
def test_activate_rejects_non_personal_bot(bot_type: str):
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": bot_type,
        "active_engine": "openclaw",
        "status": "RECYCLED",
    }
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotOperationNotAllowedError):
        svc.activate(bot_id="b1", owner_id="u1")


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "OFFLINE"])
def test_activate_rejects_other_lifecycle_states(status: str):
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "openclaw",
        "status": status,
    }
    bot_service.is_teclaw_bot.return_value = False
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotInvalidLifecycleStateError) as exc_info:
        svc.activate(bot_id="b1", owner_id="u1")

    assert exc_info.value.current_status == status


def test_activate_reactivating_returns_friendly():
    """Already REACTIVATING returns an unchanged typed result."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "openclaw",
        "status": "REACTIVATING",
    }
    bot_service.is_teclaw_bot.return_value = False
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())
    result = svc.activate(bot_id="b1", owner_id="u1")
    assert result == BotLifecycleResult(
        bot_id="b1",
        owner_id="u1",
        status="REACTIVATING",
        changed=False,
    )
    bot_service.update_status.assert_not_called()


def test_activate_recycled_kicks_async(monkeypatch):
    """RECYCLED → update_status(REACTIVATING) + start_bot called via Thread."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "openclaw",
        "status": "RECYCLED",
    }
    bot_service.is_teclaw_bot.return_value = False
    passport_mock = MagicMock()
    passport_mock.unfreeze_agent_passport.return_value = None
    passport_mock.query_token.return_value = "token-b1"

    svc = ActivateBotService(bot_service, passport_plugin=passport_mock)

    # Patch threading.Thread so it runs synchronously (no daemon race in tests)
    class _SyncThread:
        def __init__(self, target, args=(), daemon=False, **kw):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.activate_service.threading.Thread",
        _SyncThread,
    )

    result = svc.activate(bot_id="b1", owner_id="u1", owner_name="Tester")

    # Synchronous part: status set to REACTIVATING immediately
    bot_service.update_status.assert_any_call(
        bot_id="b1", user_id="u1", status="REACTIVATING"
    )
    # Async part (ran synchronously): start_bot invoked
    passport_mock.unfreeze_agent_passport.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
        reason="manual reactivate",
    )
    passport_mock.query_token.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
    )
    bot_service.start_bot.assert_called_once()
    # Return value is REACTIVATING
    assert result == BotLifecycleResult(
        bot_id="b1",
        owner_id="u1",
        status="REACTIVATING",
        changed=True,
    )
    bot_service.start_bot.assert_called_once_with(
        bot_id="b1", user_id="u1", nick_name="Tester"
    )


# ---------------------------------------------------------------------------
# Rollback path helpers
# ---------------------------------------------------------------------------

class _SyncThread:
    """Replaces threading.Thread so the async target runs synchronously in tests."""
    def __init__(self, target, args=(), daemon=False, **kw):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _make_svc_with_passport(bot_status: str, passport_mock: MagicMock):
    """Build an ActivateBotService with a pre-configured bot_service + passport mock."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "bot_type": "personal",
        "active_engine": "openclaw",
        "status": bot_status,
    }
    bot_service.is_teclaw_bot.return_value = False
    return ActivateBotService(bot_service=bot_service, passport_plugin=passport_mock), bot_service


# ---------------------------------------------------------------------------
# Rollback test 1: passport unfreeze fails → status rolled back, start_bot skipped
# ---------------------------------------------------------------------------

def test_rollback_on_passport_error(monkeypatch):
    """passport unfreeze raises → update_status(RECYCLED), start_bot NOT called."""
    passport_mock = MagicMock()
    passport_mock.unfreeze_agent_passport.side_effect = RuntimeError("quota exceeded")

    svc, bot_service = _make_svc_with_passport("RECYCLED", passport_mock)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.activate_service.threading.Thread",
        _SyncThread,
    )

    result = svc.activate(bot_id="b1", owner_id="u1")

    # Synchronous step: status must have been set to REACTIVATING first
    bot_service.update_status.assert_any_call(
        bot_id="b1", user_id="u1", status="REACTIVATING"
    )
    # Rollback: status must be restored to RECYCLED
    bot_service.update_status.assert_called_with(
        bot_id="b1", user_id="u1", status="RECYCLED"
    )
    # start_bot must NOT have been called (unfreeze never succeeded)
    bot_service.start_bot.assert_not_called()
    # Caller still gets REACTIVATING (rollback is async/transparent to caller)
    assert result.status == "REACTIVATING"


# ---------------------------------------------------------------------------
# Rollback test 2: start_bot fails → passport freeze called + status rolled back
# ---------------------------------------------------------------------------

def test_rollback_on_start_bot_failure(monkeypatch):
    """start_bot raises → passport freeze (rollback) + update_status(RECYCLED)."""
    passport_mock = MagicMock()
    passport_mock.unfreeze_agent_passport.return_value = None
    passport_mock.query_token.return_value = "token-b1"
    passport_mock.freeze_agent_passport.return_value = None

    svc, bot_service = _make_svc_with_passport("RECYCLED", passport_mock)
    bot_service.start_bot.side_effect = RuntimeError("engine down")

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.activate_service.threading.Thread",
        _SyncThread,
    )

    result = svc.activate(bot_id="b1", owner_id="u1")

    # unfreeze must have been called (and succeeded)
    passport_mock.unfreeze_agent_passport.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
        reason="manual reactivate",
    )
    # Rollback: freeze must be called to re-lock the passport
    passport_mock.freeze_agent_passport.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
        reason="reactivate rollback",
    )
    # Rollback: status restored to RECYCLED
    bot_service.update_status.assert_called_with(
        bot_id="b1", user_id="u1", status="RECYCLED"
    )
    # Caller still gets REACTIVATING
    assert result.status == "REACTIVATING"


def test_missing_token_after_unfreeze_rolls_back_before_start(monkeypatch):
    """Online success without a queryable token must not start the bot."""
    passport_mock = MagicMock()
    passport_mock.unfreeze_agent_passport.return_value = None
    passport_mock.query_token.return_value = None
    passport_mock.freeze_agent_passport.return_value = None

    svc, bot_service = _make_svc_with_passport("RECYCLED", passport_mock)
    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.activate_service.threading.Thread",
        _SyncThread,
    )

    result = svc.activate(bot_id="b1", owner_id="u1")

    passport_mock.query_token.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
    )
    bot_service.start_bot.assert_not_called()
    passport_mock.freeze_agent_passport.assert_called_once_with(
        bot_id="b1",
        owner_workno="u1",
        reason="reactivate rollback",
    )
    bot_service.update_status.assert_called_with(
        bot_id="b1", user_id="u1", status="RECYCLED"
    )
    assert result.status == "REACTIVATING"
