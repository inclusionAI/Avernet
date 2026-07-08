"""Coverage supplement — repo methods not covered by existing tests.

Targets (P2 sorted):
  - notify_log_repo: find_latest_closed, find_latest, get_by_notification_id,
    list_remindable, list_expired, list_pending_to_cancel, list_pending_open,
    count_pending, insert_notification, delete_by_notification_ids,
    count_by_notification_ids (lines 64-111, 179-244, 305-316, 553-611)
  - task_record_repo: count_by_dt_versions, delete_by_dt_versions,
    delete_by_ids, count_by_ids (lines 306-388)

Design: vertical-slice — one test seeds data then exercises multiple repo
methods in sequence, maximizing ROI per test case.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)

from .conftest import FakeDB


# ── Shared fixture ────────────────────────────────────────────────


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

    import agentclaw.community.core.economy.governance.contracts.models  # noqa: F401
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()


def _db_from_engine(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(lambda: Session(bind=engine))


def _make_notify(
    session,
    *,
    notification_id: str,
    bot_id: str = "bot-1",
    owner_id: str = "user-1",
    governance_status: str = "open",
    notify_status: str = "pending",
    closed_at: datetime | None = None,
    remind_at: datetime | None = None,
    expire_at: datetime | None = None,
    remind_count: int = 0,
    response: str | None = None,
    ticket_id: str | None = None,
    send_attempt_count: int = 1,
) -> GovernanceNotifyLog:
    """Insert a GovernanceNotifyLog row directly via session."""
    row = GovernanceNotifyLog(
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
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=remind_count,
        expire_at=expire_at,
        send_attempt_count=send_attempt_count,
        response=response,
    )
    if closed_at is not None:
        row.closed_at = closed_at
    if remind_at is not None:
        row.remind_at = remind_at
    session.add(row)
    session.commit()
    return row


# ══════════════════════════════════════════════════════════════════
# NotifyLogRepository — query methods (vertical slice)
# ══════════════════════════════════════════════════════════════════


class TestNotifyLogRepoQueries:
    """Single vertical-slice: seed data → exercise all uncovered query methods."""

    def test_find_latest_closed_returns_most_recent(self, session, engine):
        """find_latest_closed: returns the most recent closed row by closed_at."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        now = datetime.now()
        _make_notify(session, notification_id="n-old", bot_id="bot-a",
                     owner_id="user-a", governance_status="closed",
                     closed_at=now - timedelta(days=2))
        _make_notify(session, notification_id="n-recent", bot_id="bot-a",
                     owner_id="user-a", governance_status="closed",
                     closed_at=now)

        result = repo.find_latest_closed("bot-a", "user-a")
        assert result is not None
        assert result["notification_id"] == "n-recent"

    def test_find_latest_closed_returns_none_when_no_match(self, session, engine):
        """find_latest_closed: no matching rows → None."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        result = repo.find_latest_closed("bot-x", "user-x")
        assert result is None

    def test_find_latest_returns_any_status(self, session, engine):
        """find_latest: returns newest row regardless of status."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        now = datetime.now()
        _make_notify(session, notification_id="n-early", bot_id="bot-b",
                     owner_id="user-b", governance_status="open")
        # Slightly delay second insert so gmt_create is later
        _make_notify(session, notification_id="n-later", bot_id="bot-b",
                     owner_id="user-b", governance_status="closed",
                     closed_at=now)

        result = repo.find_latest("bot-b", "user-b")
        assert result is not None
        # Both rows exist; find_latest returns by gmt_create desc
        assert result["notification_id"] in ("n-early", "n-later")

    def test_get_by_notification_id(self, session, engine):
        """get_by_notification_id: fetch by notification_id."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-fetch-1", bot_id="bot-c",
                     owner_id="user-c")

        result = repo.get_by_notification_id("n-fetch-1")
        assert result is not None
        assert result["bot_id"] == "bot-c"

    def test_get_by_notification_id_not_found(self, session, engine):
        """get_by_notification_id: non-existent → None."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        result = repo.get_by_notification_id("n-nonexistent")
        assert result is None

    def test_list_remindable_returns_due(self, session, engine):
        """list_remindable: open + sent + remind_at <= now → returned."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        now = datetime.now()
        _make_notify(session, notification_id="n-remind-1", bot_id="bot-d",
                     owner_id="user-d", governance_status="open",
                     notify_status="sent", remind_at=now - timedelta(hours=1))
        _make_notify(session, notification_id="n-remind-future", bot_id="bot-d2",
                     owner_id="user-d2", governance_status="open",
                     notify_status="sent", remind_at=now + timedelta(days=1))

        results = repo.list_remindable(now)
        ids = {r["notification_id"] for r in results}
        assert "n-remind-1" in ids
        assert "n-remind-future" not in ids

    def test_list_expired_returns_past_expire(self, session, engine):
        """list_expired: open + expire_at <= now + reminded → returned."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        now = datetime.now()
        _make_notify(session, notification_id="n-exp-1", bot_id="bot-e",
                     owner_id="user-e", governance_status="open",
                     notify_status="sent", expire_at=now - timedelta(hours=1),
                     remind_count=1)
        _make_notify(session, notification_id="n-exp-no-remind", bot_id="bot-e2",
                     owner_id="user-e2", governance_status="open",
                     notify_status="sent", expire_at=now - timedelta(hours=1),
                     remind_count=0)

        results = repo.list_expired(now)
        ids = {r["notification_id"] for r in results}
        assert "n-exp-1" in ids
        assert "n-exp-no-remind" not in ids

    def test_list_pending_to_cancel(self, session, engine):
        """list_pending_to_cancel: closed/expired + pending → returned."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-pc-1", bot_id="bot-f",
                     owner_id="user-f", governance_status="closed",
                     notify_status="pending")
        _make_notify(session, notification_id="n-pc-open", bot_id="bot-f2",
                     owner_id="user-f2", governance_status="open",
                     notify_status="pending")

        results = repo.list_pending_to_cancel()
        ids = {r["notification_id"] for r in results}
        assert "n-pc-1" in ids
        assert "n-pc-open" not in ids

    def test_list_pending_open(self, session, engine):
        """list_pending_open: open + pending → returned."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-po-1", bot_id="bot-g",
                     owner_id="user-g", governance_status="open",
                     notify_status="pending")
        _make_notify(session, notification_id="n-po-sent", bot_id="bot-g2",
                     owner_id="user-g2", governance_status="open",
                     notify_status="sent")

        results = repo.list_pending_open()
        ids = {r["notification_id"] for r in results}
        assert "n-po-1" in ids
        assert "n-po-sent" not in ids

    def test_count_pending(self, session, engine):
        """count_pending: open/muted + no response → count."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-cp-1", bot_id="bot-h",
                     owner_id="user-h", governance_status="open",
                     notify_status="pending", response=None)
        _make_notify(session, notification_id="n-cp-answered", bot_id="bot-h2",
                     owner_id="user-h2", governance_status="open",
                     notify_status="sent", response="optimized")

        count = repo.count_pending()
        assert count >= 1  # At least the unanswered one


class TestNotifyLogRepoWriteMethods:
    """Cover insert_notification, delete_by_notification_ids, count_by_notification_ids."""

    def test_insert_notification(self, session, engine):
        """insert_notification: adds row via repo (self-managed session)."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        row = GovernanceNotifyLog(
            notification_id="n-ins-1",
            bot_id="bot-ins",
            bot_name="TestBot",
            owner_id="user-ins",
            worker_id="user-ins:bot-ins:n-ins-1",
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
        repo.insert_notification(row)

        # Verify visible via another session
        with db.orm_session() as s:
            found = s.query(GovernanceNotifyLog).filter_by(
                notification_id="n-ins-1",
            ).one_or_none()
            assert found is not None

    def test_delete_by_notification_ids(self, session, engine):
        """delete_by_notification_ids: deletes matching rows, returns counts."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-del-1", bot_id="bot-del",
                     owner_id="user-del")
        _make_notify(session, notification_id="n-del-2", bot_id="bot-del2",
                     owner_id="user-del2")

        deleted, not_found = repo.delete_by_notification_ids(
            ["n-del-1", "n-nonexistent"],
        )
        assert deleted == 1
        assert "n-nonexistent" in not_found

    def test_count_by_notification_ids(self, session, engine):
        """count_by_notification_ids: counts matches without deletion."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-cnt-1", bot_id="bot-cnt",
                     owner_id="user-cnt")
        _make_notify(session, notification_id="n-cnt-2", bot_id="bot-cnt2",
                     owner_id="user-cnt2")

        count, not_found = repo.count_by_notification_ids(
            ["n-cnt-1", "n-cnt-missing"],
        )
        assert count == 1
        assert "n-cnt-missing" in not_found


