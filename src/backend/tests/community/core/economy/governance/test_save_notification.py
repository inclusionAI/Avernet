"""Unit tests for NotifyLogRepository.save_notification (领域往返写回原语).

收口自 save_ticket 范式:通知发送状态机领域往返的写回原语,只写投递态
(notify_status / send_attempt_count / last_send_at / last_send_error /
external_message_id / sent_at),不碰冻结快照/sealed。

验证:
  1. 正常写回:已存在的 notify_log 行,save 后投递态字段被更新、commit 落库。
  2. 找不到返 False(notification_id 不存在)。
  3. 只动投递态:冻结快照字段(dt_version/governance_decision/hit_dimensions
     /notification_md 等)写入前后不变。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.domain.enums import (
    NotifyType,
)
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.repository.implementations.governance.notify_log import NotifyLogRepository
from agentclaw.community.core.economy.governance.orm import GovernanceNotificationOrm
from tests.community.core.economy.governance.conftest import FakeDB

_ENV_PATCH = (
    "agentclaw.community.core.repository.implementations.governance.notify_log.get_current_env"
)
NOTIFY_ID = "n-save-001"
ENV = "dev"


def _build_repo(engine) -> NotifyLogRepository:
    db = FakeDB(lambda: sessionmaker(bind=engine, expire_on_commit=False)())
    return NotifyLogRepository(db=db)


def _insert_row(engine, *, notify_status: str = "sending", send_attempt_count: int = 1):
    """Insert a baseline notify_log row (sending 态,已 claim 过一次)。"""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        row = GovernanceNotificationOrm(
            notification_id=NOTIFY_ID,
            ticket_id="t-1",
            bot_id="bot-1",
            bot_name="TestBot",
            owner_id="user-1",
            worker_id="user-1:bot-1",
            dt_version="20260629",
            governance_decision="actionable",
            governance_cycle_id="cycle-1",
            governance_status="open",
            notify_status=notify_status,
            latest_decision="actionable",
            consecutive_normal_days=0,
            remind_count=0,
            send_attempt_count=send_attempt_count,
            last_send_at=datetime(2026, 7, 12, 10, 0, 0),
            notification_md="#### md snapshot",
            notification_structured=None,
            env=ENV,
        )
        s.add(row)
        s.commit()
    finally:
        s.close()


def _build_domain_notify(delivery_status: str) -> GovernanceNotification:
    """Build a domain notification carrying snapshot + 投递态,for save round-trip."""
    snap = FrozenSnapshot(
        dt_version="20260629",
        decision_at_create="actionable",
        triggered_dimensions="token_usage",
        hit_dimensions_count=3,
        severity="high",
        estimated_saving_tokens=1000,
        saving_ratio=0.5,
        notification_md="#### md snapshot",
        notification_structured=None,
    )
    notify = GovernanceNotification.create(
        notification_id=NOTIFY_ID,
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
    # 直接走领域守卫方法推进状态(driver 里会这么做,这里手动推进到目标态)。
    # pending → sending(claim)→ sent/failed;不能跳过 sending。
    notify.mark_claimed(datetime(2026, 7, 12, 10, 0, 0))
    if delivery_status == "sent":
        notify.mark_sent(external_message_id="ext-1", sent_at=datetime(2026, 7, 12, 10, 5, 0))
    elif delivery_status == "failed":
        notify.mark_failed("boom", terminal=True)
    return notify


def _fetch_row(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        return s.query(GovernanceNotificationOrm).filter_by(notification_id=NOTIFY_ID).one_or_none()
    finally:
        s.close()


# ── save_notification ─────────────────────────────────────────────────


class TestSaveNotification:
    def test_save_sent_writes_delivery_fields(self, engine, tables):
        _insert_row(engine, notify_status="sending")
        repo = _build_repo(engine)
        notify = _build_domain_notify("sent")  # sending → sent via domain guard

        with patch(_ENV_PATCH, return_value=ENV):
            ok = repo.save_notification(notify)

        assert ok is True
        row = _fetch_row(engine)
        assert row.notify_status == "sent"
        assert row.external_message_id == "ext-1"
        assert row.sent_at == datetime(2026, 7, 12, 10, 5, 0)
        assert row.last_send_error is None

    def test_save_failed_writes_error(self, engine, tables):
        _insert_row(engine, notify_status="sending")
        repo = _build_repo(engine)
        notify = _build_domain_notify("failed")  # sending → failed (terminal)

        with patch(_ENV_PATCH, return_value=ENV):
            ok = repo.save_notification(notify)

        assert ok is True
        row = _fetch_row(engine)
        assert row.notify_status == "failed"
        assert row.last_send_error == "boom"

    def test_save_not_found_returns_false(self, engine, tables):
        # 不插行,直接 save 一个领域模型
        repo = _build_repo(engine)
        notify = _build_domain_notify("sent")

        with patch(_ENV_PATCH, return_value=ENV):
            ok = repo.save_notification(notify)

        assert ok is False

    def test_save_preserves_frozen_snapshot_fields(self, engine, tables):
        """save_notification 只写投递态,冻结快照字段(dt_version/
        governance_decision/notification_md 等)写入前后不变。"""
        _insert_row(engine, notify_status="sending")
        repo = _build_repo(engine)
        notify = _build_domain_notify("sent")

        before = _fetch_row(engine)
        with patch(_ENV_PATCH, return_value=ENV):
            repo.save_notification(notify)
        after = _fetch_row(engine)

        # 冻结快照字段不动
        assert after.dt_version == before.dt_version == "20260629"
        assert after.governance_decision == before.governance_decision == "actionable"
        assert after.notification_md == before.notification_md == "#### md snapshot"
        assert after.bot_id == before.bot_id == "bot-1"
        assert after.owner_id == before.owner_id == "user-1"
        # 投递态确已改
        assert after.notify_status == "sent"
        assert before.notify_status == "sending"

    def test_save_persists_notify_channel(self, engine, tables):
        """apply_to 必须把领域模型的 channel 写回 notify_channel 列。

        channel 是可变投递态字段(例如 tc_card 构建失败降级 markdown 时模型
        channel 可能被改写);apply_to 漏写该列会导致 save_notification 静默丢失
        渠道变更。此处锁死该写回契约。
        """
        _insert_row(engine, notify_status="sending")  # 行里 notify_channel 默认 None
        repo = _build_repo(engine)
        notify = _build_domain_notify("sent")  # 领域模型 channel="markdown"

        with patch(_ENV_PATCH, return_value=ENV):
            repo.save_notification(notify)
        row = _fetch_row(engine)

        assert row.notify_channel == "markdown"