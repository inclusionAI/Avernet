"""Coverage supplement — vertical-slice tests for the biggest uncovered blocks.

P2-sorted targets:
  - admin_service.deliver_pending (607-839, ~233 lines)
  - record_process._handle_active_ticket_refresh (434-548, ~115 lines)
  - record_process._handle_whitelist_hit with active ticket (374-403, ~30 lines)
  - scan_service schedule_due + auto_silence_converge (scattered ~30 lines)

Design principles (from coverage-supplement-principles.md):
  P1 — trunk first: only happy paths, skip defensive branches
  P3 — vertical slice: one test penetrates multiple layers
  P4 — reuse conftest fixtures
  P5 — skip dry_run placeholders, except blocks, input-validation returns
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.economy.governance.domain.enums import (
    AuditAction,
    CloseReason,
    GovernanceStatus,
)
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
from agentclaw.community.core.economy.governance.services.delivery_service import (
    GovernanceDeliveryService,
)
from agentclaw.community.core.economy.governance.services.record_process_service import (
    GovernanceRecordService,
)
from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.scan_service import (
    GovernanceBotService,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.core.economy.governance.services.notify_lifecycle_service import (
    NotifyLifecycleService,
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


# ── Shared engine fixture (covers all tables) ─────────────────────


@pytest.fixture()
def engine():
    """In-memory SQLite with FK pragmas + all governance tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    import agentclaw.community.core.economy.governance.repositories.orm  # noqa: F401
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


# ── Helpers ────────────────────────────────────────────────────────


def _db_from_engine(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(lambda: Session(bind=engine))


def _seed_pending_notify(
    session,
    *,
    notification_id: str | None = None,
    ticket_id: str | None = None,
    bot_id: str = "bot-1",
    owner_id: str = "user-1",
    notify_channel: str = "tc_card",
    governance_status: str = "open",
    notify_status: str = "pending",
) -> str:
    """Insert a pending GovernanceNotificationOrm row for delivery testing."""
    if notification_id is None:
        notification_id = f"n-{uuid.uuid4().hex[:8]}"
    if ticket_id is None:
        ticket_id = f"t-{uuid.uuid4().hex[:8]}"
    row = GovernanceNotificationOrm(
        notification_id=notification_id,
        ticket_id=ticket_id,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        worker_id=f"{owner_id}:{bot_id}:{notification_id}",
        dt_version="20260705",
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=governance_status,
        notify_status=notify_status,
        notify_type="first_send",
        notify_source="offline_batch",
        notify_channel=notify_channel,
        notification_md="**Token saving opportunity**",
        notification_structured=json.dumps({"dimensions": ["token_usage"]}),
        hit_dimensions="token_usage",
        hit_dimensions_count="3",
        governance_max_priority="high",
        expected_token_saving=1000.0,
        saving_ratio=0.5,
        send_attempt_count=1,
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=1,
    )
    session.add(row)
    session.commit()
    return notification_id


# ══════════════════════════════════════════════════════════════════
# 1. admin_service.deliver_pending — Phase 2-5 (lines 607-839)
# ══════════════════════════════════════════════════════════════════


class _FakeScanSvc:
    """Scan service stub for deliver_pending's scan_svc param."""

    async def scan_and_deliver(self, **kwargs):
        return {"status": "ok"}


def _build_admin_svc(engine):
    """Build GovernanceDeliveryService with in-memory DB."""
    db = _db_from_engine(engine)
    cache = FakeCache()
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    whitelist_repo = GovernanceWhitelistRepository(db=db)
    # Driver first — lifecycle_service has no whitelist dependency (the
    # accept_feedback whitelist-add is owned by feedback_service), so build
    # it directly, then whitelist_service (which calls back into the driver).
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
        task_repo=task_repo,
    )
    svc = GovernanceDeliveryService(
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        task_repo=task_repo,
        config=FakeGovernanceConfig(),
        notify_sender=FakeNotifySender(),
        render_svc=NotifyRenderService(),
        lifecycle_svc=lifecycle_svc,
    )
    svc._scan_svc = _FakeScanSvc()
    return svc, db


