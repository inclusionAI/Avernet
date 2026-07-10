"""Coverage supplement — task_record_repo uncovered methods.

Targets (from coverage report on lyp_dev_0710):
  - find_by_ticket_id (filter clause)
  - find_latest_closed_by_worker (return path)
  - list_scheduled_due (filter)
  - list_auto_silence_eligible (filter)
  - list_remindable_tickets (filter)
  - list_tickets_by_owner_and_statuses (filter)
  - find_ticket_by_notification_id
  - insert_ticket

Note: get_completed_decisions, _normalize_dt_field, _parse_gmt_create
were removed in the lyp_dev_0710 refactor — no longer in scope.

Repo methods return domain GovernanceTicket objects (not dicts).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.economy.governance.repositories.orm import (
    GovernanceNotificationOrm,
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)

from .conftest import FakeDB


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def engine():
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
def db(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(Session)


@pytest.fixture()
def repo(db):
    return TaskRecordRepository(db=db)


def _make_ticket(**overrides) -> GovernanceTicketOrm:
    """Build a valid GovernanceTicketOrm with sensible defaults."""
    import uuid
    now = datetime.now()
    uid = uuid.uuid4().hex[:8]
    defaults = dict(
        worker_id=f"w:{uid}",
        owner_id=f"o:{uid}",
        bot_id=f"b:{uid}",
        bot_name="TestBot",
        dt_version="20260710",
        governance_decision="actionable",
        latest_decision="actionable",
        governance_status="open",
        active_worker=f"w:{uid}",
        ticket_id=f"tkt-{uid}",
        analysis_status="completed",
        last_sync_at=now,
    )
    defaults.update(overrides)
    return GovernanceTicketOrm(**defaults)


def _make_notify(**overrides) -> GovernanceNotificationOrm:
    """Build a valid GovernanceNotificationOrm with sensible defaults."""
    import uuid
    uid = uuid.uuid4().hex[:8]
    defaults = dict(
        notification_id=f"notif-{uid}",
        ticket_id=f"tkt-{uid}",
        owner_id="owner1",
        bot_id="bot1",
        bot_name="TestBot",
        worker_id="owner1:bot1",
        dt_version="20260710",
        governance_decision="actionable",
        governance_cycle_id="cycle-001",
        governance_status="open",
        notify_status="pending",
    )
    defaults.update(overrides)
    return GovernanceNotificationOrm(**defaults)


# ── Tests ─────────────────────────────────────────────────────────


class TestFindByTicketId:
    """find_by_ticket_id(ticket_id) → GovernanceTicket | None"""

    def test_found(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-find-001", active_worker="w:find1"))
            s.commit()

        result = repo.find_by_ticket_id("tkt-find-001")
        assert result is not None
        assert result.ticket_id == "tkt-find-001"

    def test_not_found(self, repo):
        assert repo.find_by_ticket_id("nonexistent") is None


class TestFindLatestClosedByWorker:
    """find_latest_closed_by_worker(worker_id) → GovernanceTicket | None"""

    def test_returns_most_recently_closed(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                ticket_id="tkt-c1", worker_id="w:cls1",
                active_worker=None,
                governance_status="closed", closed_at=now - timedelta(hours=1),
                close_reason="user_close",
            ))
            s.add(_make_ticket(
                ticket_id="tkt-c2", worker_id="w:cls1",
                active_worker=None,
                governance_status="closed", closed_at=now,
                close_reason="whitelist_close",
            ))
            s.commit()

        result = repo.find_latest_closed_by_worker("w:cls1")
        assert result is not None
        # Domain model: close_reason exposed via GovernanceTicket
        assert result.close_reason == "whitelist_close"

    def test_no_closed(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(worker_id="w:cls2", active_worker="w:cls2"))
            s.commit()

        assert repo.find_latest_closed_by_worker("w:cls2") is None


class TestListScheduledDue:
    """list_scheduled_due(now) — tickets with mute_until <= now."""

    def test_returns_due_tickets(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:sch1",
                governance_status="scheduled",
                mute_until=now - timedelta(hours=1),
            ))
            s.commit()

        result = repo.list_scheduled_due(now)
        assert len(result) == 1
        assert result[0].governance_status == "scheduled"

    def test_skips_not_yet_due(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:sch2",
                governance_status="scheduled",
                mute_until=now + timedelta(hours=1),
            ))
            s.commit()

        assert repo.list_scheduled_due(now) == []


class TestListAutoSilenceEligible:
    """list_auto_silence_eligible(min_consecutive_days)."""

    def test_returns_eligible(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:as1",
                governance_status="open",
                latest_decision="normal",
                consecutive_normal_days=10,
            ))
            s.commit()

        result = repo.list_auto_silence_eligible(min_consecutive_days=7)
        assert len(result) == 1

    def test_skips_insufficient_days(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:as2",
                governance_status="open",
                latest_decision="normal",
                consecutive_normal_days=3,
            ))
            s.commit()

        assert repo.list_auto_silence_eligible(min_consecutive_days=7) == []


class TestListRemindableTickets:
    """list_remindable_tickets(now) — open + actionable + remind_at <= now."""

    def test_returns_remindable(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:rem1",
                governance_status="open",
                latest_decision="actionable",
                remind_at=now - timedelta(hours=1),
            ))
            s.commit()

        result = repo.list_remindable_tickets(now)
        assert len(result) == 1

    def test_skips_without_remind_at(self, repo, engine):
        now = datetime.now()
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                active_worker="w:rem2",
                governance_status="open",
                latest_decision="actionable",
                remind_at=None,
            ))
            s.commit()

        assert repo.list_remindable_tickets(now) == []


class TestListTicketsByOwnerAndStatuses:
    """list_tickets_by_owner_and_statuses(owner_id, statuses, …)."""

    def test_filters_by_owner_and_status(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(
                owner_id="o1", governance_status="open", active_worker="w:o1a",
            ))
            s.add(_make_ticket(
                owner_id="o1", governance_status="closed", active_worker=None,
            ))
            s.add(_make_ticket(
                owner_id="o2", governance_status="open", active_worker="w:o2",
            ))
            s.commit()

        result = repo.list_tickets_by_owner_and_statuses("o1", ["open"])
        assert len(result) == 1
        assert result[0].owner_id == "o1"


class TestFindTicketByNotificationId:
    """find_ticket_by_notification_id(notification_id)."""

    def test_found_via_notify_log(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_ticket(ticket_id="tkt-abc", active_worker="w:nfy1"))
            s.commit()
        with Session() as s:
            s.add(_make_notify(
                notification_id="notif-001",
                ticket_id="tkt-abc",
            ))
            s.commit()

        result = repo.find_ticket_by_notification_id("notif-001")
        assert result is not None
        assert result.ticket_id == "tkt-abc"

    def test_not_found_notification(self, repo):
        assert repo.find_ticket_by_notification_id("nonexistent") is None

    def test_notification_without_ticket_id(self, repo, engine):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            s.add(_make_notify(
                notification_id="notif-002",
                ticket_id=None,
            ))
            s.commit()

        assert repo.find_ticket_by_notification_id("notif-002") is None


class TestInsertTicket:
    """insert_ticket(row) — self-managed session insert."""

    def test_inserts_and_flushes(self, repo, engine):
        row = _make_ticket(ticket_id="tkt-ins", active_worker="w:ins1")
        repo.insert_ticket(row)

        result = repo.find_by_ticket_id("tkt-ins")
        assert result is not None
        assert result.ticket_id == "tkt-ins"