"""Tests for GovernanceAdminService — close_all_open, cancel_pending,
pause_ticket, review_ticket, emergency_close, get_state."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.domain.enums import AuditAction
from agentclaw.community.core.economy.governance.repositories.orm import (
    AuditLogOrm,
    GovernanceNotificationOrm,
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from agentclaw.community.core.economy.governance.services.admin_service import (
    GovernanceAdminService,
)
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)

from .conftest import (
    FakeCache,
    FakeDB,
    FakeGovernanceConfig,
    FakeNotifySender,
)


def _make_task_record(session, *, ticket_id, governance_status="open", response=None, **overrides):
    """Create a test GovernanceTicketOrm row.

    When *governance_status* is ``closed``, *active_worker* defaults to
    ``None``; otherwise it defaults to ``worker_id`` (so the UK on
    ``(env, active_worker)`` is satisfied).
    """
    worker_id = overrides.pop("worker_id", f"owner-{ticket_id}:bot-{ticket_id}")
    active_worker = overrides.pop("active_worker", None)
    if active_worker is None:
        active_worker = worker_id if governance_status != "closed" else None

    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker_id,
        bot_id=overrides.pop("bot_id", f"bot-{ticket_id}"),
        owner_id=overrides.pop("owner_id", f"owner-{ticket_id}"),
        bot_name=overrides.pop("bot_name", "TestBot"),
        dt_version=overrides.pop("dt_version", "20260629"),
        governance_decision=overrides.pop("governance_decision", "actionable"),
        governance_status=governance_status,
        latest_decision=overrides.pop("latest_decision", "actionable"),
        active_worker=active_worker,
        last_sync_at=overrides.pop("last_sync_at", datetime.now()),
        response=response,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def _make_notification(session, *, notification_id, notify_status="pending", ticket_id=None, **overrides):
    """Create a test notification row (for cancel_pending_by_ticket tests)."""
    row = GovernanceNotificationOrm(
        notification_id=notification_id,
        bot_id=overrides.pop("bot_id", f"bot-{notification_id}"),
        bot_name=overrides.pop("bot_name", "TestBot"),
        owner_id=overrides.pop("owner_id", "user-1"),
        worker_id=overrides.pop("worker_id", f"user-1:bot-{notification_id}"),
        dt_version="20260629",
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=overrides.pop("governance_status", "open"),
        notify_status=notify_status,
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=1,
        send_attempt_count=1,
        response=overrides.pop("response", None),
        ticket_id=ticket_id,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def _build_svc(engine):
    """Build admin service with in-memory DB + fake cache."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    cache = FakeCache()
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    whitelist_repo = GovernanceWhitelistRepository(db=db)
    # Driver first — lifecycle_service no longer depends on a whitelist
    # service (the accept_feedback whitelist-add is owned by feedback_service),
    # so the whitelist↔driver construction cycle is gone. Build the driver
    # directly, then the whitelist_service (which calls back into the driver).
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    whitelist_service = GovernanceWhitelistService(
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=FakeGovernanceConfig(),
        lifecycle_svc=lifecycle_svc,
    )
    svc = GovernanceAdminService(
        cache=cache,
        whitelist_service=whitelist_service,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        task_repo=task_repo,
        config=FakeGovernanceConfig(),
        notify_sender=FakeNotifySender(),
        lifecycle_svc=lifecycle_svc,
    )
    return svc, db, cache


# ── close_all_open ──────────────────────────────────────────────