class TestDeliverPendingTcCard:
    """deliver_pending: tc_card happy path — full Phase 2-5 (233 lines)."""

    @pytest.mark.asyncio
    async def test_tc_card_send_and_db_update(self, session, engine):
        """pending → tc_card send → notify_status='sent' + audit written."""
        svc, db = _build_admin_svc(engine)
        nid = _seed_pending_notify(session, notify_channel="tc_card")

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=False,
            max_send=10,
            channel="auto",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["sent_count"] == 1
        assert result["total"] == 1
        assert not result["dry_run"]

        # DB: notify_status flipped to "sent"
        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).filter_by(notification_id=nid).one()
            assert row.notify_status == "sent"
            assert row.sent_at is not None
            assert row.external_message_id is not None

        # Audit: NOTIFICATION_SENT written
        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            assert any(a.action_taken == AuditAction.NOTIFICATION_SENT for a in audits)

    @pytest.mark.asyncio
    async def test_empty_pending_returns_zero(self, session, engine):
        """No pending rows → total=0, sent_count=0."""
        svc, _ = _build_admin_svc(engine)

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=False,
            max_send=10,
            channel="auto",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["total"] == 0
        assert result["sent_count"] == 0

    @pytest.mark.asyncio
    async def test_max_send_limits_batch(self, session, engine):
        """max_send=1 truncates pending list to 1 item."""
        svc, _ = _build_admin_svc(engine)
        _seed_pending_notify(session, notification_id="n-a", bot_id="bot-a")
        _seed_pending_notify(session, notification_id="n-b", bot_id="bot-b")

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=False,
            max_send=1,
            channel="auto",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["total"] == 1


class TestDeliverPendingDryRun:
    """deliver_pending: dry_run=True — no DB writes, preview returned."""

    @pytest.mark.asyncio
    async def test_dry_run_no_db_update(self, session, engine):
        """dry_run → results contain tc_card preview, notify_status stays pending."""
        svc, db = _build_admin_svc(engine)
        nid = _seed_pending_notify(session, notify_channel="tc_card")

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=True,
            max_send=10,
            channel="auto",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["dry_run"] is True
        assert result["sent_count"] == 0
        assert len(result["results"]) == 1
        assert result["results"][0].get("dry_run") is True
        assert "tc_card" in result["results"][0]

        # DB unchanged
        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).filter_by(notification_id=nid).one()
            assert row.notify_status == "pending"


class TestDeliverPendingMarkdown:
    """deliver_pending: markdown channel path (lines 739-756)."""

    @pytest.mark.asyncio
    async def test_markdown_channel_sends(self, session, engine):
        """channel=markdown → send_markdown called, notify_status='sent'."""
        svc, db = _build_admin_svc(engine)
        nid = _seed_pending_notify(session, notify_channel="markdown")

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=False,
            max_send=10,
            channel="markdown",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["sent_count"] == 1

        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).filter_by(notification_id=nid).one()
            assert row.notify_status == "sent"


class TestDeliverPendingDegradation:
    """deliver_pending: tc_card send fails → degrades to markdown (P5: 1 case)."""

    @pytest.mark.asyncio
    async def test_tc_card_fails_degrades_to_markdown(self, session, engine):
        """send_tc_card returns None → markdown fallback, channel_used='markdown'."""
        svc, db = _build_admin_svc(engine)
        nid = _seed_pending_notify(session, notify_channel="tc_card")

        # Replace sender with one that fails tc_card but succeeds markdown
        class _FailingTcCardSender(FakeNotifySender):
            def send(self, message, *, channel="markdown"):
                if channel == "tc_card":
                    return None  # tc_card fails
                return f"fake-msg-{message.recipient}"

        svc._notify_sender = _FailingTcCardSender()

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=False,
            max_send=10,
            channel="auto",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["sent_count"] == 1
        assert result["results"][0].get("channel") == "markdown"

        with db.orm_session() as s:
            row = s.query(GovernanceNotificationOrm).filter_by(notification_id=nid).one()
            assert row.notify_status == "sent"
            # Channel updated on DB row (degradation recorded)
            assert row.notify_channel == "markdown"


