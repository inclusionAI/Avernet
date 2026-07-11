"""End-to-end tests for GovernanceWhitelistService — SQLite-backed.

Exercises ``bulk_whitelist`` and ``delete_whitelist_entry`` through the
real service + real repos backed by in-memory SQLite.  No MagicMock —
all DB operations hit the real ORM layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.domain.enums import AuditAction
from agentclaw.community.core.economy.governance.repositories.orm import (
    WhitelistEntryOrm,
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
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.whitelist_service import (
    GovernanceWhitelistService,
)

from .conftest import FakeDB, FakeGovernanceConfig


# --- Helpers ---


def _db(engine):
    """Build a FakeDB backed by in-memory SQLite."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(lambda: Session(bind=engine))


def _build_svc(engine):
    """Build GovernanceWhitelistService with real repos + SQLite."""
    db = _db(engine)
    whitelist_repo = GovernanceWhitelistRepository(db=db)
    notify_repo = NotifyLogRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    config = FakeGovernanceConfig()
    # whitelist_service needs lifecycle_svc (Task 8: bulk_whitelist closes
    # task_record subjects). lifecycle_service no longer depends on a whitelist
    # service (the accept_feedback whitelist-add is owned by feedback_service),
    # so the construction cycle is gone — build the driver directly.
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=TaskRecordRepository(db=db),
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    return GovernanceWhitelistService(
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=config,
        lifecycle_svc=lifecycle_svc,
    ), db


def _make_notification(session, *, notification_id, bot_id, owner_id,
                       governance_status="open", response=None, **overrides):
    """Insert a GovernanceNotificationOrm row for testing."""
    row = GovernanceNotificationOrm(
        notification_id=notification_id,
        bot_id=bot_id,
        bot_name=overrides.pop("bot_name", "TestBot"),
        owner_id=owner_id,
        worker_id=overrides.pop("worker_id", f"{owner_id}:{bot_id}"),
        dt_version=overrides.pop("dt_version", "20260629"),
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=governance_status,
        notify_status=overrides.pop("notify_status", "pending"),
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=1,
        send_attempt_count=1,
        response=response,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def _make_whitelist(session, *, bot_id, owner_id, whitelist_type="governance",
                    source="manual", **overrides):
    """Insert a WhitelistEntryOrm row directly."""
    row = WhitelistEntryOrm(
        bot_id=bot_id,
        owner_id=owner_id,
        whitelist_type=whitelist_type,
        source=source,
        reason=overrides.pop("reason", ""),
        created_by=overrides.pop("created_by", "admin"),
        env=overrides.pop("env", "dev"),
    )
    session.add(row)
    session.commit()
    return row


def _make_ticket(session, *, ticket_id, bot_id, owner_id, governance_status="open"):
    """Insert a GovernanceTicketOrm row linked to a notification via ticket_id."""
    worker = f"{owner_id}:{bot_id}"
    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker,
        active_worker=worker if governance_status != "closed" else None,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        dt_version="20260629",
        governance_decision="actionable",
        governance_status=governance_status,
        latest_decision="actionable",
        last_sync_at=datetime.now(),
    )
    session.add(row)
    session.commit()
    return row


# ── bulk_whitelist ────────────────────────────────────────────────


