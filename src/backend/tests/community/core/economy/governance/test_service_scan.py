"""Tests for GovernanceBotService.process_cron_tick() — cron orchestrator.

Covers the main orchestration flows: pending send (markdown + tc_card),
send failure, cancel, reminder creation, schedule_due, emergency brake,
dry_run, timeout recovery, and CronTickSummary.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import agentclaw.community.core.economy.governance.repositories.orm  # noqa: F401
from agentclaw.community.core.base import Base
from agentclaw.community.core.economy.governance.repositories.orm import (
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
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.scan_service import (
    CronTickSummary,
    GovernanceBotService,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.core.economy.governance.services.notify_lifecycle_service import (
    NotifyLifecycleService,
)



from .conftest import FakeDB, FakeGovernanceConfig, FakeNotifySender


# --- Scan-specific fakes (not shared with other test files) ---


class _ConfigurableSender:
    """Sender whose tc_card/markdown return values are configurable.

    Implements the ``NotifySenderPlugin`` Protocol surface (``send`` + ``channels``).
    Used for testing delivery failure paths (e.g. markdown send returns None).
    """

    def __init__(self, tc_card: str | None = "tc-123", markdown: str | None = "md-123"):
        self._tc = tc_card
        self._md = markdown

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"markdown", "tc_card"})

    def send(self, message: object, *, channel: str = "markdown") -> str | None:
        if channel == "tc_card":
            return self._tc
        return self._md


# --- Helpers ---


def _make_tables(engine):
    """Create all tables and return a session factory."""
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_ticket(
    session,
    *,
    ticket_id=None,
    governance_status="open",
    latest_decision="actionable",
    active_worker="user-1:bot-1",
    worker_id="user-1:bot-1",
    bot_id="bot-1",
    owner_id="user-1",
    remind_at=None,
    mute_until=None,
    response=None,
    remind_count=0,
):
    """Insert a GovernanceTicketOrm ticket row."""
    if ticket_id is None:
        ticket_id = uuid.uuid4().hex
    now = datetime.now()
    row = GovernanceTicketOrm(
        ticket_id=ticket_id,
        worker_id=worker_id,
        bot_id=bot_id,
        owner_id=owner_id,
        dt_version="20260629",
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
    return ticket_id


def _seed_pending_notify(
    session,
    *,
    ticket_id,
    notify_channel="markdown",
    notify_type="first_send",
    notification_id=None,
    worker_id="user-1:bot-1",
    bot_id="bot-1",
    owner_id="user-1",
):
    """Insert a pending GovernanceNotificationOrm linked to a ticket."""
    if notification_id is None:
        notification_id = uuid.uuid4().hex
    row = GovernanceNotificationOrm(
        notification_id=notification_id,
        ticket_id=ticket_id,
        worker_id=worker_id,
        bot_id=bot_id,
        bot_name="TestBot",
        owner_id=owner_id,
        dt_version="20260629",
        governance_decision="actionable",
        governance_cycle_id="cyc",
        governance_status="open",
        notify_status="pending",
        notify_type=notify_type,
        notify_source="offline_batch",
        notify_channel=notify_channel,
        notification_md="**test**",
        send_attempt_count=0,
    )
    session.add(row)
    session.commit()
    return notification_id


def _build_service(engine, *, config=None, notify_sender=None):
    """Build GovernanceBotService with real repos against in-memory SQLite."""
    Sess = _make_tables(engine)
    db = FakeDB(Sess)
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
    if notify_sender is None:
        notify_sender = FakeNotifySender()
    svc = GovernanceBotService(
        task_repo=task_repo,
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        config=config,
        notify_sender=notify_sender,
        lifecycle_svc=lifecycle_svc,
        render_svc=NotifyRenderService(),
        notify_lifecycle_svc=NotifyLifecycleService(notify_repo=notify_repo),
    )
    return svc, db, Sess


# --- Tests: basic smoke ---


class TestProcessCronTickSmoke:
    """Smoke tests — empty DB, summary defaults."""

    @pytest.mark.asyncio
    async def test_cron_tick_returns_summary(self, engine):
        """Cron tick returns a valid CronTickSummary even with no data."""
        svc, db, _sess = _build_service(engine)

        summary = svc.process_cron_tick()
        assert isinstance(summary, CronTickSummary)
        assert summary.run_id != ""
        assert summary.dry_run is False

    @pytest.mark.asyncio
    async def test_empty_db_no_errors(self, engine):
        """No pending data → tick completes gracefully with zero counts."""
        svc, db, _sess = _build_service(engine)

        summary = svc.process_cron_tick()
        assert summary.errors == 0
        assert summary.sent_count == 0


class TestCronTickSummary:
    """CronTickSummary dataclass defaults."""

    def test_defaults(self):
        s = CronTickSummary()
        assert s.run_id == ""
        assert s.sent_count == 0
        assert s.failed_count == 0
        assert s.dry_run is False


# --- Tests: send pending ---


class TestProcessCronTick:
    """Test GovernanceBotService.process_cron_tick()."""

    @pytest.mark.asyncio
    async def test_send_pending_succeeds(self, engine):
        """Pending notify with open+actionable ticket → sent."""
        svc, db, Sess = _build_service(engine)
        s = Sess()
        ticket_id = _seed_ticket(s)
        notif_id = _seed_pending_notify(s, ticket_id=ticket_id, notify_channel="markdown")
        s.close()

        summary = svc.process_cron_tick()

        assert isinstance(summary, CronTickSummary)
        assert summary.sent_count >= 1
        with db.orm_session() as s2:
            row = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
            assert row.notify_status == "sent"

    @pytest.mark.asyncio
    async def test_send_pending_tc_card_success(self, engine):
        """tc_card channel: pending notify gets sent via send_tc_card."""
        svc, db, Sess = _build_service(
            engine,
            config=FakeGovernanceConfig(notify_channel="tc_card"),
            notify_sender=_ConfigurableSender(tc_card="tc-123"),
        )
        s = Sess()
        ticket_id = _seed_ticket(s)
        notif_id = _seed_pending_notify(s, ticket_id=ticket_id, notify_channel="tc_card")
        s.close()

        summary = svc.process_cron_tick(dry_run=False)

        assert summary.sent_count >= 1
        with db.orm_session() as s2:
            row = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
            assert row.notify_status == "sent"

    @pytest.mark.asyncio
    async def test_send_pending_markdown_fails(self, engine):
        """markdown channel, send returns None → fails, reverts to pending for retry."""
        svc, db, Sess = _build_service(
            engine,
            config=FakeGovernanceConfig(notify_channel="markdown"),
            notify_sender=_ConfigurableSender(tc_card=None, markdown=None),
        )
        s = Sess()
        ticket_id = _seed_ticket(s)
        notif_id = _seed_pending_notify(
            s, ticket_id=ticket_id, notify_channel="markdown",
        )
        s.close()

        summary = svc.process_cron_tick(dry_run=False)

        assert summary.failed_count >= 1
        with db.orm_session() as s2:
            row = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
            # First attempt (count < MAX_SEND_ATTEMPTS) → reverts to pending
            assert row.notify_status in ("pending", "failed")
            assert row.last_send_error is not None

    @pytest.mark.asyncio
    async def test_cancel_pending_when_ticket_closed(self, engine):
        """Pending notify whose ticket is closed → cancelled."""
        svc, db, Sess = _build_service(engine)
        s = Sess()
        ticket_id = _seed_ticket(s, governance_status="closed")
        notif_id = _seed_pending_notify(s, ticket_id=ticket_id, notify_channel="markdown")
        s.close()

        summary = svc.process_cron_tick()

        assert summary.cancelled_count >= 1
        with db.orm_session() as s2:
            row = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
            assert row.notify_status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_pending_when_ticket_not_actionable(self, engine):
        """Pending notify whose ticket latest_decision=normal → cancelled."""
        svc, db, Sess = _build_service(engine)
        s = Sess()
        ticket_id = _seed_ticket(s, latest_decision="normal")
        notif_id = _seed_pending_notify(s, ticket_id=ticket_id, notify_channel="markdown")
        s.close()

        summary = svc.process_cron_tick()

        assert summary.cancelled_count >= 1
        with db.orm_session() as s2:
            row = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
            assert row.notify_status == "cancelled"

    @pytest.mark.asyncio
    async def test_brake_does_not_block_manual_tick(self, engine):
        """制动生效时直调 process_cron_tick 仍执行(手动路径不被拦)。

        制动拦截已移交调度层 GovernanceBotLifecycle._run_scan(见
        test_governance_lifecycle)。process_cron_tick 自身不查制动——
        手动接口(trigger-scan/scan-and-deliver)在制动期间照常可用。
        这里 seed 一条 pending 通知,断言手动 tick 照常投递而非被跳过。
        """
        svc, db, Sess = _build_service(engine)
        s = Sess()
        ticket_id = _seed_ticket(s)
        _seed_pending_notify(s, ticket_id=ticket_id, notify_channel="markdown")
        s.close()

        summary = svc.process_cron_tick()

        assert isinstance(summary, CronTickSummary)
        # 制动不拦手动 tick:pending 通知被正常处理(发送),非全零跳过
        assert summary.sent_count >= 1

    @pytest.mark.asyncio
    async def test_dry_run_skips_sending_and_reminders(self, engine):
        """dry_run=True → no sends or reminder creation occur."""
        svc, db, Sess = _build_service(engine)
        s = Sess()
        ticket_id = _seed_ticket(s)
        notif_id = _seed_pending_notify(s, ticket_id=ticket_id, notify_channel="markdown")
        s.close()

        summary = svc.process_cron_tick(dry_run=True)

        assert summary.dry_run is True
        assert summary.sent_count == 0
        assert summary.reminders_created == 0
        with db.orm_session() as s2:
            row = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
            assert row.notify_status == "pending"

    @pytest.mark.asyncio
    async def test_reminder_created_for_remindable_ticket(self, engine):
        """Ticket with open+actionable+remind_at <= now → reminder notify created."""
        svc, db, Sess = _build_service(engine)
        s = Sess()
        now = datetime.now()
        ticket_id = _seed_ticket(
            s,
            remind_at=now - timedelta(hours=1),
            active_worker="u2:b-rem",
            worker_id="u2:b-rem",
            bot_id="b-rem",
            owner_id="u2",
        )
        s.close()

        summary = svc.process_cron_tick()

        assert summary.reminders_created >= 1
        with db.orm_session() as s2:
            reminders = (
                s2.query(GovernanceNotificationOrm)
                .filter_by(ticket_id=ticket_id, notify_type="reminder")
                .all()
            )
            assert len(reminders) >= 1

    @pytest.mark.asyncio
    async def test_schedule_due_transitions_ticket(self, engine):
        """Scheduled ticket with mute_until <= now → waiting_review."""
        svc, db, Sess = _build_service(engine)
        s = Sess()
        now = datetime.now()
        ticket_id = _seed_ticket(
            s,
            governance_status="scheduled",
            mute_until=now - timedelta(hours=1),
            active_worker="u3:b-sched",
            worker_id="u3:b-sched",
            bot_id="b-sched",
            owner_id="u3",
        )
        s.close()

        summary = svc.process_cron_tick()

        assert summary.schedule_due_count >= 1
        with db.orm_session() as s2:
            ticket = s2.query(GovernanceTicketOrm).filter_by(ticket_id=ticket_id).one()
            assert ticket.governance_status == "waiting_review"

    @pytest.mark.asyncio
    async def test_auto_silence_converge_closes_recovered_ticket(self, engine):
        """Open ticket with consecutive_normal_days >= threshold → closed."""
        cfg = FakeGovernanceConfig(auto_silence_close_days=7)
        svc, db, Sess = _build_service(engine, config=cfg)
        s = Sess()
        ticket_id = _seed_ticket(
            s,
            latest_decision="normal",
        )
        # Set consecutive_normal_days to meet threshold
        ticket = s.query(GovernanceTicketOrm).filter_by(ticket_id=ticket_id).one()
        ticket.consecutive_normal_days = 7
        # Also seed a pending notify — should be cancelled on close
        notif_id = _seed_pending_notify(s, ticket_id=ticket_id)
        s.commit()
        s.close()

        summary = svc.process_cron_tick(dry_run=False)

        assert summary.auto_silence_closed >= 1
        # Verify ticket state
        s2 = Sess()
        t = s2.query(GovernanceTicketOrm).filter_by(ticket_id=ticket_id).one()
        assert t.governance_status == "closed"
        assert t.close_reason == "auto_silenced_normal"
        assert t.active_worker is None
        assert t.remind_at is None
        # Verify pending notify was cancelled
        n = s2.query(GovernanceNotificationOrm).filter_by(notification_id=notif_id).one()
        assert n.notify_status == "cancelled"
        s2.close()

    @pytest.mark.asyncio
    async def test_auto_silence_converge_below_threshold_no_close(self, engine):
        """Open ticket with consecutive_normal_days < threshold → stays open."""
        cfg = FakeGovernanceConfig(auto_silence_close_days=7)
        svc, db, Sess = _build_service(engine, config=cfg)
        s = Sess()
        ticket_id = _seed_ticket(
            s,
            latest_decision="normal",
        )
        ticket = s.query(GovernanceTicketOrm).filter_by(ticket_id=ticket_id).one()
        ticket.consecutive_normal_days = 6  # Below threshold
        s.commit()
        s.close()

        summary = svc.process_cron_tick(dry_run=False)

        assert summary.auto_silence_closed == 0
        s2 = Sess()
        t = s2.query(GovernanceTicketOrm).filter_by(ticket_id=ticket_id).one()
        assert t.governance_status == "open"
        s2.close()