class TestDeliverPendingFailedSend:
    """deliver_pending: Phase 5 — failed_ids audit (lines 813-832)."""

    @pytest.mark.asyncio
    async def test_failed_send_writes_failed_audit(self, session, engine):
        """Both tc_card and markdown fail → failed audit written, sent_count=0."""

        class _AlwaysFailSender(FakeNotifySender):
            def send(self, message, *, channel="markdown"):
                return None

        svc, db = _build_admin_svc(engine)
        _seed_pending_notify(session, notification_id="n-fail", notify_channel="tc_card")
        svc._notify_sender = _AlwaysFailSender()

        result = svc.deliver_pending(
            scan_svc=_FakeScanSvc(),
            override_recipient="user-1",
            dry_run=False,
            max_send=10,
            channel="auto",
            skip_scan=True,
            scan_dry_run=False,
        )

        assert result["sent_count"] == 0

        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            assert any(a.action_taken == AuditAction.NOTIFICATION_SEND_FAILED for a in audits)


# ══════════════════════════════════════════════════════════════════
# 2. record_process._handle_active_ticket_refresh (lines 434-548)
# ══════════════════════════════════════════════════════════════════


def _build_record_svc(engine):
    """Build GovernanceRecordService with in-memory DB."""
    db = _db_from_engine(engine)
    task_repo = TaskRecordRepository(db=db)
    whitelist_repo = GovernanceWhitelistRepository(db=db)
    notify_repo = NotifyLogRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    svc = GovernanceRecordService(
        task_repo=task_repo,
        whitelist_repo=whitelist_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=FakeGovernanceConfig(),
        lifecycle_svc=lifecycle_svc,
        render_svc=NotifyRenderService(),
    )
    return svc, db


def _sample_record(
    owner_id: str = "staff-001",
    bot_id: str = "bot-001",
    governance_decision: str = "actionable",
    dt_version: str = "20260705",
) -> GovernanceRecord:
    """Build a minimal GovernanceRecord for process_record."""
    return GovernanceRecord(
        owner_id=owner_id,
        bot_id=bot_id,
        bot_name="TestBot",
        governance_decision=governance_decision,
        dt_version=dt_version,
        hit_dimensions="token_usage",
        hit_dimensions_count=3,
        governance_max_priority="high",
        expected_token_saving=1000,
        saving_ratio=0.5,
        task_summary="Token saving opportunity",
        notification_structured=None,
        analysis_status="completed",
    )


def _make_ticket(
    session,
    *,
    ticket_id: str,
    worker_key: str = "staff-001:bot-001",
    governance_status: str = "open",
    governance_decision: str = "actionable",
    latest_decision: str = "actionable",
    dt_version: str = "20260701",
    response: str | None = None,
    remind_at=None,
    env: str = "dev",
) -> GovernanceTicketOrm:
    """Create a test ticket row (live ORM — caller is inside session)."""
    owner_id, bot_id = worker_key.split(":", 1)
    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker_key,
        active_worker=worker_key if governance_status != "closed" else None,
        governance_status=governance_status,
        governance_decision=governance_decision,
        latest_decision=latest_decision,
        dt_version=dt_version,
        env=env,
        bot_id=bot_id,
        owner_id=owner_id,
        bot_name="TestBot",
        consecutive_normal_days=0,
        remind_count=0,
        last_sync_at=datetime.now(),
        response=response,
        remind_at=remind_at,
    )
    session.add(row)
    session.commit()
    return row