class TestCloseAllOpen:
    """Test GovernanceAdminService.close_all_open()."""

    def test_closes_all_open_records(self, session, engine):
        """All open tickets → closed with close_reason=admin_closed."""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-open-1", governance_status="open")
        _make_notification(session, notification_id="n-open-2", governance_status="open")

        result = svc.close_all_open(reason="test", operator="admin")
        assert result.affected == 2

        with db.orm_session() as s:
            rows = s.query(GovernanceNotificationOrm).all()
            for row in rows:
                assert row.governance_status == "closed"
                assert row.close_reason == "admin_closed"
                assert row.closed_at is not None
                assert row.cooldown_until is not None

    def test_closes_scheduled_records(self, session, engine):
        """Scheduled records (e.g. need_time) → also closed."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-sched-1", governance_status="muted",
            response="need_time",
        )

        result = svc.close_all_open(reason="test", operator="admin")
        assert result.affected == 1

        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            assert row.governance_status == "closed"
            assert row.close_reason == "admin_closed"
            assert row.response == "need_time"

    def test_preserves_user_response(self, session, engine):
        """Existing response/response_source are NOT overwritten."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-responded", governance_status="muted",
            response="need_time", response_source="card_callback",
        )

        svc.close_all_open(reason="emergency", operator="admin")

        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            assert row.response == "need_time"
            assert row.response_source == "card_callback"
            assert row.governance_status == "closed"
            assert row.close_reason == "admin_closed"

    def test_cancels_pending_notify_status(self, session, engine):
        """Pending notify_status → cancelled; already-sent → preserved."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-pending",
            notify_status="pending", governance_status="open",
        )
        _make_notification(
            session, notification_id="n-sent",
            notify_status="sent", governance_status="open",
        )

        svc.close_all_open(reason="test", operator="admin")

        with db.orm_session() as s:
            rows = {r.notification_id: r for r in s.query(GovernanceNotificationOrm).all()}
            assert rows["n-pending"].notify_status == "cancelled"
            assert rows["n-sent"].notify_status == "sent"

    def test_skips_closed_records(self, session, engine):
        """Already-closed records are not affected."""
        svc, db, _ = _build_svc(engine)
        _make_notification(
            session, notification_id="n-closed", governance_status="closed",
            close_reason="user_optimized", closed_at=datetime.now(),
        )
        _make_notification(
            session, notification_id="n-open", governance_status="open",
        )

        result = svc.close_all_open(reason="test", operator="admin")
        assert result.affected == 1

        with db.orm_session() as s:
            rows = {r.notification_id: r for r in s.query(GovernanceNotificationOrm).all()}
            assert rows["n-closed"].close_reason == "user_optimized"
            assert rows["n-open"].close_reason == "admin_closed"

    def test_writes_audit(self, session, engine):
        """close_all_open writes AuditLogOrm."""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-1", governance_status="open")

        svc.close_all_open(reason="emergency test", operator="admin-123")

        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            admin_audits = [a for a in audits if a.action_taken == AuditAction.ADMIN_CLOSE_ALL]
            assert len(admin_audits) >= 1

    def test_empty_set_is_idempotent(self, session, engine):
        """No active tickets → returns closed=0, no error."""
        svc, db, _ = _build_svc(engine)

        result = svc.close_all_open(reason="test", operator="admin")
        assert result.affected == 0

    def test_cooldown_applied(self, session, engine):
        """Each closed ticket gets cooldown_until = now + cooldown_days."""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-1", governance_status="open")

        before = datetime.now()
        svc.close_all_open(reason="test", operator="admin")
        after = datetime.now()

        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            expected_min = before + timedelta(days=14)
            expected_max = after + timedelta(days=14)
            assert expected_min <= row.cooldown_until <= expected_max


# ── cancel_pending vs close_all_open distinction ────────────────


class TestCancelPendingVsCloseAllOpen:
    """Verify that cancel_pending uses emergency_closed
    while close_all_open uses admin_closed (with cooldown)."""

    def test_cancel_pending_closes_all_active(self, session, engine):
        """cancel_pending closes unresponded open/muted notifications with emergency_closed."""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-no-resp", governance_status="open")
        _make_notification(
            session, notification_id="n-no-resp-2", governance_status="muted",
        )

        result = svc.cancel_pending(reason="test", operator="admin")
        assert result.affected == 2  # Both closed

        with db.orm_session() as s:
            rows = s.query(GovernanceNotificationOrm).all()
            for row in rows:
                assert row.governance_status == "closed"
                assert row.close_reason == "emergency_closed"

    def test_close_all_open_includes_responded(self, session, engine):
        """close_all_open closes ALL open/muted records, even with response, and applies cooldown."""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-no-resp", governance_status="open")
        _make_notification(
            session, notification_id="n-responded", governance_status="muted",
            response="need_time",
        )

        result = svc.close_all_open(reason="test", operator="admin")
        assert result.affected == 2  # Both closed

        with db.orm_session() as s:
            rows = s.query(GovernanceNotificationOrm).all()
            for row in rows:
                assert row.governance_status == "closed"
                assert row.close_reason == "admin_closed"
                assert row.cooldown_until is not None


# ── Task 8 口径对齐:应急操作通知侧 + 工单侧 双关闭 ───────────────


class TestEmergencyTicketNotifyAlignment:
    """Task 8: cancel_pending / close_all_open 在取消通知投递的同时,
    按口径对齐把对应 task_record 工单主体关闭(修"只关通知、工单留 open"脱钩)。
    两 notify_log_repo 批量方法行为面不动(镜像字段写入保留),仅新增驱动服务
    对 task_record 主体的关闭编排。"""

    def test_close_all_open_closes_ticket_subjects(self, session, engine):
        """close_all_open:open/muted 通知对应的 open/scheduled 工单 → CLOSED(admin_closed)。
        用全量 bulk_close_open(WHERE status IN (open,scheduled) 口径天然对齐通知侧
        governance_status IN (open,muted))。"""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-a", governance_status="open", ticket_id="t-a")
        _make_notification(session, notification_id="n-b", governance_status="muted",
                           response="need_time", ticket_id="t-b")
        _make_task_record(session, ticket_id="t-a", governance_status="open")
        _make_task_record(session, ticket_id="t-b", governance_status="scheduled")  # responded need_time

        result = svc.close_all_open(reason="test", operator="admin")
        assert result.affected == 2  # 通知侧两条

        with db.orm_session() as s:
            tickets = {t.ticket_id: t for t in s.query(GovernanceTicketOrm).all()}
            assert tickets["t-a"].governance_status == "closed"
            assert tickets["t-a"].close_reason == "admin_closed"
            assert tickets["t-b"].governance_status == "closed"
            assert tickets["t-b"].close_reason == "admin_closed"

    def test_cancel_pending_closes_only_unresponded_ticket_subjects(self, session, engine):
        """cancel_pending:仅取消 response IS NULL 的通知,按被关通知的 ticket_id
        集合关工单(逐条 domain guard)—— **不可裸用全量 bulk_close_open**(会
        多关已反馈的 scheduled 单)。已反馈的 scheduled 工单保留(口径精确)。"""
        svc, db, _ = _build_svc(engine)
        # unresponded open → 取消通知 + 关工单(emergency_closed)
        _make_notification(session, notification_id="n-unresp", governance_status="open",
                           ticket_id="t-unresp")
        _make_task_record(session, ticket_id="t-unresp", governance_status="open")
        # responded muted (need_time) → 不在 cancel scope,通知+工单都不动
        _make_notification(session, notification_id="n-responded", governance_status="muted",
                           response="need_time", ticket_id="t-responded")
        _make_task_record(session, ticket_id="t-responded", governance_status="scheduled")

        result = svc.cancel_pending(reason="test", operator="admin")
        assert result.affected == 1  # 仅 n-unresp

        with db.orm_session() as s:
            tickets = {t.ticket_id: t for t in s.query(GovernanceTicketOrm).all()}
            assert tickets["t-unresp"].governance_status == "closed"
            assert tickets["t-unresp"].close_reason == "emergency_closed"
            # 已反馈的 scheduled 单保留 open-status 精确口径(critical:不是 closed)
            assert tickets["t-responded"].governance_status == "scheduled"

    def test_cancel_pending_idempotent_on_already_closed_ticket(self, session, engine):
        """工单已 closed → driver emergency_close 幂等跳过,不重复审计/不报错。"""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-dup", governance_status="open",
                           ticket_id="t-closed")
        _make_task_record(session, ticket_id="t-closed", governance_status="closed")

        result = svc.cancel_pending(reason="test", operator="admin")
        assert result.affected == 1  # 通知侧仍取消

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(ticket_id="t-closed").one()
            assert ticket.governance_status == "closed"  # 状态不变
            assert ticket.close_reason is None  # 既有 close_reason 未被覆盖


# ── get_state includes active_count ───────────────────────────────


class TestGetState:
    """Test that get_state() includes open_count from notify_log."""

    def test_get_state_includes_active_count(self, session, engine):
        """get_state returns open_count for all open/muted notifications."""
        svc, db, _ = _build_svc(engine)
        _make_notification(session, notification_id="n-1", governance_status="open")
        _make_notification(
            session, notification_id="n-2", governance_status="muted",
            response="need_time",
        )

        state = svc.get_state()
        assert state.open_count == 2  # Both open + muted


# ── pause_ticket (§7.5.1) ──────────────────────────────────────


class TestPauseTicket:
    """Test GovernanceAdminService.pause_ticket()."""

    def test_open_to_waiting_review(self, session, engine):
        svc, db, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-pause-1", governance_status="open")

        result = svc.pause_ticket("t-pause-1", admin_id="admin-1", reason="testing")
        assert result.ticket_id == "t-pause-1"
        assert result.status.value == "waiting_review"
        assert result.review_reason == "admin_paused"

    def test_scheduled_to_waiting_review(self, session, engine):
        svc, db, _ = _build_svc(engine)
        _make_task_record(
            session, ticket_id="t-pause-2",
            governance_status="scheduled", response="need_time",
        )

        result = svc.pause_ticket("t-pause-2", admin_id="admin-1")
        assert result.status.value == "waiting_review"

    def test_invalid_status_rejected(self, session, engine):
        svc, db, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-pause-3", governance_status="waiting_review")

        result = svc.pause_ticket("t-pause-3", admin_id="admin-1")
        assert result.error is not None

    def test_not_found(self, session, engine):
        svc, db, _ = _build_svc(engine)
        result = svc.pause_ticket("nonexistent", admin_id="admin-1")
        assert result.error_code == "NOT_FOUND"


# ── review_ticket (§7.5.2) ─────────────────────────────────────


class TestReviewTicket:
    """Test GovernanceAdminService.review_ticket()."""

    def _setup_waiting_review(self, engine):
        svc, db, _ = _build_svc(engine)
        with db.orm_session() as s:
            _make_task_record(
                s, ticket_id="t-review",
                governance_status="waiting_review",
                review_reason="user_optimized",
            )
            s.commit()
        return svc, db

    def test_approve_close(self, session, engine):
        svc, db = self._setup_waiting_review(engine)
        result = svc.review_ticket(
            "t-review", action="approve_close", admin_id="admin-1",
        )
        assert result.status.value == "closed"
        assert result.close_reason == "user_optimized_approved"

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).first()
            assert ticket.cooldown_until is not None

    def test_approve_whitelist(self, session, engine):
        svc, db = self._setup_waiting_review(engine)
        result = svc.review_ticket(
            "t-review", action="approve_whitelist", admin_id="admin-1",
        )
        assert result.close_reason == "whitelist_approved"

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).first()
            assert ticket.cooldown_until is None

    def test_reject_for_reopen(self, session, engine):
        svc, db = self._setup_waiting_review(engine)
        result = svc.review_ticket(
            "t-review", action="reject_for_reopen", admin_id="admin-1",
        )
        assert result.close_reason == "review_rejected"

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).first()
            assert ticket.cooldown_until is None

    def test_invalid_action(self, session, engine):
        svc, db = self._setup_waiting_review(engine)
        result = svc.review_ticket(
            "t-review", action="bad_action", admin_id="admin-1",
        )
        assert result.error is not None

    def test_not_waiting_review_rejected(self, session, engine):
        svc, db, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-review-open", governance_status="open")

        result = svc.review_ticket(
            "t-review-open", action="approve_close", admin_id="admin-1",
        )
        assert result.error is not None

    def test_not_found(self, session, engine):
        svc, db, _ = _build_svc(engine)
        result = svc.review_ticket("nonexistent", action="approve_close", admin_id="admin-1")
        assert result.error_code == "NOT_FOUND"


# ── emergency_close ─────────────────────────────────────────────


class TestEmergencyClose:
    """Test GovernanceAdminService.emergency_close()."""

    def test_open_to_closed(self, session, engine):
        svc, db, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-em-1", governance_status="open")

        result = svc.emergency_close("t-em-1", admin_id="admin-1", reason="urgent")
        assert result.status.value == "closed"
        assert result.close_reason == "emergency_closed"

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).first()
            assert ticket.cooldown_until is None
            assert ticket.active_worker is None

    def test_waiting_review_to_closed(self, session, engine):
        svc, db, _ = _build_svc(engine)
        _make_task_record(
            session, ticket_id="t-em-2", governance_status="waiting_review",
        )

        result = svc.emergency_close("t-em-2", admin_id="admin-1")
        assert result.status.value == "closed"

    def test_not_found(self, session, engine):
        svc, db, _ = _build_svc(engine)
        result = svc.emergency_close("nonexistent", admin_id="admin-1")
        assert result.error_code == "NOT_FOUND"

    def test_emergency_close_audit_actor_is_admin_id(self, session, engine):
        """Group A review §Finding 2 (re-anchored at admin_service after the
        Group B double-audit fix): the emergency-close audit row records the
        admin who executed it (actor_id=admin_id), not the ticket's original
        actor. The driver no longer writes its own audit (de-duped in Group B
        review), so exactly ONE audit row with action=ADMIN_CLOSE_ALL is
        emitted, and it carries actor_id=admin_id.
        """
        svc, db, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-em-audit", governance_status="open")

        result = svc.emergency_close("t-em-audit", admin_id="admin-77", reason="uh")
        assert result.status.value == "closed"

        with db.orm_session() as s:
            emg_audits = [
                a for a in s.query(AuditLogOrm).all()
                if a.action_taken == AuditAction.ADMIN_CLOSE_ALL
            ]
            assert len(emg_audits) == 1, "emergency_close must emit exactly one audit row"
            assert emg_audits[0].actor_id == "admin-77"
            assert "t-em-audit" in (emg_audits[0].error_msg or "")


# ── list_review_tickets / get_review_ticket_detail (评审只读查询) ────


class TestListReviewTickets:
    """list_review_tickets: 按治理状态跨 owner 过滤 + 分页,返回领域模型 + total。"""

    def test_default_statuses_is_all_active(self, session, engine):
        svc, _, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-1", governance_status="open")
        _make_task_record(session, ticket_id="t-2", governance_status="scheduled")
        _make_task_record(session, ticket_id="t-3", governance_status="waiting_review")
        _make_task_record(session, ticket_id="t-4", governance_status="closed")

        tickets, total = svc.list_review_tickets(None, limit=50)
        # 默认 = open+scheduled+waiting_review,closed 排除
        assert total == 3
        assert {t.ticket_id for t in tickets} == {"t-1", "t-2", "t-3"}

    def test_explicit_status_filter(self, session, engine):
        svc, _, _ = _build_svc(engine)
        _make_task_record(
            session, ticket_id="t-1", owner_id="o-A", governance_status="open",
        )
        _make_task_record(
            session, ticket_id="t-2", owner_id="o-B", governance_status="open",
        )
        _make_task_record(
            session, ticket_id="t-3", owner_id="o-C", governance_status="closed",
        )

        tickets, total = svc.list_review_tickets(["closed"], limit=50)
        assert total == 1
        assert tickets[0].ticket_id == "t-3"
        # 跨 owner:命中不同 owner_id
        assert {t.owner_id for t in tickets} == {"o-C"}

    def test_returns_domain_model_not_dict(self, session, engine):
        svc, _, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-1", governance_status="open")

        tickets, _ = svc.list_review_tickets(["open"], limit=50)
        assert len(tickets) == 1
        # 领域模型流转约束:返回 GovernanceTicket,非 dict/ORM
        assert type(tickets[0]).__name__ == "GovernanceTicket"
        # Task 1 链路:gmt_create 经 from_orm 灌入
        assert tickets[0].gmt_create is not None

    def test_paging_offset_limit(self, session, engine):
        svc, _, _ = _build_svc(engine)
        for i in range(5):
            _make_task_record(
                session, ticket_id=f"t-{i}", governance_status="open",
            )
        page1, total = svc.list_review_tickets(["open"], offset=0, limit=2)
        page2, _ = svc.list_review_tickets(["open"], offset=2, limit=2)
        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
        # 两页不重叠
        assert {t.ticket_id for t in page1}.isdisjoint(
            {t.ticket_id for t in page2}
        )

    def test_empty_statuses_means_no_result_not_default(self, session, engine):
        """[] 显式表示无状态匹配 → 空结果,不得回落到全活跃态默认。"""
        svc, _, _ = _build_svc(engine)
        _make_task_record(session, ticket_id="t-1", governance_status="open")
        _make_task_record(session, ticket_id="t-2", governance_status="closed")

        tickets, total = svc.list_review_tickets([], limit=50)
        assert total == 0
        assert tickets == []


class TestGetReviewTicketDetail:
    """get_review_ticket_detail: 单工单领域模型, 不存在返回 None。"""

    def test_found(self, session, engine):
        svc, _, _ = _build_svc(engine)
        _make_task_record(
            session, ticket_id="t-detail", governance_status="waiting_review",
        )
        t = svc.get_review_ticket_detail("t-detail")
        assert t is not None
        assert type(t).__name__ == "GovernanceTicket"
        assert t.governance_status.value == "waiting_review"

    def test_not_found_returns_none(self, session, engine):
        svc, _, _ = _build_svc(engine)
        assert svc.get_review_ticket_detail("nonexistent") is None
