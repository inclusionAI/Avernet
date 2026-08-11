"""Tests for ActivateBotService (Task 6).

Scenarios:
  1. reject_active_bot          — ACTIVE → InvalidBotStateError
  2. reactivating_friendly      — REACTIVATING → friendly dict (no update_status call)
  3. recycled_kicks_async       — RECYCLED → update_status(REACTIVATING) + start_bot called
  4. rollback_on_passport_error — passport unfreeze raises → status=RECYCLED,
                                   start_bot NOT called
  5. rollback_on_start_bot_fail — start_bot raises → passport freeze + status=RECYCLED
  6. missing_token_after_unfreeze — token never queryable → RECYCLED before start,
                                   passport left online so a retry can converge
  7. token_settles_on_retry     — token empty then present → bot starts, no freeze
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_dormant import activate_service
from agentclaw.community.core.bot_dormant.activate_service import (
    TOKEN_VERIFY_ATTEMPTS,
    ActivateBotService,
    BotNotFoundError,
    InvalidBotStateError,
)


@pytest.fixture(autouse=True)
def _no_token_verify_sleep(monkeypatch):
    """Collapse the token-verify backoff so tests exercise it without waiting."""
    monkeypatch.setattr(activate_service.time, "sleep", lambda _seconds: None)


def test_activate_reports_missing_bot_through_dormant_contract():
    """A protocol implementation returning no bot raises the stable service error."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = None
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())

    with pytest.raises(BotNotFoundError):
        svc.activate(bot_id="missing", user_id="u1")


def test_activate_rejects_active_bot():
    """Non-RECYCLED/REACTIVATING bot → InvalidBotStateError."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {"bot_id": "b1", "status": "ACTIVE"}
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())
    with pytest.raises(InvalidBotStateError):
        svc.activate(bot_id="b1", user_id="u1")


def test_activate_reactivating_returns_friendly():
    """Already REACTIVATING → friendly return, no state mutation."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {"bot_id": "b1", "status": "REACTIVATING"}
    svc = ActivateBotService(bot_service, passport_plugin=MagicMock())
    result = svc.activate(bot_id="b1", user_id="u1")
    assert result["status"] == "REACTIVATING"
    assert "稍候" in result["message"]
    bot_service.update_status.assert_not_called()


def test_activate_recycled_kicks_async(monkeypatch):
    """RECYCLED → update_status(REACTIVATING) + start_bot called via Thread."""
    bot_service = MagicMock()
    bot_service.get_bot.return_value = {"bot_id": "b1", "status": "RECYCLED"}
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

    result = svc.activate(bot_id="b1", user_id="u1")

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
    assert result["status"] == "REACTIVATING"


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
    bot_service.get_bot.return_value = {"bot_id": "b1", "status": bot_status}
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

    result = svc.activate(bot_id="b1", user_id="u1")

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
    assert result["status"] == "REACTIVATING"


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

    result = svc.activate(bot_id="b1", user_id="u1")

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
    assert result["status"] == "REACTIVATING"


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

    result = svc.activate(bot_id="b1", user_id="u1")

    # Every attempt is spent before the bot is declared un-startable.
    assert passport_mock.query_token.call_count == TOKEN_VERIFY_ATTEMPTS
    passport_mock.query_token.assert_called_with(
        bot_id="b1",
        owner_workno="u1",
    )
    bot_service.start_bot.assert_not_called()
    # The credential stays online: re-freezing would reset the next attempt to
    # the same cold start and strand the bot in RECYCLED forever.
    passport_mock.freeze_agent_passport.assert_not_called()
    bot_service.update_status.assert_called_with(
        bot_id="b1", user_id="u1", status="RECYCLED"
    )
    assert result["status"] == "REACTIVATING"


def test_token_settling_after_unfreeze_starts_the_bot(monkeypatch):
    """A token that only becomes queryable on a later read still activates."""
    passport_mock = MagicMock()
    passport_mock.unfreeze_agent_passport.return_value = None
    # First read races the unfreeze propagation, second one raises, third wins.
    passport_mock.query_token.side_effect = [
        None,
        RuntimeError("passport gateway timeout"),
        "token-b1",
    ]

    svc, bot_service = _make_svc_with_passport("RECYCLED", passport_mock)
    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.activate_service.threading.Thread",
        _SyncThread,
    )

    svc.activate(bot_id="b1", user_id="u1")

    assert passport_mock.query_token.call_count == 3
    bot_service.start_bot.assert_called_once_with(
        bot_id="b1", user_id="u1", nick_name="u1"
    )
    passport_mock.freeze_agent_passport.assert_not_called()
    # No rollback: the last status write is the synchronous REACTIVATING one.
    bot_service.update_status.assert_called_once_with(
        bot_id="b1", user_id="u1", status="REACTIVATING"
    )