class TestActiveTicketRefresh:
    """_handle_active_ticket_refresh via process_record — fresh dt_version refreshes snapshot."""

    def test_fresh_dt_version_refreshes_snapshot(self, session, engine):
        """Incoming dt_version > existing → snapshot fields updated (lines 460-548)."""
        svc, db = _build_record_svc(engine)

        # Create an active ticket with older dt_version
        with db.orm_session() as s:
            _make_ticket(
                s,
                ticket_id="t-refresh-1",
                worker_key="staff-001:bot-001",
                governance_status="open",
                dt_version="20260701",  # old
                latest_decision="actionable",
            )
            s.commit()

        # Process a record with newer dt_version
        record = _sample_record(dt_version="20260705", governance_decision="actionable")
        result = svc.process_record(record, run_id="run-refresh-1")

        assert result.action == "still_actionable"
        assert result.ticket_id == "t-refresh-1"

        # Verify snapshot fields were updated
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-refresh-1",
            ).one()
            assert ticket.dt_version == "20260705"
            assert ticket.hit_dimensions == "token_usage"
            assert ticket.expected_token_saving == 1000.0
            assert ticket.last_seen_at is not None

    def test_stale_dt_version_skips_refresh(self, session, engine):
        """Incoming dt_version <= existing → skip refresh (lines 434-459)."""
        svc, db = _build_record_svc(engine)

        with db.orm_session() as s:
            _make_ticket(
                s,
                ticket_id="t-stale-1",
                worker_key="staff-002:bot-002",
                governance_status="open",
                dt_version="20260710",  # newer
                latest_decision="actionable",
            )
            s.commit()

        # Process a record with older dt_version
        record = _sample_record(
            owner_id="staff-002", bot_id="bot-002",
            dt_version="20260705", governance_decision="actionable",
        )
        result = svc.process_record(record, run_id="run-stale-1")

        assert result.action == "still_actionable"
        assert result.reason == "stale_dt_version_skipped"

        # dt_version should NOT change
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-stale-1",
            ).one()
            assert ticket.dt_version == "20260710"

    def test_normal_decision_increments_consecutive_days(self, session, engine):
        """governance_decision='normal' + fresh dt → latest_decision='normal',
        consecutive_normal_days incremented (lines 509-512)."""
        svc, db = _build_record_svc(engine)

        with db.orm_session() as s:
            _make_ticket(
                s,
                ticket_id="t-normal-1",
                worker_key="staff-003:bot-003",
                governance_status="open",
                dt_version="20260701",
                latest_decision="actionable",
                governance_decision="actionable",
            )
            s.commit()

        record = _sample_record(
            owner_id="staff-003", bot_id="bot-003",
            dt_version="20260705", governance_decision="normal",
        )
        result = svc.process_record(record, run_id="run-normal-1")

        assert result.action == "still_actionable"

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-normal-1",
            ).one()
            assert ticket.latest_decision == "normal"
            assert ticket.consecutive_normal_days == 1


class TestWhitelistHitWithActiveTicket:
    """_handle_whitelist_hit with active ticket — observe + cancel (lines 374-408)."""

    def test_whitelist_hit_closes_active_ticket(self, session, engine):
        """Whitelist hit + active ticket → ticket observed(OBSERVED), notify cancelled."""
        svc, db = _build_record_svc(engine)

        # Create active ticket
        with db.orm_session() as s:
            _make_ticket(
                s,
                ticket_id="t-whitelist-1",
                worker_key="staff-004:bot-004",
                governance_status="open",
            )
            s.commit()

        # Add to whitelist
        whitelist_repo = GovernanceWhitelistRepository(db=db)
        whitelist_repo.add(
            bot_id="bot-004", owner_id="staff-004",
            created_by="admin",
            whitelist_type="governance",
        )

        # Create pending notify for this ticket
        with db.orm_session() as s:
            notify_row = GovernanceNotificationOrm(
                notification_id="n-wl-1",
                ticket_id="t-whitelist-1",
                bot_id="bot-004",
                bot_name="TestBot",
                owner_id="staff-004",
                worker_id="staff-004:bot-004:n-wl-1",
                dt_version="20260705",
                governance_decision="actionable",
                governance_cycle_id="cycle-1",
                governance_status="open",
                notify_status="pending",
                latest_decision="actionable",
                consecutive_normal_days=0,
                remind_count=0,
                send_attempt_count=1,
            )
            s.add(notify_row)
            s.commit()

        # Process record for the whitelisted bot
        record = _sample_record(owner_id="staff-004", bot_id="bot-004")
        result = svc.process_record(record, run_id="run-wl-1")

        assert result.action == "scan_whitelisted"
        assert result.ticket_id == "t-whitelist-1"

        # Verify ticket observed (OBSERVED, not closed — 白名单观察态)
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-whitelist-1",
            ).one()
            assert ticket.governance_status == "closed"
            assert ticket.close_reason == "scan_whitelisted"

        # Verify notify cancelled
        with db.orm_session() as s:
            notify = s.query(GovernanceNotificationOrm).filter_by(
                notification_id="n-wl-1",
            ).one()
            assert notify.notify_status == "cancelled"

        # Verify audit
        with db.orm_session() as s:
            audits = s.query(AuditLogOrm).all()
            assert any(a.action_taken == AuditAction.SCAN_WHITELISTED for a in audits)


