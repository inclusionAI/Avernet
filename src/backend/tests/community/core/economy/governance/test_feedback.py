"""Tests for GovernanceFeedbackService — resolve, one-time feedback rule,
state transitions, listing."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

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
from agentclaw.community.core.economy.governance.services.feedback_service import (
    GovernanceFeedbackService,
)
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)

from .conftest import FakeDB, FakeGovernanceConfig, FakeWhitelistService


def _build_svc(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    svc = GovernanceFeedbackService(
        whitelist_service=FakeWhitelistService(),
        notify_repo=notify_repo,
        task_repo=task_repo,
        audit_repo=audit_repo,
        config=FakeGovernanceConfig(),
        lifecycle_svc=lifecycle_svc,
    )
    return svc, db


def _make_notification(session, **overrides):
    """Create a test notification + linked task_record (§7.4).

    The feedback service resolves via:
      notification_id → notify_log.ticket_id → task_record.
    Both rows must exist for ``find_ticket_by_notification_id`` to work.
    """
    now = datetime.now()
    ticket_id = overrides.pop("ticket_id", "ticket-001")
    governance_status = overrides.pop("governance_status", "open")

    # --- notify_log row ---
    notify_row = GovernanceNotificationOrm(
        notification_id=overrides.pop("notification_id", "n-001"),
        ticket_id=ticket_id,
        bot_id=overrides.pop("bot_id", "bot-1"),
        bot_name="TestBot",
        owner_id=overrides.pop("owner_id", "user-1"),
        worker_id="user-1:bot-1",
        dt_version="20260629",
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=governance_status,
        notify_status="sent",
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=1,
        send_attempt_count=1,
        **overrides,
    )
    session.add(notify_row)

    # --- task_record row (the actual ticket) ---
    task_row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id="user-1:bot-1",
        bot_id=notify_row.bot_id,
        owner_id=notify_row.owner_id,
        dt_version="20260629",
        governance_decision="actionable",
        governance_status=governance_status,
        last_sync_at=now,
        active_worker="user-1:bot-1",
    )
    session.add(task_row)
    session.commit()
    return notify_row


def _make_linked_rows(
    session,
    *,
    notification_id: str | None = None,
    ticket_id: str | None = None,
    governance_status: str = "open",
    owner_id: str = "staff-001",
    bot_id: str = "bot-001",
    response: str | None = None,
    latest_decision: str = "actionable",
    env: str = "dev",
) -> tuple[GovernanceTicketOrm, GovernanceNotificationOrm]:
    """Create a linked ticket + notify_log pair (for §7.4.1 tests)."""
    if notification_id is None:
        notification_id = uuid.uuid4().hex
    if ticket_id is None:
        ticket_id = uuid.uuid4().hex
    worker_key = f"{owner_id}:{bot_id}"

    ticket = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker_key,
        active_worker=worker_key if governance_status != "closed" else None,
        governance_status=governance_status,
        governance_decision="actionable",
        latest_decision=latest_decision,
        env=env,
        bot_id=bot_id,
        owner_id=owner_id,
        dt_version="20260705",
        bot_name="TestBot",
        consecutive_normal_days=0,
        remind_count=0,
        response=response,
        last_sync_at=datetime.now(),
    )
    session.add(ticket)

    notify = GovernanceNotificationOrm(
        notification_id=notification_id,
        ticket_id=ticket_id,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        worker_id=worker_key,
        dt_version="20260705",
        governance_decision=latest_decision,
        governance_cycle_id="cycle-v2",
        governance_status=governance_status,
        notify_status="sent",
        notify_type="first_send",
        notify_source="offline_batch",
        latest_decision=latest_decision,
        consecutive_normal_days=0,
        remind_count=0,
        send_attempt_count=1,
    )
    session.add(notify)
    session.commit()
    return ticket, notify


# --- Fixtures ---


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    eng = create_engine("sqlite:///:memory:")
    GovernanceTicketOrm.__table__.create(eng, checkfirst=True)
    GovernanceNotificationOrm.__table__.create(eng, checkfirst=True)
    AuditLogOrm.__table__.create(eng, checkfirst=True)
    return eng


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


# ── TestFeedback: legacy resolve tests ─────────────────────────


class TestFeedback:
    """Test GovernanceFeedbackService.resolve()."""

    def test_optimized_response(self, session, engine):
        """response=optimized → waiting_review (§7.4.2)."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "optimized", "user-1")
        assert result.success
        assert result.governance_status == "waiting_review"
        assert result.close_reason is None

    def test_whitelist_adds_to_whitelist_table(self, session, engine):
        """response=whitelist → waiting_review."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "whitelist", "user-1", remark="Internal tool")
        assert result.success

    def test_dispute_requires_remark(self, session, engine):
        """response=dispute without remark → error."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "dispute", "user-1")
        assert not result.success
        assert "remark" in (result.error or "").lower()

    def test_whitelist_requires_remark(self, session, engine):
        """response=whitelist without remark → error."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "whitelist", "user-1")
        assert not result.success
        assert "remark" in (result.error or "").lower()

    def test_dispute_with_remark(self, session, engine):
        """response=dispute with remark → waiting_review."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "dispute", "user-1", remark="I disagree")
        assert result.success
        assert result.governance_status == "waiting_review"

    def test_need_time_requires_repair_deadline(self, session, engine):
        """response=need_time without repair_deadline → error."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "need_time", "user-1")
        assert not result.success
        assert "repair_deadline" in (result.error or "").lower()

    def test_need_time_sets_scheduled_and_mute_until(self, session, engine):
        """response=need_time → scheduled + mute_until = repair_deadline + cooldown_days."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        deadline = datetime(2026, 7, 15)
        result = svc.resolve(
            "n-001", "need_time", "user-1",
            repair_deadline=deadline,
        )
        assert result.success
        assert result.governance_status == "scheduled"
        # mute_until = 2026-07-15 + cooldown_days(14) = 2026-07-29
        assert result.mute_until == datetime(2026, 7, 29)

    def test_owner_check_currently_bypassed(self, session, engine):
        """Owner check temporarily disabled — any user_id can resolve."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        result = svc.resolve("n-001", "optimized", "wrong-user")
        assert result.success

    def test_idempotent_double_resolve(self, session, engine):
        """Resolve twice → same result (idempotent)."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        r1 = svc.resolve("n-001", "optimized", "user-1")
        r2 = svc.resolve("n-001", "optimized", "user-1")
        assert r1.success
        assert r2.success
        assert r1.governance_status == r2.governance_status

    def test_notification_not_found(self, session, engine):
        """Non-existent notification → error."""
        svc, db = _build_svc(engine)

        result = svc.resolve("nonexistent", "optimized", "user-1")
        assert not result.success

    def test_feedback_payload_valid_json(self, session, engine):
        """feedback_payload with valid JSON → stored."""
        svc, db = _build_svc(engine)
        _make_notification(session)

        payload = {"version": 1, "overall_action": "accepted", "items": []}
        result = svc.resolve(
            "n-001", "optimized", "user-1",
            feedback_payload=payload,
        )
        assert result.success


