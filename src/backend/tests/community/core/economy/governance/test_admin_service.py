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
    whitelist_service = GovernanceWhitelistService(
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=FakeGovernanceConfig(),
    )
    svc = GovernanceAdminService(
        cache=cache,
        whitelist_service=whitelist_service,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        task_repo=task_repo,
        config=FakeGovernanceConfig(),
        notify_sender=FakeNotifySender(),
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