# ══════════════════════════════════════════════════════════════════
# 3. scan_service schedule_due + auto_silence_converge
# ══════════════════════════════════════════════════════════════════


def _build_scan_svc(engine, *, config=None):
    """Build GovernanceBotService with in-memory DB."""
    db = _db_from_engine(engine)
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    if config is None:
        config = FakeGovernanceConfig()
    svc = GovernanceBotService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=config,
        notify_sender=FakeNotifySender(),
        lifecycle_svc=lifecycle_svc,
        render_svc=NotifyRenderService(),
        notify_lifecycle_svc=NotifyLifecycleService(notify_repo=notify_repo),
    )
    return svc, db


class TestScheduleDue:
    """_process_schedule_due: scheduled + mute_until <= now → waiting_review."""

    @pytest.mark.asyncio
    async def test_scheduled_ticket_transitions(self, session, engine):
        """scheduled ticket with mute_until in the past → waiting_review."""
        svc, db = _build_scan_svc(engine)

        # Create a scheduled ticket with expired mute_until
        with db.orm_session() as s:
            ticket = GovernanceTicketOrm(
                ticket_id="t-sched-1",
                worker_id="staff-001:bot-001",
                active_worker="staff-001:bot-001",
                governance_status="scheduled",
                governance_decision="actionable",
                latest_decision="actionable",
                dt_version="20260705",
                bot_id="bot-001",
                owner_id="staff-001",
                bot_name="TestBot",
                mute_until=datetime.now() - timedelta(hours=1),
                remind_count=0,
                last_sync_at=datetime.now(),
            )
            s.add(ticket)
            s.commit()

        # Create pending notify for this ticket (should be cancelled)
        with db.orm_session() as s:
            notify = GovernanceNotificationOrm(
                notification_id="n-sched-1",
                ticket_id="t-sched-1",
                bot_id="bot-001",
                bot_name="TestBot",
                owner_id="staff-001",
                worker_id="staff-001:bot-001:n-sched-1",
                dt_version="20260705",
                governance_decision="actionable",
                governance_cycle_id="cycle-1",
                governance_status="scheduled",
                notify_status="pending",
                latest_decision="actionable",
                consecutive_normal_days=0,
                remind_count=0,
                send_attempt_count=1,
            )
            s.add(notify)
            s.commit()

        result = svc.process_cron_tick(run_id="run-sched-1")

        assert result.schedule_due_count >= 1

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-sched-1",
            ).one()
            assert ticket.governance_status == "waiting_review"


class TestAutoSilenceConverge:
    """_process_auto_silence_converge: consecutive_normal_days >= threshold → closed."""

    @pytest.mark.asyncio
    async def test_auto_silence_closes_recovered(self, session, engine):
        """Ticket with consecutive_normal_days >= 7 → auto-closed."""
        config = FakeGovernanceConfig(auto_silence_close_days=7)
        svc, db = _build_scan_svc(engine, config=config)

        with db.orm_session() as s:
            ticket = GovernanceTicketOrm(
                ticket_id="t-auto-1",
                worker_id="staff-002:bot-002",
                active_worker="staff-002:bot-002",
                governance_status="open",
                governance_decision="normal",
                latest_decision="normal",
                dt_version="20260705",
                bot_id="bot-002",
                owner_id="staff-002",
                bot_name="TestBot",
                consecutive_normal_days=7,
                remind_count=0,
                last_sync_at=datetime.now(),
            )
            s.add(ticket)
            s.commit()

        result = svc.process_cron_tick(run_id="run-auto-1")

        assert result.auto_silence_closed >= 1

        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-auto-1",
            ).one()
            assert ticket.governance_status == "closed"
            assert ticket.close_reason == "auto_silenced_normal"


# ══════════════════════════════════════════════════════════════════
# 4. scan_service._advance_reminder_chain (lines 726-738, 751)
# ══════════════════════════════════════════════════════════════════


def _build_scan_svc(engine, *, config=None):
    """Build GovernanceBotService with in-memory DB."""
    db = _db_from_engine(engine)
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
    )
    if config is None:
        config = FakeGovernanceConfig()
    svc = GovernanceBotService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=config,
        notify_sender=FakeNotifySender(),
        lifecycle_svc=lifecycle_svc,
        render_svc=NotifyRenderService(),
        notify_lifecycle_svc=NotifyLifecycleService(notify_repo=notify_repo),
    )
    return svc, db


