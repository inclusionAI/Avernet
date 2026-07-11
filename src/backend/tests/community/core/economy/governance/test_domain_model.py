"""领域模型单测 — GovernanceNotification / GovernanceTicket / 翻译边界。

验证:
  - 冻结快照不可变性 (FrozenInstanceError)
  - 可变快照可替换(replace + refresh_snapshot)
  - 状态机转换白名单 (IllegalNotifyTransitionError / IllegalTicketTransitionError)
  - 翻译边界: from_orm / to_orm / apply_to
  - sealed 列不泄漏
  - create() 工厂初值
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from types import SimpleNamespace

import pytest

from agentclaw.community.core.economy.governance.domain.notification import FrozenSnapshot, GovernanceNotification, IllegalNotifyTransitionError, NOTIFY_TRANSITIONS

from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord

from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket, IllegalTicketTransitionError, MutableSnapshot, TICKET_TRANSITIONS

from agentclaw.community.core.economy.governance.domain.whitelist import WhitelistEntry
from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
    NotifyStatus,
    NotifyType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(**overrides) -> FrozenSnapshot:
    """构造 FrozenSnapshot,可按需覆盖字段。"""
    defaults = dict(
        dt_version="20260709",
        decision_at_create="actionable",
        triggered_dimensions="token_usage",
        hit_dimensions_count=1,
        severity="P1",
        estimated_saving_tokens=1000,
        saving_ratio=0.25,
        notification_md="# Test",
        notification_structured='{"key": "val"}',
    )
    defaults.update(overrides)
    return FrozenSnapshot(**defaults)


def _make_notification(**overrides) -> GovernanceNotification:
    """构造 GovernanceNotification,可按需覆盖字段。"""
    snapshot = overrides.pop("snapshot", None) or _make_snapshot()
    defaults = dict(
        notification_id="abc123",
        ticket_id="ticket-001",
        bot_id="bot-001",
        bot_name="TestBot",
        owner_id="owner-001",
        worker_id="owner-001:bot-001",
        _snapshot=snapshot,
        delivery_status=NotifyStatus.PENDING,
        channel="markdown",
        notify_type=NotifyType.FIRST_SEND,
        notify_source="online_cron",
        send_attempt_count=0,
        last_send_at=None,
        last_send_error=None,
        external_message_id=None,
        sent_at=None,
    )
    defaults.update(overrides)
    return GovernanceNotification(**defaults)


def _make_orm_obj(**overrides) -> SimpleNamespace:
    """构造模拟 ORM 对象的 SimpleNamespace。

    字段名用 ORM Column 原名(非 business property)。
    """
    defaults = dict(
        notification_id="abc123",
        ticket_id="ticket-001",
        bot_id="bot-001",
        bot_name="TestBot",
        owner_id="owner-001",
        worker_id="owner-001:bot-001",
        dt_version="20260709",
        governance_decision="actionable",
        hit_dimensions="token_usage",
        hit_dimensions_count=1,
        governance_max_priority="P1",
        expected_token_saving=1000,
        saving_ratio=0.25,
        notification_md="# ORM",
        notification_structured='{"orm": true}',
        notify_status="pending",
        notify_channel="markdown",
        notify_type="first_send",
        notify_source="offline_batch",
        send_attempt_count=0,
        last_send_at=None,
        last_send_error=None,
        external_message_id=None,
        sent_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# FrozenSnapshot — 不可变
# ---------------------------------------------------------------------------


class TestFrozenSnapshot:
    """冻结快照:赋值即 FrozenInstanceError。"""

    def test_rejects_mutation(self) -> None:
        snapshot = _make_snapshot()
        with pytest.raises(FrozenInstanceError):
            snapshot.dt_version = "changed"  # type: ignore[misc]

    def test_rejects_null_mutation(self) -> None:
        snapshot = _make_snapshot(severity=None)
        with pytest.raises(FrozenInstanceError):
            snapshot.severity = "P0"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 状态机转换
# ---------------------------------------------------------------------------


class TestNotifyTransitions:
    """状态机白名单 + IllegalNotifyTransitionError。"""

    def test_mark_claimed_pending_to_sending(self) -> None:
        n = _make_notification()
        now = datetime.now()
        n.mark_claimed(now)
        assert n.delivery_status == NotifyStatus.SENDING
        assert n.send_attempt_count == 1
        assert n.last_send_at == now

    def test_mark_sent_sending_to_sent(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.SENDING)
        now = datetime.now()
        n.mark_sent("ext-id-123", now)
        assert n.delivery_status == NotifyStatus.SENT
        assert n.external_message_id == "ext-id-123"
        assert n.sent_at == now
        assert n.last_send_error is None

    def test_mark_failed_terminal(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.SENDING)
        n.mark_failed("timeout", terminal=True)
        assert n.delivery_status == NotifyStatus.FAILED
        assert n.last_send_error == "timeout"

    def test_mark_failed_non_terminal(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.SENDING, send_attempt_count=3)
        n.mark_failed("retry", terminal=False)
        assert n.delivery_status == NotifyStatus.PENDING
        assert n.last_send_error == "retry"
        # attempt_count 不由 mark_failed 改,由 mark_claimed 改
        assert n.send_attempt_count == 3

    def test_transition_from_cancelled_rejected(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.CANCELLED)
        with pytest.raises(IllegalNotifyTransitionError):
            n.transition_to(NotifyStatus.PENDING)

    def test_transition_from_sent_rejected(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.SENT)
        with pytest.raises(IllegalNotifyTransitionError):
            n.transition_to(NotifyStatus.PENDING)

    def test_transition_from_failed_rejected(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.FAILED)
        with pytest.raises(IllegalNotifyTransitionError):
            n.transition_to(NotifyStatus.PENDING)

    def test_pending_to_cancelled(self) -> None:
        n = _make_notification()
        n.transition_to(NotifyStatus.CANCELLED)
        assert n.delivery_status == NotifyStatus.CANCELLED

    def test_sending_to_failed(self) -> None:
        n = _make_notification(delivery_status=NotifyStatus.SENDING)
        n.transition_to(NotifyStatus.FAILED)
        assert n.delivery_status == NotifyStatus.FAILED

    def test_can_send(self) -> None:
        assert _make_notification().can_send() is True
        assert _make_notification(delivery_status=NotifyStatus.SENDING).can_send() is False

    def test_is_pending(self) -> None:
        assert _make_notification().is_pending is True
        assert _make_notification(delivery_status=NotifyStatus.SENT).is_pending is False


# ---------------------------------------------------------------------------
# 工厂: create()
# ---------------------------------------------------------------------------


class TestCreateFactory:
    """create() 初值:delivery_status=PENDING,attempt=0。"""

    def test_create_initial_values(self) -> None:
        snapshot = _make_snapshot()
        n = GovernanceNotification.create(
            notification_id="nid-001",
            ticket_id="tid-001",
            bot_id="bot-001",
            bot_name="Bot",
            owner_id="owner-001",
            worker_id="owner-001:bot-001",
            snapshot=snapshot,
            notify_type=NotifyType.FIRST_SEND,
            notify_source="offline_batch",
            channel="tc_card",
        )
        assert n.delivery_status == NotifyStatus.PENDING
        assert n.send_attempt_count == 0
        assert n.last_send_at is None
        assert n.last_send_error is None
        assert n.external_message_id is None
        assert n.sent_at is None
        assert n.channel == "tc_card"
        assert n.notify_source == "offline_batch"
        assert n.snapshot is snapshot

    def test_create_defaults(self) -> None:
        snapshot = _make_snapshot()
        n = GovernanceNotification.create(
            notification_id="nid",
            ticket_id=None,
            bot_id="b",
            bot_name=None,
            owner_id="o",
            worker_id="o:b",
            snapshot=snapshot,
            notify_type=NotifyType.REMINDER,
        )
        assert n.channel == "markdown"        # default
        assert n.notify_source == "online_cron"  # default


# ---------------------------------------------------------------------------
# 翻译边界: from_orm
# ---------------------------------------------------------------------------


class TestFromOrm:
    """ORM → 领域模型:所有业务字段正确映射,sealed 不泄漏。"""

    def test_roundtrips_business_fields(self) -> None:
        orm_obj = _make_orm_obj()
        n = GovernanceNotification.from_orm(orm_obj)
        assert n.notification_id == "abc123"
        assert n.ticket_id == "ticket-001"
        assert n.bot_id == "bot-001"
        assert n.bot_name == "TestBot"
        assert n.owner_id == "owner-001"
        assert n.worker_id == "owner-001:bot-001"
        # 快照
        assert n.snapshot.dt_version == "20260709"
        assert n.snapshot.decision_at_create == "actionable"
        assert n.snapshot.triggered_dimensions == "token_usage"
        assert n.snapshot.hit_dimensions_count == 1
        assert n.snapshot.severity == "P1"
        assert n.snapshot.estimated_saving_tokens == 1000
        assert n.snapshot.saving_ratio == 0.25
        assert n.snapshot.notification_md == "# ORM"
        assert n.snapshot.notification_structured == '{"orm": true}'
        # 投递态
        assert n.delivery_status == NotifyStatus.PENDING
        assert n.channel == "markdown"
        assert n.notify_type == NotifyType.FIRST_SEND
        assert n.notify_source == "offline_batch"
        assert n.send_attempt_count == 0

    def test_from_orm_handles_nulls(self) -> None:
        """ORM 中可 null 字段正确映射。"""
        orm_obj = _make_orm_obj(
            governance_decision=None,
            hit_dimensions=None,
            hit_dimensions_count=None,
            governance_max_priority=None,
            expected_token_saving=None,
            saving_ratio=None,
            notification_md=None,
            notification_structured=None,
            notify_status=None,
            notify_channel=None,
            notify_type=None,
            notify_source=None,
            send_attempt_count=None,
        )
        n = GovernanceNotification.from_orm(orm_obj)
        assert n.snapshot.decision_at_create == "actionable"  # fallback
        assert n.snapshot.triggered_dimensions is None
        assert n.snapshot.severity is None
        assert n.delivery_status == NotifyStatus.PENDING  # fallback
        assert n.channel == "markdown"                     # fallback
        assert n.send_attempt_count == 0                   # fallback

    def test_sealed_not_leaked(self) -> None:
        """domain 实例没有 sealed 属性。"""
        n = GovernanceNotification.from_orm(_make_orm_obj())
        sealed_attrs = [
            "governance_status", "governance_cycle_id", "response",
            "response_at", "response_remark", "close_reason",
            "closed_at", "cooldown_until", "remind_at", "expire_at",
            "remind_count", "actor_id",
        ]
        for attr in sealed_attrs:
            assert not hasattr(n, attr), f"domain should not have sealed attr: {attr}"


# ---------------------------------------------------------------------------
# 翻译边界: to_orm
# ---------------------------------------------------------------------------


class TestToOrm:
    """领域模型 → ORM:业务字段写入,sealed 列不写。"""

    def test_to_orm_creates_orm_object(self) -> None:
        """to_orm(row=None) 创建新 ORM 对象。"""
        n = _make_notification()
        orm_row = SimpleNamespace()  # 用 SimpleNamespace 模拟
        # to_orm 需要实际 ORM class,这里只验证 to_orm 不抛
        # 真正的 ORM 集成在 repo 层测
        result = n.to_orm()
        assert result.notification_id == "abc123"
        assert result.notify_status == "pending"

    def test_to_orm_does_not_write_sealed_columns(self) -> None:
        """to_orm 不主动写入除 governance_cycle_id 外的 sealed 列。"""
        n = _make_notification()
        orm_row = n.to_orm()
        # governance_cycle_id 是 NOT NULL 约束,to_orm 会回退赋值
        assert orm_row.governance_cycle_id is not None
        # 其余 sealed 列保持 ORM default(None 或 DB default)
        assert orm_row.governance_status is None or orm_row.governance_status == "open"
        assert orm_row.response is None
        assert orm_row.close_reason is None
        assert orm_row.closed_at is None
        assert orm_row.cooldown_until is None
        assert orm_row.remind_at is None
        assert orm_row.expire_at is None
        assert orm_row.remind_count is None or orm_row.remind_count == 0
        assert orm_row.actor_id is None


# ---------------------------------------------------------------------------
# 翻译边界: apply_to
# ---------------------------------------------------------------------------


class TestApplyTo:
    """增量写:只改可变投递态,不碰快照/sealed。"""

    def test_apply_to_updates_delivery_fields(self) -> None:
        """apply_to 只写投递态字段。"""
        n = _make_notification(
            delivery_status=NotifyStatus.SENT,
            send_attempt_count=1,
            external_message_id="ext-123",
            sent_at=datetime(2026, 1, 1),
            last_send_error=None,
        )
        row = SimpleNamespace(
            notify_status="pending",
            send_attempt_count=0,
            last_send_at=None,
            last_send_error="old error",
            external_message_id=None,
            sent_at=None,
            # 快照字段(不应被 touched)
            dt_version="20260709",
            governance_decision="actionable",
            hit_dimensions="token_usage",
        )
        n.apply_to(row)
        assert row.notify_status == "sent"
        assert row.send_attempt_count == 1
        assert row.external_message_id == "ext-123"
        assert row.sent_at == datetime(2026, 1, 1)
        assert row.last_send_error is None

    def test_apply_to_preserves_frozen_snapshot(self) -> None:
        """apply_to 不碰冻结快照字段。"""
        n = _make_notification(delivery_status=NotifyStatus.SENT)
        # row 有快照字段,apply_to 不应改它们
        row = SimpleNamespace(
            notify_status="pending",
            send_attempt_count=0,
            last_send_at=None,
            last_send_error=None,
            external_message_id=None,
            sent_at=None,
            # 快照(不应变)
            dt_version="original_dt",
            governance_decision="original_decision",
            hit_dimensions="original_dims",
        )
        n.apply_to(row)
        assert row.dt_version == "original_dt"
        assert row.governance_decision == "original_decision"
        assert row.hit_dimensions == "original_dims"


# ---------------------------------------------------------------------------
# NOTIFY_TRANSITIONS 表完整性
# ---------------------------------------------------------------------------


class TestTransitionsTable:
    """NOTIFY_TRANSITIONS 覆盖所有 NotifyStatus。"""

    def test_all_statuses_have_entries(self) -> None:
        for status in NotifyStatus:
            assert status in NOTIFY_TRANSITIONS, \
                f"NOTIFY_TRANSITIONS missing entry for {status}"

    def test_terminal_states_have_empty_transitions(self) -> None:
        for status in (NotifyStatus.SENT, NotifyStatus.FAILED, NotifyStatus.CANCELLED):
            assert NOTIFY_TRANSITIONS[status] == frozenset()


# ---------------------------------------------------------------------------
# NotifyStatus enum coercion
# ---------------------------------------------------------------------------


class TestEnumCoercion:
    """NotifyStatus(str, Enum) 与字符串互转。"""

    def test_from_string(self) -> None:
        assert NotifyStatus("pending") == NotifyStatus.PENDING
        assert NotifyStatus("sending") == NotifyStatus.SENDING

    def test_value_roundtrip(self) -> None:
        for status in NotifyStatus:
            assert NotifyStatus(status.value) == status


# ══════════════════════════════════════════════════════════
# GovernanceTicket 领域模型
# ══════════════════════════════════════════════════════════


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ticket_snapshot(**overrides) -> MutableSnapshot:
    """构造 MutableSnapshot,可按需覆盖字段。"""
    defaults = dict(
        dt_version="20260709",
        initial_decision="actionable",
        current_decision="actionable",
        triggered_dimensions="token_usage",
        hit_dimensions_count=1,
        severity="P1",
        estimated_saving_tokens=5000,
        saving_ratio=0.5,
        task_summary="Bot cost high",
        notification_structured='{"dims": ["cost"]}',
        analysis_status="done",
        consecutive_normal_days=0,
        last_decision_dt_version=None,
        last_seen_at=None,
        last_sync_at=datetime(2026, 7, 9, 12, 0, 0),
    )
    defaults.update(overrides)
    return MutableSnapshot(**defaults)


def _make_ticket(**overrides) -> GovernanceTicket:
    """构造 GovernanceTicket,可按需覆盖字段。"""
    snapshot = overrides.pop("snapshot", None) or _make_ticket_snapshot()
    defaults = dict(
        ticket_id="T-001",
        worker_id="owner-1:bot-1",
        bot_id="bot-1",
        owner_id="owner-1",
        bot_name="TestBot",
        _snapshot=snapshot,
        governance_status=GovernanceStatus.OPEN,
        assignee="owner-1:bot-1",
        user_feedback=None,
        feedback_at=None,
        feedback_remark=None,
        feedback_source=None,
        close_reason=None,
        closed_at=None,
        cooldown_until=None,
        review_reason=None,
        review_decision=None,
        reviewed_by=None,
        reviewed_at=None,
        review_remark=None,
        repair_deadline=None,
        resume_at=None,
        remind_at=None,
        remind_count=0,
        feedback_payload=None,
        actor_id=None,
    )
    defaults.update(overrides)
    return GovernanceTicket(**defaults)


def _make_ticket_orm_obj(**overrides) -> SimpleNamespace:
    """构造模拟 ORM 对象的 SimpleNamespace(ORM Column 原名)。"""
    defaults = dict(
        ticket_id="T-100",
        worker_id="owner-1:bot-1",
        bot_id="bot-1",
        owner_id="owner-1",
        bot_name="TestBot",
        dt_version="20260709",
        governance_decision="actionable",
        hit_dimensions="token_usage",
        hit_dimensions_count=1,
        governance_max_priority="P1",
        expected_token_saving=5000,
        saving_ratio=0.5,
        task_summary="summary",
        notification_structured='{"dims": ["cost"]}',
        analysis_status="done",
        last_sync_at=datetime(2026, 7, 9, 12, 0, 0),
        active_worker="owner-1:bot-1",
        governance_status="open",
        response=None,
        response_at=None,
        response_remark=None,
        response_source=None,
        close_reason=None,
        closed_at=None,
        cooldown_until=None,
        review_reason=None,
        review_decision=None,
        reviewed_by=None,
        reviewed_at=None,
        review_remark=None,
        repair_deadline=None,
        mute_until=None,
        last_seen_at=None,
        latest_decision="actionable",
        consecutive_normal_days=0,
        last_decision_dt_version=None,
        remind_at=None,
        remind_count=0,
        feedback_payload=None,
        actor_id=None,
        gmt_create=datetime(2026, 7, 9, 8, 0, 0),
        gmt_modified=datetime(2026, 7, 9, 9, 0, 0),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# MutableSnapshot — 可替换
# ---------------------------------------------------------------------------


class TestMutableSnapshot:
    """可变快照:可赋值,可 refresh_snapshot 替换。"""

    def test_snapshot_is_mutable(self) -> None:
        """MutableSnapshot 非 frozen — 赋值不抛。"""
        snap = _make_ticket_snapshot()
        snap.dt_version = "20260710"  # 不应抛
        assert snap.dt_version == "20260710"

    def test_refresh_snapshot_replaces(self) -> None:
        """GovernanceTicket.refresh_snapshot 创建新快照替换。"""
        t = _make_ticket()
        assert t.dt_version == "20260709"
        t.refresh_snapshot(dt_version="20260710", current_decision="normal", consecutive_normal_days=3)
        assert t.dt_version == "20260710"
        assert t.current_decision == "normal"
        assert t.consecutive_normal_days == 3

    def test_refresh_snapshot_preserves_unspecified(self) -> None:
        """refresh_snapshot 只替换指定字段,其余保留。"""
        t = _make_ticket()
        original_severity = t.severity
        t.refresh_snapshot(dt_version="20260710")
        assert t.severity == original_severity
        assert t.dt_version == "20260710"


# ---------------------------------------------------------------------------
# GovernanceTicket 工厂: create()
# ---------------------------------------------------------------------------


class TestTicketCreateFactory:
    """create() 初值:governance_status=OPEN,assignee=worker_id。"""

    def test_create_initial_values(self) -> None:
        snap = _make_ticket_snapshot()
        t = GovernanceTicket.create(
            ticket_id="T-new",
            worker_id="u1:bot1",
            bot_id="bot1",
            owner_id="u1",
            bot_name="Bot",
            snapshot=snap,
        )
        assert t.governance_status == GovernanceStatus.OPEN
        assert t.assignee == "u1:bot1"  # defaults to worker_id
        assert t.user_feedback is None
        assert t.feedback_at is None
        assert t.close_reason is None
        assert t.closed_at is None
        assert t.remind_count == 0
        assert t.resume_at is None

    def test_create_explicit_assignee(self) -> None:
        snap = _make_ticket_snapshot()
        t = GovernanceTicket.create(
            ticket_id="T-exp",
            worker_id="u1:bot1",
            bot_id="bot1",
            owner_id="u1",
            bot_name="Bot",
            snapshot=snap,
            assignee="custom-assignee",
        )
        assert t.assignee == "custom-assignee"

    def test_create_snapshot_accessible_via_properties(self) -> None:
        snap = _make_ticket_snapshot(
            dt_version="20260708",
            severity="P0",
            estimated_saving_tokens=99999,
        )
        t = GovernanceTicket.create(
            ticket_id="T-props",
            worker_id="u1:bot1",
            bot_id="bot1",
            owner_id="u1",
            bot_name="Bot",
            snapshot=snap,
        )
        assert t.dt_version == "20260708"
        assert t.initial_decision == "actionable"
        assert t.current_decision == "actionable"
        assert t.severity == "P0"
        assert t.estimated_saving_tokens == 99999
        assert t.saving_ratio == 0.5


# ---------------------------------------------------------------------------
# GovernanceTicket 状态机转换
# ---------------------------------------------------------------------------


class TestTicketTransitions:
    """状态机白名单 + IllegalTicketTransitionError。"""

    def test_open_to_scheduled(self) -> None:
        t = _make_ticket()
        t.transition_to(GovernanceStatus.SCHEDULED)
        assert t.governance_status == GovernanceStatus.SCHEDULED

    def test_open_to_waiting_review(self) -> None:
        t = _make_ticket()
        t.transition_to(GovernanceStatus.WAITING_REVIEW)
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW

    def test_open_to_closed(self) -> None:
        t = _make_ticket()
        t.close(close_reason="emergency_closed", closed_at=datetime.now())
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.assignee is None  # closed releases active_worker

    def test_scheduled_to_waiting_review(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.SCHEDULED)
        t.transition_to(GovernanceStatus.WAITING_REVIEW)
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW

    def test_scheduled_to_closed(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.SCHEDULED)
        t.close(close_reason="admin_closed", closed_at=datetime.now())
        assert t.governance_status == GovernanceStatus.CLOSED

    def test_waiting_review_to_open(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW)
        t.resume()
        assert t.governance_status == GovernanceStatus.OPEN

    def test_waiting_review_to_closed(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW)
        t.close(close_reason="review_approved", closed_at=datetime.now())
        assert t.governance_status == GovernanceStatus.CLOSED

    def test_closed_is_terminal(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.CLOSED, assignee=None)
        for target in GovernanceStatus:
            if target == GovernanceStatus.CLOSED:
                continue
            with pytest.raises(IllegalTicketTransitionError):
                t.transition_to(target)

    def test_illegal_transition_raises(self) -> None:
        """CLOSED → 任何状态都是非法的。"""
        t = _make_ticket(governance_status=GovernanceStatus.CLOSED, assignee=None)
        with pytest.raises(IllegalTicketTransitionError, match="closed.*open"):
            t.transition_to(GovernanceStatus.OPEN)


# ---------------------------------------------------------------------------
# GovernanceTicket 业务行为
# ---------------------------------------------------------------------------


class TestTicketAcceptFeedback:
    """accept_feedback:OPEN → SCHEDULED/WAITING_REVIEW。"""

    def test_accept_need_time(self) -> None:
        t = _make_ticket()
        now = datetime.now()
        deadline = datetime(2026, 8, 1)
        t.accept_feedback(
            user_feedback="need_time",
            feedback_at=now,
            feedback_source="card_callback",
            target_status=GovernanceStatus.SCHEDULED,
            repair_deadline=deadline,
            resume_at=datetime(2026, 8, 8),
            review_reason="user_need_time",
        )
        assert t.governance_status == GovernanceStatus.SCHEDULED
        assert t.user_feedback == "need_time"
        assert t.feedback_at == now
        assert t.repair_deadline == deadline
        assert t.resume_at == datetime(2026, 8, 8)
        assert t.review_reason == "user_need_time"

    def test_accept_optimized(self) -> None:
        t = _make_ticket()
        now = datetime.now()
        t.accept_feedback(
            user_feedback="optimized",
            feedback_at=now,
            feedback_source="http_api",
            target_status=GovernanceStatus.WAITING_REVIEW,
            review_reason="user_optimized",
        )
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.user_feedback == "optimized"

    def test_cannot_accept_feedback_if_not_open(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.SCHEDULED)
        assert t.can_accept_feedback() is False

    def test_cannot_accept_feedback_if_already_responded(self) -> None:
        t = _make_ticket(user_feedback="optimized")
        assert t.can_accept_feedback() is False


class TestTicketClose:
    """close: → CLOSED, 释放 assignee, 清 remind_at。"""

    def test_close_sets_fields(self) -> None:
        t = _make_ticket()
        now = datetime.now()
        t.close(close_reason="auto_silenced_normal", closed_at=now, cooldown_until=datetime(2026, 8, 1))
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.close_reason == "auto_silenced_normal"
        assert t.closed_at == now
        assert t.cooldown_until == datetime(2026, 8, 1)
        assert t.assignee is None

    def test_close_clears_remind_at(self) -> None:
        """对齐 repo close_ticket L229(默认 None 清空)。review §LOW 盲区。"""
        t = _make_ticket(remind_at=datetime(2026, 8, 1, 9, 0, 0))
        t.close(close_reason="auto_silenced_normal", closed_at=datetime.now())
        assert t.remind_at is None


class TestTicketAcceptFeedbackRemindAt:
    """accept_feedback 清 remind_at — 对齐 repo L190。review §LOW 盲区。"""

    def test_accept_feedback_clears_remind_at(self) -> None:
        t = _make_ticket(remind_at=datetime(2026, 8, 1, 9, 0, 0))
        now = datetime.now()
        t.accept_feedback(
            user_feedback="optimized",
            feedback_at=now,
            feedback_source="http_api",
            target_status=GovernanceStatus.WAITING_REVIEW,
            review_reason="user_optimized",
        )
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.remind_at is None


class TestTicketPauseResume:
    """pause → WAITING_REVIEW; resume → OPEN。"""

    def test_pause(self) -> None:
        t = _make_ticket()
        t.pause(review_reason="admin_paused")
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.review_reason == "admin_paused"

    def test_pause_clears_remind_at(self) -> None:
        """对齐 repo pause_ticket L272(默认 None 清空)。review §LOW 盲区。"""
        t = _make_ticket(remind_at=datetime(2026, 8, 1, 9, 0, 0))
        t.pause(review_reason="schedule_due")
        assert t.governance_status == GovernanceStatus.WAITING_REVIEW
        assert t.remind_at is None

    def test_resume(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW)
        t.resume()
        assert t.governance_status == GovernanceStatus.OPEN


class TestTicketReview:
    """review():WAITING_REVIEW → CLOSED 三态分支,对齐 repo review_ticket。

    逐字段对齐 repo task_record_repo.review_ticket:
      - approve_close   → close_reason=close_reason|'approve_close', 可带 cooldown_until
      - approve_whitelist→ close_reason='whitelisted'
      - reject_for_reopen→ close_reason='review_rejected'
    共性:无条件清 remind_at(L314)、清 active_worker(L320/327/336/341)、
        closed_at=now(L312/L319/L326/L335)、写 review_decision/reviewed_by/
        reviewed_at/review_remark。
    """

    def test_review_approve_close(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW,
                         remind_at=datetime(2026, 8, 1, 9, 0, 0))
        now = datetime.now()
        t.review(
            review_decision="approve_close",
            reviewed_by="admin-1",
            reviewed_at=now,
            review_remark="ok",
            close_reason="user_optimized_approved",
            cooldown_until=datetime(2026, 9, 1),
        )
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.review_decision == "approve_close"
        assert t.reviewed_by == "admin-1"
        assert t.reviewed_at == now
        assert t.review_remark == "ok"
        assert t.close_reason == "user_optimized_approved"
        assert t.cooldown_until == datetime(2026, 9, 1)
        assert t.assignee is None          # active_worker 释放
        assert t.remind_at is None         # 无条件清(L314)

    def test_review_approve_whitelist(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW,
                         remind_at=datetime(2026, 8, 1, 9, 0, 0))
        t.review(review_decision="approve_whitelist", reviewed_by="admin-1")
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.close_reason == "whitelisted"
        assert t.assignee is None
        assert t.remind_at is None

    def test_review_reject_for_reopen(self) -> None:
        """打回 → 仍 CLOSED(review_rejected),释放 active_worker,下个 scan 重建 open 单。"""
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW,
                         remind_at=datetime(2026, 8, 1, 9, 0, 0))
        t.review(review_decision="reject_for_reopen", reviewed_by="admin-1")
        assert t.governance_status == GovernanceStatus.CLOSED
        assert t.close_reason == "review_rejected"
        assert t.assignee is None
        assert t.remind_at is None

    def test_review_defaults_reviewed_at_to_now(self) -> None:
        t = _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW)
        before = datetime.now()
        t.review(review_decision="approve_close", reviewed_by="admin-1")
        after = datetime.now()
        assert before <= t.reviewed_at <= after
        assert t.closed_at is not None

    def test_review_from_any_active_state(self) -> None:
        """review() 在领域层只受 transition_to 白名单约束(OPEN/SCHEDULED/
        WAITING_REVIEW → CLOSED 均合法);WAITING_REVIEW 限制是 service 层
        业务守卫(admin_service.review_ticket L401),非领域状态机不变量。"""
        for src in (GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED,
                    GovernanceStatus.WAITING_REVIEW):
            t = _make_ticket(governance_status=src,
                             remind_at=datetime(2026, 8, 1, 9, 0, 0))
            t.review(review_decision="approve_close", reviewed_by="admin-1")
            assert t.governance_status == GovernanceStatus.CLOSED
            assert t.remind_at is None

    def test_review_illegal_from_closed(self) -> None:
        """CLOSED → CLOSED 不在白名单(TICKET_TRANSITIONS[CLOSED]=∅),必须拒绝。"""
        t = _make_ticket(governance_status=GovernanceStatus.CLOSED, assignee=None)
        with pytest.raises(IllegalTicketTransitionError):
            t.review(review_decision="approve_close", reviewed_by="admin-1")


class TestTicketBusinessProperties:
    """业务 property:is_open / is_active / is_actionable / has_feedback。"""

    def test_is_open(self) -> None:
        assert _make_ticket().is_open is True
        assert _make_ticket(governance_status=GovernanceStatus.SCHEDULED).is_open is False

    def test_is_active(self) -> None:
        assert _make_ticket().is_active is True
        assert _make_ticket(governance_status=GovernanceStatus.CLOSED, assignee=None).is_active is False
        assert _make_ticket(governance_status=GovernanceStatus.WAITING_REVIEW).is_active is True

    def test_is_actionable(self) -> None:
        assert _make_ticket().is_actionable is True
        t = _make_ticket()
        t.refresh_snapshot(current_decision="normal")
        assert t.is_actionable is False

    def test_has_feedback(self) -> None:
        assert _make_ticket().has_feedback is False
        assert _make_ticket(user_feedback="optimized").has_feedback is True


# ---------------------------------------------------------------------------
# GovernanceTicket 翻译边界: from_orm
# ---------------------------------------------------------------------------


class TestTicketFromOrm:
    """ORM → 领域模型:ORM 列名正确映射到 domain 属性名。"""

    def test_identity_mapping(self) -> None:
        orm_obj = _make_ticket_orm_obj()
        t = GovernanceTicket.from_orm(orm_obj)
        assert t.ticket_id == "T-100"
        assert t.worker_id == "owner-1:bot-1"
        assert t.bot_id == "bot-1"
        assert t.owner_id == "owner-1"
        assert t.bot_name == "TestBot"

    def test_snapshot_field_renaming(self) -> None:
        """ORM 列名 → domain 快照属性名。"""
        orm_obj = _make_ticket_orm_obj()
        t = GovernanceTicket.from_orm(orm_obj)
        # governance_decision → initial_decision
        assert t.initial_decision == "actionable"
        # latest_decision → current_decision
        assert t.current_decision == "actionable"
        # hit_dimensions → triggered_dimensions
        assert t.triggered_dimensions == "token_usage"
        # governance_max_priority → severity
        assert t.severity == "P1"
        # expected_token_saving → estimated_saving_tokens
        assert t.estimated_saving_tokens == 5000

    def test_lifecycle_field_renaming(self) -> None:
        """ORM 列名 → domain 生命周期属性名。"""
        now = datetime.now()
        orm_obj = _make_ticket_orm_obj(
            active_worker="owner-1:bot-1",
            response="optimized",
            response_at=now,
            response_remark="looks good",
            response_source="http_api",
            mute_until=datetime(2026, 8, 1),
            governance_status="scheduled",
        )
        t = GovernanceTicket.from_orm(orm_obj)
        # active_worker → assignee
        assert t.assignee == "owner-1:bot-1"
        # response → user_feedback
        assert t.user_feedback == "optimized"
        # response_at → feedback_at
        assert t.feedback_at == now
        # response_remark → feedback_remark
        assert t.feedback_remark == "looks good"
        # response_source → feedback_source
        assert t.feedback_source == "http_api"
        # mute_until → resume_at
        assert t.resume_at == datetime(2026, 8, 1)
        assert t.governance_status == GovernanceStatus.SCHEDULED

    def test_from_orm_handles_nulls(self) -> None:
        """ORM 中可 null 字段正确映射。"""
        orm_obj = _make_ticket_orm_obj(
            governance_decision=None,
            latest_decision=None,
            hit_dimensions=None,
            hit_dimensions_count=None,
            governance_max_priority=None,
            expected_token_saving=None,
            saving_ratio=None,
            task_summary=None,
            notification_structured=None,
            analysis_status=None,
            governance_status=None,
            active_worker=None,
            response=None,
            remind_count=None,
            consecutive_normal_days=None,
        )
        t = GovernanceTicket.from_orm(orm_obj)
        assert t.initial_decision == "actionable"  # fallback
        assert t.current_decision is None
        assert t.governance_status == GovernanceStatus.OPEN  # fallback
        assert t.assignee is None
        assert t.remind_count == 0  # fallback
        assert t.consecutive_normal_days == 0  # fallback

    def test_sealed_not_leaked(self) -> None:
        """sealed 列(id/env)不泄漏到领域模型;gmt_create/gmt_modified 作为只读元信息保留。"""
        t = GovernanceTicket.from_orm(_make_ticket_orm_obj())
        for attr in ("env", "id"):
            assert not hasattr(t, attr), f"domain should not have sealed attr: {attr}"
        # gmt_create/gmt_modified 现为领域只读元信息,需可读
        assert hasattr(t, "gmt_create")
        assert hasattr(t, "gmt_modified")
        assert t.gmt_create == datetime(2026, 7, 9, 8, 0, 0)

    def test_saving_ratio_float_conversion(self) -> None:
        """Numeric → float 转换。"""
        orm_obj = _make_ticket_orm_obj(saving_ratio="0.75")
        t = GovernanceTicket.from_orm(orm_obj)
        assert t.saving_ratio == 0.75


# ---------------------------------------------------------------------------
# GovernanceTicket 翻译边界: to_orm
# ---------------------------------------------------------------------------


class TestTicketToOrm:
    """领域模型 → ORM:domain 属性名写回 ORM 列名。"""

    def test_to_orm_identity(self) -> None:
        t = _make_ticket()
        orm_row = t.to_orm()
        assert orm_row.ticket_id == "T-001"
        assert orm_row.worker_id == "owner-1:bot-1"
        assert orm_row.bot_id == "bot-1"
        assert orm_row.owner_id == "owner-1"

    def test_to_orm_snapshot_rename(self) -> None:
        """domain 快照属性名 → ORM 列名。"""
        t = _make_ticket()
        orm_row = t.to_orm()
        assert orm_row.governance_decision == "actionable"  # initial_decision
        assert orm_row.latest_decision == "actionable"       # current_decision
        assert orm_row.hit_dimensions == "token_usage"        # triggered_dimensions
        assert orm_row.governance_max_priority == "P1"       # severity
        assert orm_row.expected_token_saving == 5000          # estimated_saving_tokens

    def test_to_orm_lifecycle_rename(self) -> None:
        """domain 生命周期属性名 → ORM 列名。"""
        now = datetime.now()
        t = _make_ticket(
            assignee="owner-1:bot-1",
            user_feedback="need_time",
            feedback_at=now,
            feedback_source="card_callback",
            resume_at=datetime(2026, 8, 1),
        )
        orm_row = t.to_orm()
        assert orm_row.active_worker == "owner-1:bot-1"      # assignee
        assert orm_row.response == "need_time"                # user_feedback
        assert orm_row.response_at == now                     # feedback_at
        assert orm_row.response_source == "card_callback"     # feedback_source
        assert orm_row.mute_until == datetime(2026, 8, 1)     # resume_at


# ---------------------------------------------------------------------------
# GovernanceTicket 翻译边界: apply_to
# ---------------------------------------------------------------------------


class TestTicketApplyTo:
    """增量写:只改生命周期态,不碰快照。"""

    def test_apply_to_updates_lifecycle_fields(self) -> None:
        t = _make_ticket(
            governance_status=GovernanceStatus.WAITING_REVIEW,
            assignee=None,
            user_feedback="dispute",
            feedback_source="http_api",
        )
        row = SimpleNamespace(
            active_worker="old-worker",
            governance_status="open",
            response=None,
            response_at=None,
            response_remark=None,
            response_source=None,
            close_reason=None,
            closed_at=None,
            cooldown_until=None,
            review_reason=None,
            review_decision=None,
            reviewed_by=None,
            reviewed_at=None,
            review_remark=None,
            repair_deadline=None,
            mute_until=None,
            remind_at=None,
            remind_count=0,
            feedback_payload=None,
            actor_id=None,
            # 快照字段(不应被 touched)
            dt_version="original",
            governance_decision="actionable",
            latest_decision="actionable",
        )
        t.apply_to(row)
        assert row.governance_status == "waiting_review"
        assert row.active_worker is None  # assignee=None → active_worker=None
        assert row.response == "dispute"
        assert row.response_source == "http_api"
        # 快照保留
        assert row.dt_version == "original"
        assert row.governance_decision == "actionable"

    def test_apply_to_preserves_snapshot(self) -> None:
        """apply_to 不碰快照字段。"""
        t = _make_ticket(governance_status=GovernanceStatus.SCHEDULED)
        row = SimpleNamespace(
            active_worker="owner-1:bot-1",
            governance_status="open",
            response=None,
            response_at=None,
            response_remark=None,
            response_source=None,
            close_reason=None,
            closed_at=None,
            cooldown_until=None,
            review_reason=None,
            review_decision=None,
            reviewed_by=None,
            reviewed_at=None,
            review_remark=None,
            repair_deadline=None,
            mute_until=None,
            remind_at=None,
            remind_count=0,
            feedback_payload=None,
            actor_id=None,
            # 快照(不应变)
            dt_version="original_dt",
            governance_decision="original_decision",
            latest_decision="original_latest",
            hit_dimensions="original_dims",
        )
        t.apply_to(row)
        assert row.dt_version == "original_dt"
        assert row.governance_decision == "original_decision"
        assert row.latest_decision == "original_latest"
        assert row.hit_dimensions == "original_dims"


# ---------------------------------------------------------------------------
# TICKET_TRANSITIONS 表完整性
# ---------------------------------------------------------------------------


class TestTicketTransitionsTable:
    """TICKET_TRANSITIONS 覆盖所有 GovernanceStatus。"""

    def test_all_statuses_have_entries(self) -> None:
        for status in GovernanceStatus:
            assert status in TICKET_TRANSITIONS, \
                f"TICKET_TRANSITIONS missing entry for {status}"

    def test_closed_is_terminal(self) -> None:
        assert TICKET_TRANSITIONS[GovernanceStatus.CLOSED] == frozenset()

    def test_open_has_most_transitions(self) -> None:
        allowed = TICKET_TRANSITIONS[GovernanceStatus.OPEN]
        assert GovernanceStatus.SCHEDULED in allowed
        assert GovernanceStatus.WAITING_REVIEW in allowed
        assert GovernanceStatus.CLOSED in allowed


# ---------------------------------------------------------------------------
# WhitelistEntry — 翻译边界 + 序列化 + 过期判定 (vertical slice)
# ---------------------------------------------------------------------------


class TestWhitelistEntryTranslation:
    """from_orm / to_orm / to_dict / is_expired 主路径穿透。"""

    def test_from_orm_fills_defaults_for_none_fields(self) -> None:
        """from_orm: source/created_by 为 None 时回退默认值,sealed 列不映射。"""
        orm = SimpleNamespace(
            bot_id="bot-1", owner_id="u-1", whitelist_type="governance",
            source=None, reason=None, created_by=None,
            expires_at=None,
            # sealed 列模拟存在 — from_orm 不应触碰
            id=99, env="dev", gmt_create=None, gmt_modified=None,
        )
        entry = WhitelistEntry.from_orm(orm)
        assert entry.bot_id == "bot-1"
        assert entry.source == "manual"
        assert entry.reason == ""
        assert entry.created_by == ""
        assert entry.is_expired is False  # expires_at None → 永不过期

    def test_to_orm_roundtrip_and_to_dict(self) -> None:
        """to_orm: 把领域模型写回 ORM 行;to_dict: 给前端的 API dict。

        P3 vertical slice: from_orm → to_orm → to_dict 一条路径。
        """
        orm_in = SimpleNamespace(
            bot_id="bot-2", owner_id="u-2", whitelist_type="governance",
            source="admin", reason="auto", created_by="op-1",
            expires_at=datetime(2026, 1, 1, 0, 0, 0),
            id=None, env=None, gmt_create=None, gmt_modified=None,
        )
        entry = WhitelistEntry.from_orm(orm_in)

        # to_orm 写回新 ORM 行 — 用 SimpleNamespace 接收
        sink = SimpleNamespace()
        row = entry.to_orm(sink)
        assert row is sink  # 传 row 时原地写
        assert row.bot_id == "bot-2"
        assert row.expires_at == datetime(2026, 1, 1, 0, 0, 0)

        # to_dict: API 序列化,expires_at 转 ISO
        d = entry.to_dict()
        assert d["bot_id"] == "bot-2"
        assert d["source"] == "admin"
        assert d["expires_at"] == "2026-01-01T00:00:00"

    def test_is_expired_true_for_past_expires_at(self) -> None:
        """is_expired: expires_at 早于 now → True。"""
        entry = WhitelistEntry(
            bot_id="b", owner_id="o", whitelist_type="governance",
            source="manual", reason="", created_by="",
            expires_at=datetime(2000, 1, 1),
        )
        assert entry.is_expired is True

    def test_to_dict_expires_at_none_serializes_to_none(self) -> None:
        """to_dict: expires_at 为 None → JSON null(永久白名单)。"""
        entry = WhitelistEntry(
            bot_id="b", owner_id="o", whitelist_type="governance",
            source="manual", reason="", created_by="",
            expires_at=None,
        )
        assert entry.to_dict()["expires_at"] is None


# ---------------------------------------------------------------------------
# GovernanceRecord — 离线批治理记录领域模型
# ---------------------------------------------------------------------------


class TestGovernanceRecord:
    """GovernanceRecord: 离线批输入载体,身份+数据平铺,不嵌套 snapshot。"""

    def _make(self, **overrides) -> GovernanceRecord:
        defaults = dict(
            owner_id="o-1",
            bot_id="b-1",
            governance_decision="actionable",
            dt_version="20260711",
        )
        defaults.update(overrides)
        return GovernanceRecord(**defaults)

    def test_effective_worker_key_uses_worker_id_when_present(self) -> None:
        """worker_id 有且含 ':' → 用 worker_id(生产者优先,避免重建错配)。"""
        rec = self._make(worker_id="o-1:b-1")
        assert rec.effective_worker_key == "o-1:b-1"

    def test_effective_worker_key_synthesizes_when_missing(self) -> None:
        """worker_id 缺失 → owner_id:bot_id 合成。"""
        rec = self._make()
        assert rec.effective_worker_key == "o-1:b-1"

    def test_effective_worker_key_synthesizes_when_no_colon(self) -> None:
        """worker_id 有但无 ':' → 视为非法,降级合成 owner_id:bot_id。"""
        rec = self._make(worker_id="no-colon-here")
        assert rec.effective_worker_key == "o-1:b-1"

    def test_frozen_immutable(self) -> None:
        """frozen dataclass:赋值即 FrozenInstanceError。"""
        rec = self._make()
        with pytest.raises(FrozenInstanceError):
            rec.owner_id = "mutated"  # type: ignore[misc]

    def test_optional_fields_default_none(self) -> None:
        """可选数据字段缺省 None,不影响构造。"""
        rec = self._make()
        assert rec.worker_id is None
        assert rec.bot_name is None
        assert rec.hit_dimensions is None
        assert rec.saving_ratio is None
        assert rec.task_summary is None

    def test_data_fields_round_trip(self) -> None:
        """数据字段可承载完整记录样貌。"""
        rec = self._make(
            bot_name="TestBot",
            hit_dimensions="token_usage,cost",
            hit_dimensions_count=2,
            governance_max_priority="P1",
            expected_token_saving=5000,
            saving_ratio=0.5,
            task_summary="cost high",
            notification_structured='{"dims":["cost"]}',
            analysis_status="done",
        )
        assert rec.bot_name == "TestBot"
        assert rec.hit_dimensions_count == 2
        assert rec.saving_ratio == 0.5