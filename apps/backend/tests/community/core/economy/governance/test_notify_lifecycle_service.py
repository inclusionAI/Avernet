"""Tests for NotifyLifecycleService — 正常路径领域往返驱动(脱 DB,Mock repo)。

验证:
  1. claim:claim_pending CAS 成功 → re-read 返领域模型;CAS 失败 → None。
  2. mark_sent:read → notify.mark_sent()(guard)→ save;正常/找不到/非法转移返 False。
  3. mark_failed:read → notify.mark_failed(terminal=…)→ save;terminal 双分支/找不到/非法返 False。
  4. 领域守卫真被 invoke:对已 sent 的通知再 mark_sent → IllegalNotifyTransitionError 被
     catch → 返 False(driver 不 raise,记 log)。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.economy.governance.domain.enums import (
    NotifyStatus,
    NotifyType,
)
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.economy.governance.services.notify_lifecycle_service import (
    NotifyLifecycleService,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_notify(delivery_status: NotifyStatus = NotifyStatus.PENDING) -> GovernanceNotification:
    """Build a domain notification, optionally advanced to a target state via guards."""
    snap = FrozenSnapshot(
        dt_version="20260705",
        decision_at_create="actionable",
        triggered_dimensions="token_usage",
        hit_dimensions_count=3,
        severity="high",
        estimated_saving_tokens=1000,
        saving_ratio=0.5,
        notification_md="#### md",
        notification_structured=None,
    )
    notify = GovernanceNotification.create(
        notification_id="n-1",
        ticket_id="t-1",
        bot_id="bot-1",
        bot_name="TestBot",
        owner_id="user-1",
        worker_id="user-1:bot-1",
        snapshot=snap,
        notify_type=NotifyType.FIRST_SEND,
        notify_source="online_cron",
        channel="markdown",
    )
    if delivery_status == NotifyStatus.SENDING:
        notify.mark_claimed(datetime(2026, 7, 12, 10, 0, 0))
    elif delivery_status == NotifyStatus.SENT:
        notify.mark_claimed(datetime(2026, 7, 12, 10, 0, 0))
        notify.mark_sent("ext-1", datetime(2026, 7, 12, 10, 5, 0))
    return notify


def _mock_repo(notify: GovernanceNotification | None = None, *, claim_ok: bool = True,
               save_ok: bool = True) -> MagicMock:
    repo = MagicMock()
    repo.claim_pending.return_value = claim_ok
    repo.get_by_notification_id.return_value = notify
    repo.save_notification.return_value = save_ok
    return repo


@pytest.fixture
def svc():
    return lambda repo: NotifyLifecycleService(notify_repo=repo)


NOW = datetime(2026, 7, 12, 10, 0, 0)


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


class TestClaim:
    def test_claim_success_returns_sending_model(self, svc):
        sending = _make_notify(NotifyStatus.SENDING)
        repo = _mock_repo(notify=sending, claim_ok=True)
        result = svc(repo).claim("n-1", now=NOW)
        repo.claim_pending.assert_called_once_with("n-1", NOW)
        repo.get_by_notification_id.assert_called_once_with("n-1")
        assert result is sending

    def test_claim_cas_failed_returns_none(self, svc):
        # claim_pending 返 False(被并发抢/已非 pending)→ 不 re-read,返 None
        sending = _make_notify(NotifyStatus.SENDING)
        repo = _mock_repo(notify=sending, claim_ok=False)
        result = svc(repo).claim("n-1", now=NOW)
        assert result is None
        repo.claim_pending.assert_called_once()
        repo.get_by_notification_id.assert_not_called()


# ---------------------------------------------------------------------------
# mark_sent
# ---------------------------------------------------------------------------


class TestMarkSent:
    def test_mark_sent_success(self, svc):
        sending = _make_notify(NotifyStatus.SENDING)
        repo = _mock_repo(notify=sending, save_ok=True)
        ok = svc(repo).mark_sent("n-1", external_message_id="ext-1", sent_at=NOW)
        assert ok is True
        assert sending.delivery_status == NotifyStatus.SENT
        assert sending.external_message_id == "ext-1"
        repo.save_notification.assert_called_once_with(sending)

    def test_mark_sent_not_found(self, svc):
        repo = _mock_repo(notify=None)
        ok = svc(repo).mark_sent("n-1", external_message_id="ext-1", sent_at=NOW)
        assert ok is False
        repo.save_notification.assert_not_called()

    def test_mark_sent_illegal_transition_returns_false(self, svc):
        """对已 sent 的通知再 mark_sent → 领域守卫抛 IllegalNotifyTransitionError
        → driver catch,返 False,不 raise、不 save。"""
        sent = _make_notify(NotifyStatus.SENT)  # 已终态 sent
        repo = _mock_repo(notify=sent)
        ok = svc(repo).mark_sent("n-1", external_message_id="ext-2", sent_at=NOW)
        assert ok is False
        repo.save_notification.assert_not_called()


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    def test_mark_failed_terminal_goes_to_failed(self, svc):
        sending = _make_notify(NotifyStatus.SENDING)
        repo = _mock_repo(notify=sending, save_ok=True)
        ok = svc(repo).mark_failed("n-1", error="boom", terminal=True)
        assert ok is True
        assert sending.delivery_status == NotifyStatus.FAILED
        assert sending.last_send_error == "boom"
        repo.save_notification.assert_called_once()

    def test_mark_failed_non_terminal_goes_to_pending(self, svc):
        sending = _make_notify(NotifyStatus.SENDING)
        repo = _mock_repo(notify=sending, save_ok=True)
        ok = svc(repo).mark_failed("n-1", error="retry", terminal=False)
        assert ok is True
        assert sending.delivery_status == NotifyStatus.PENDING
        assert sending.last_send_error == "retry"

    def test_mark_failed_not_found(self, svc):
        repo = _mock_repo(notify=None)
        ok = svc(repo).mark_failed("n-1", error="boom", terminal=True)
        assert ok is False
        repo.save_notification.assert_not_called()

    def test_mark_failed_illegal_transition_returns_false(self, svc):
        """对 pending(未 claim)通知直接 mark_failed → illegal(sending→failed/pending
        才合法)→ 返 False。"""
        pending = _make_notify(NotifyStatus.PENDING)
        repo = _mock_repo(notify=pending)
        ok = svc(repo).mark_failed("n-1", error="boom", terminal=True)
        assert ok is False
        repo.save_notification.assert_not_called()