def _scan_seed_ticket(
    session,
    *,
    ticket_id: str,
    governance_status: str = "open",
    latest_decision: str = "actionable",
    active_worker: str = "user-1:bot-1",
    worker_id: str = "user-1:bot-1",
    bot_id: str = "bot-1",
    owner_id: str = "user-1",
    remind_at=None,
    remind_count: int = 0,
    response=None,
    mute_until=None,
) -> GovernanceTicketOrm:
    """Insert a GovernanceTicketOrm ticket row."""
    now = datetime.now()
    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker_id,
        bot_id=bot_id,
        owner_id=owner_id,
        dt_version="20260705",
        governance_decision="actionable",
        bot_name="TestBot",
        governance_status=governance_status,
        latest_decision=latest_decision,
        active_worker=active_worker,
        remind_at=remind_at,
        mute_until=mute_until,
        response=response,
        remind_count=remind_count,
        analysis_status="success",
        last_sync_at=now,
    )
    session.add(row)
    session.commit()
    return row


def _scan_seed_pending_notify(
    session,
    *,
    notification_id: str,
    ticket_id: str,
    notify_channel: str = "markdown",
    notify_type: str = "first_send",
    bot_id: str = "bot-1",
    owner_id: str = "user-1",
    worker_id: str = "user-1:bot-1",
    remind_count: int = 0,
) -> GovernanceNotificationOrm:
    """Insert a pending GovernanceNotificationOrm linked to a ticket."""
    row = GovernanceNotificationOrm(
        notification_id=notification_id,
        ticket_id=ticket_id,
        worker_id=worker_id,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        dt_version="20260705",
        governance_decision="actionable",
        governance_cycle_id="cyc",
        governance_status="open",
        notify_status="pending",
        notify_type=notify_type,
        notify_source="offline_batch",
        notify_channel=notify_channel,
        notification_md="**test**",
        hit_dimensions="token_usage",
        hit_dimensions_count="3",
        governance_max_priority="high",
        expected_token_saving=1000.0,
        saving_ratio=0.5,
        send_attempt_count=0,
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=remind_count,
    )
    session.add(row)
    session.commit()
    return row


class TestAdvanceReminderChain:
    """_advance_reminder_chain: reminder type → increment + schedule next (726-751)."""

    @pytest.mark.asyncio
    async def test_first_send_schedules_first_reminder(self, session, engine):
        """first_send → remind_at = now + delays[0], remind_count unchanged."""
        svc, db = _build_scan_svc(engine)

        _scan_seed_ticket(
            session, ticket_id="t-chain-1", remind_count=0,
        )
        _scan_seed_pending_notify(
            session, notification_id="n-chain-1", ticket_id="t-chain-1",
            notify_type="first_send",
        )

        summary = svc.process_cron_tick(dry_run=False)

        assert summary.sent_count >= 1
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-chain-1",
            ).one()
            # first_send sets remind_at = now + delays[0]
            assert ticket.remind_at is not None
            # remind_count stays 0 until a reminder actually sends
            assert ticket.remind_count == 0

    @pytest.mark.asyncio
    async def test_reminder_send_increments_count(self, session, engine):
        """reminder type → remind_count incremented + next remind_at scheduled (726-751)."""
        svc, db = _build_scan_svc(engine)

        _scan_seed_ticket(
            session, ticket_id="t-chain-2", remind_count=1,
            remind_at=datetime.now() - timedelta(hours=1),
        )
        # Seed a reminder-type pending notify
        _scan_seed_pending_notify(
            session, notification_id="n-chain-2", ticket_id="t-chain-2",
            notify_type="reminder", remind_count=1,
        )

        # The cron tick will: Step 2 — send the pending reminder
        # (it won't create a *new* reminder because the existing one is still pending)
        summary = svc.process_cron_tick(dry_run=False)

        assert summary.sent_count >= 1
        with db.orm_session() as s:
            ticket = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-chain-2",
            ).one()
            # After reminder sends, remind_count increments
            assert ticket.remind_count >= 2
            # Next remind_at is scheduled
            assert ticket.remind_at is not None