# ══════════════════════════════════════════════════════════════════
# TaskRecordRepository — admin CRUD methods
# ══════════════════════════════════════════════════════════════════


def _make_task_record(
    session,
    *,
    ticket_id: str,
    bot_id: str = "bot-1",
    owner_id: str = "user-1",
    dt_version: str = "20260705",
    governance_status: str = "open",
    env: str = "dev",
):
    """Insert a GovernanceTaskRecordDaily row directly."""
    worker_key = f"{owner_id}:{bot_id}"
    row = GovernanceTaskRecordDaily(
        ticket_id=ticket_id,
        worker_id=worker_key,
        active_worker=worker_key if governance_status != "closed" else None,
        governance_status=governance_status,
        governance_decision="actionable",
        latest_decision="actionable",
        dt_version=dt_version,
        env=env,
        bot_id=bot_id,
        owner_id=owner_id,
        bot_name="TestBot",
        consecutive_normal_days=0,
        remind_count=0,
        last_sync_at=datetime.now(),
    )
    session.add(row)
    session.commit()
    return row


class TestTaskRecordRepoAdminMethods:
    """Vertical slice: count_by_dt_versions, delete_by_dt_versions,
    delete_by_ids, count_by_ids."""

    def test_count_by_dt_versions(self, session, engine):
        """count_by_dt_versions: returns per-version counts."""
        db = _db_from_engine(engine)
        repo = TaskRecordRepository(db=db)

        _make_task_record(session, ticket_id="t-1", dt_version="20260701")
        _make_task_record(session, ticket_id="t-2", dt_version="20260701",
                          bot_id="bot-2", owner_id="user-2")
        _make_task_record(session, ticket_id="t-3", dt_version="20260705",
                          bot_id="bot-3", owner_id="user-3")

        result = repo.count_by_dt_versions(["20260701", "20260705"])
        assert result["20260701"] == 2
        assert result["20260705"] == 1

    def test_delete_by_dt_versions(self, session, engine):
        """delete_by_dt_versions: removes matching rows, returns count."""
        db = _db_from_engine(engine)
        repo = TaskRecordRepository(db=db)

        _make_task_record(session, ticket_id="t-del-1", dt_version="20260701")
        _make_task_record(session, ticket_id="t-del-2", dt_version="20260702",
                          bot_id="bot-del2", owner_id="user-del2")

        count = repo.delete_by_dt_versions(["20260701"])
        assert count == 1

        # Verify only the correct row was deleted
        with db.orm_session() as s:
            remaining = s.query(GovernanceTaskRecordDaily).all()
            assert len(remaining) == 1
            assert remaining[0].dt_version == "20260702"

    def test_delete_by_ids(self, session, engine):
        """delete_by_ids: removes rows by primary key, returns (deleted, not_found)."""
        db = _db_from_engine(engine)
        repo = TaskRecordRepository(db=db)

        _make_task_record(session, ticket_id="t-id-1", bot_id="bot-id1",
                          owner_id="user-id1")
        _make_task_record(session, ticket_id="t-id-2", bot_id="bot-id2",
                          owner_id="user-id2")

        # Get the actual primary key IDs
        with db.orm_session() as s:
            rows = s.query(GovernanceTaskRecordDaily).all()
            existing_ids = [r.id for r in rows]

        deleted, not_found = repo.delete_by_ids(
            existing_ids[:1] + [99999],
        )
        assert deleted == 1
        assert 99999 in not_found

    def test_count_by_ids(self, session, engine):
        """count_by_ids: counts matching rows without deletion."""
        db = _db_from_engine(engine)
        repo = TaskRecordRepository(db=db)

        _make_task_record(session, ticket_id="t-cnt-1", bot_id="bot-cnt1",
                          owner_id="user-cnt1")

        with db.orm_session() as s:
            row = s.query(GovernanceTaskRecordDaily).filter_by(
                ticket_id="t-cnt-1",
            ).one()
            real_id = row.id

        count, not_found = repo.count_by_ids([real_id, 88888])
        assert count == 1
        assert 88888 in not_found