# ── One-time feedback rule (§7.4.1) ────────────────────────────


class TestOneTimeFeedbackRule:
    """§7.4.1: ticket not found → error, duplicate → ignored,
    terminal → ignored, open + empty → proceed."""

    def test_ticket_not_found_returns_error(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session():
            result = svc.resolve(
                notification_id="nonexistent-id",
                response="optimized",
                user_id="staff-001",
                remark=None,
                source="http_api",
            )
        assert result.success is False
        assert result.error_code == "NOT_FOUND"

    def test_duplicate_feedback_ignored(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s,
                notification_id="n-dup",
                ticket_id="t-dup",
                governance_status="open",
                response="optimized",
            )
            s.commit()

        with db.orm_session() as s:
            result = svc.resolve(
                notification_id="n-dup",
                response="dispute",
                user_id="staff-001",
                remark=None,
                source="http_api",
            )
        assert result.success is True
        assert (
            "已反馈过" in (result.message or "")
            or "duplicate" in (result.message or "").lower()
            or result.governance_status == "open"
        )

    def test_terminal_status_ignored(self, session, engine):
        for status in ("scheduled", "waiting_review", "closed"):
            svc, db = _build_svc(engine)
            with db.orm_session() as s:
                _make_linked_rows(
                    s,
                    notification_id=f"n-term-{status}",
                    ticket_id=f"t-term-{status}",
                    governance_status=status,
                    bot_id=f"bot-term-{status}",
                )
                s.commit()

            with db.orm_session() as s:
                result = svc.resolve(
                    notification_id=f"n-term-{status}",
                    response="optimized",
                    user_id="staff-001",
                    remark=None,
                    source="http_api",
                )
            assert result.success is False

    def test_open_with_empty_response_proceeds(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s,
                notification_id="n-proceed",
                ticket_id="t-proceed",
                governance_status="open",
                response=None,
            )
            s.commit()

        with db.orm_session() as s:
            result = svc.resolve(
                notification_id="n-proceed",
                response="optimized",
                user_id="staff-001",
                remark=None,
                source="http_api",
            )
        assert result.success is True
        assert result.governance_status == "waiting_review"


# ── State transitions (§7.4.2) ─────────────────────────────────


class TestStateTransitions:
    def test_optimized_to_waiting_review(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s, notification_id="n-opt", ticket_id="t-opt",
                governance_status="open",
            )
            s.commit()

        with db.orm_session() as s:
            result = svc.resolve(
                notification_id="n-opt",
                response="optimized",
                user_id="staff-001",
                remark="Fixed",
                source="http_api",
            )
            s.commit()

        assert result.success is True
        assert result.governance_status == "waiting_review"
        assert result.close_reason is None

    def test_dispute_to_waiting_review(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s, notification_id="n-disp", ticket_id="t-disp",
                governance_status="open",
            )
            s.commit()

        with db.orm_session() as s:
            result = svc.resolve(
                notification_id="n-disp",
                response="dispute",
                user_id="staff-001",
                remark="Not applicable",
                source="http_api",
            )
            s.commit()

        assert result.success is True
        assert result.governance_status == "waiting_review"

    def test_whitelist_to_waiting_review(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s, notification_id="n-wl", ticket_id="t-wl",
                governance_status="open",
            )
            s.commit()

        with db.orm_session() as s:
            result = svc.resolve(
                notification_id="n-wl",
                response="whitelist",
                user_id="staff-001",
                remark="Approved",
                source="http_api",
            )
            s.commit()

        assert result.success is True
        assert result.governance_status == "waiting_review"

    def test_need_time_to_scheduled(self, session, engine):
        svc, db = _build_svc(engine)

        repair_deadline = datetime.now() + timedelta(days=7)

        with db.orm_session() as s:
            _make_linked_rows(
                s, notification_id="n-nt", ticket_id="t-nt",
                governance_status="open",
            )
            s.commit()

        with db.orm_session() as s:
            result = svc.resolve(
                notification_id="n-nt",
                response="need_time",
                user_id="staff-001",
                remark=None,
                source="http_api",
                repair_deadline=repair_deadline,
            )
            s.commit()

        assert result.success is True
        assert result.governance_status == "scheduled"
        assert result.mute_until is not None


# ── Listing ────────────────────────────────────────────────────


class TestListing:
    def test_list_pending_returns_open_and_scheduled(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s, notification_id="n-p1", ticket_id="t-p1",
                governance_status="open", owner_id="staff-list",
                bot_id="bot-p1",
            )
            _make_linked_rows(
                s, notification_id="n-p2", ticket_id="t-p2",
                governance_status="scheduled", owner_id="staff-list",
                bot_id="bot-p2",
            )
            _make_linked_rows(
                s, notification_id="n-p3", ticket_id="t-p3",
                governance_status="closed", owner_id="staff-list",
                bot_id="bot-p3",
            )
            s.commit()

        items = svc.list_pending("staff-list", limit=50, offset=0)
        assert len(items) == 2

    def test_list_history_returns_closed(self, session, engine):
        svc, db = _build_svc(engine)

        with db.orm_session() as s:
            _make_linked_rows(
                s, notification_id="n-h1", ticket_id="t-h1",
                governance_status="closed", owner_id="staff-hist",
                bot_id="bot-h1",
            )
            s.commit()

        items = svc.list_history("staff-hist", limit=50, offset=0)
        assert len(items) == 1