class TestBulkWhitelist:
    """bulk_whitelist: add to whitelist + close related notifications."""

    def test_whitelists_and_cancels_pending(self, session, engine):
        """Bots with open notifications → whitelisted + cancelled."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )
        _make_notification(
            session, notification_id="n-2",
            bot_id="bot-b", owner_id="owner-b", governance_status="open",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-a", "bot-b"], reason="cleanup", operator="admin",
        )

        assert result["whitelisted"] == 2
        assert result["cancelled"] == 2

        # Verify notifications are closed
        with db.orm_session() as s:
            notif_rows = s.query(GovernanceNotificationOrm).all()
            for n in notif_rows:
                assert n.notify_status == "cancelled"
                assert n.governance_status == "closed"
                assert n.close_reason == "emergency_closed"
                assert n.cooldown_until is not None

        # Verify whitelist entries exist
        with db.orm_session() as s:
            wl = s.query(WhitelistEntryOrm).all()
            assert len(wl) == 2
            bot_ids = {r.bot_id for r in wl}
            assert bot_ids == {"bot-a", "bot-b"}

    def test_audit_written(self, session, engine):
        """bulk_whitelist writes a governance audit entry."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-x", owner_id="owner-x", governance_status="open",
        )

        svc.bulk_whitelist(
            bot_ids=["bot-x"], reason="test", operator="admin-1",
        )

        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            wl_audits = [a for a in audits if a.action_taken == AuditAction.ADMIN_WHITELIST]
            assert len(wl_audits) >= 1
            assert wl_audits[0].actor_id == "admin-1"

    def test_no_matching_bots(self, session, engine):
        """No notifications for requested bots → whitelisted=0, cancelled=0."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-known", owner_id="owner-1", governance_status="open",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-unknown"], reason="x", operator="admin",
        )

        assert result["whitelisted"] == 0
        assert result["cancelled"] == 0

    def test_skips_closed_notifications(self, session, engine):
        """Already-closed notifications are not affected."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-open",
            bot_id="bot-x", owner_id="owner-x", governance_status="open",
        )
        _make_notification(
            session, notification_id="n-closed",
            bot_id="bot-x", owner_id="owner-x", governance_status="closed",
            notify_status="sent",
        )

        result = svc.bulk_whitelist(
            bot_ids=["bot-x"], reason="x", operator="admin",
        )

        # Only the open notification is cancelled
        assert result["cancelled"] == 1

        with db.orm_session() as s:
            rows = {r.notification_id: r for r in s.query(GovernanceNotificationOrm).all()}
            assert rows["n-open"].governance_status == "closed"
            assert rows["n-closed"].governance_status == "closed"  # was already closed

    def test_cooldown_applied(self, session, engine):
        """Each cancelled notification gets cooldown_until = now + cooldown_days."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )

        before = datetime.now()
        result = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        after = datetime.now()

        assert result["cancelled"] == 1
        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            expected_min = before + timedelta(days=14)
            expected_max = after + timedelta(days=14)
            assert expected_min <= row.cooldown_until <= expected_max

    def test_idempotent_whitelist(self, session, engine):
        """Re-whitelisting same bots → skipped in whitelist, still cancels open."""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-1",
            bot_id="bot-a", owner_id="owner-a", governance_status="open",
        )

        # First call: inserted
        result1 = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        assert result1["whitelisted"] == 1

        # Re-open notification (simulating a new cycle)
        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).one()
            row.governance_status = "open"
            row.notify_status = "pending"
            row.close_reason = None

        # Second call: skipped in whitelist, still cancels
        result2 = svc.bulk_whitelist(
            bot_ids=["bot-a"], reason="test", operator="admin",
        )
        assert result2["whitelisted"] == 0  # skip — already in whitelist
        assert result2["cancelled"] == 1     # still cancels the open notification


# ── Task 8 口径对齐 ──────────────────────────────────────────────


class TestBulkWhitelistTicketAlignment:
    """Task 8: bulk_whitelist 取消通知投递的同时,按 ticket_id 集合关 task_record
    主体(逐条 domain guard、幂等)——修正"只关通知、工单留 open"脱钩。
    bot_id IN (...) 且 response IS NULL 口径精确:已反馈的通知/工单不动。"""

    def test_closes_task_record_subjects_for_unresponded(self, session, engine):
        """unresponded open 通知对应工单 → CLOSED(emergency_closed)。"""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-a", bot_id="bot-a", owner_id="owner-a",
            governance_status="open", ticket_id="t-a",
        )
        _make_ticket(session, ticket_id="t-a", bot_id="bot-a", owner_id="owner-a")

        result = svc.bulk_whitelist(["bot-a"], reason="cleanup", operator="admin")
        assert result["cancelled"] == 1

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).one()
            assert ticket.governance_status == "closed"
            assert ticket.close_reason == "emergency_closed"

    def test_preserves_responded_ticket_subject(self, session, engine):
        """已反馈通知(bot 命中但 response 非 None)不在 cancel scope,
        工单不动——按口径精确,不裸用全量关闭。"""
        svc, db = _build_svc(engine)
        _make_notification(
            session, notification_id="n-responded", bot_id="bot-a", owner_id="owner-a",
            governance_status="muted", response="need_time", ticket_id="t-responded",
        )
        _make_ticket(
            session, ticket_id="t-responded", bot_id="bot-a", owner_id="owner-a",
            governance_status="scheduled",
        )

        result = svc.bulk_whitelist(["bot-a"], reason="cleanup", operator="admin")
        assert result["cancelled"] == 0  # 已反馈,不取消

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).one()
            assert ticket.governance_status == "scheduled"  # 精确口径:不动


# ── delete_whitelist_entry ──────────────────────────────────────


class TestDeleteWhitelistEntry:
    """delete_whitelist_entry: single remove by (bot_id, owner_id)."""

    def test_delete_existing(self, session, engine):
        """Delete an existing whitelist entry."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")
        _make_whitelist(session, bot_id="bot-b", owner_id="user-1")

        result = svc.delete_whitelist_entry(
            bot_id="bot-a", owner_id="user-1",
            reason="cleanup", operator="admin-1",
        )

        assert result["deleted"] is True
        assert result["bot_id"] == "bot-a"
        assert result["owner_id"] == "user-1"

        with db.orm_session() as s:
            remaining = s.query(WhitelistEntryOrm).all()
            assert len(remaining) == 1
            assert remaining[0].bot_id == "bot-b"

    def test_delete_nonexistent(self, session, engine):
        """Deleting a non-existent entry → deleted=False."""
        svc, db = _build_svc(engine)

        result = svc.delete_whitelist_entry(
            bot_id="bot-x", owner_id="user-x",
            reason="test", operator="admin",
        )

        assert result["deleted"] is False

    def test_audit_written_on_delete(self, session, engine):
        """Real delete writes WHITELIST_REMOVED audit."""
        svc, db = _build_svc(engine)
        _make_whitelist(session, bot_id="bot-a", owner_id="user-1")

        svc.delete_whitelist_entry(
            bot_id="bot-a", owner_id="user-1",
            reason="cleanup", operator="admin-1",
        )

        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            wl_audits = [a for a in audits if a.action_taken == AuditAction.WHITELIST_REMOVED]
            assert len(wl_audits) >= 1
            assert wl_audits[0].actor_id == "admin-1"