"""Coverage supplement — repo methods not covered by existing tests.

Targets (P2 sorted):
  - notify_log_repo: get_by_notification_id,
    count_pending, insert_notification, delete_by_notification_ids,
    count_by_notification_ids
  - task_record_repo: count_by_dt_versions, delete_by_dt_versions,
    delete_by_ids, count_by_ids

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
from agentclaw.community.core.economy.governance.repositories.orm import (
    GovernanceNotificationOrm,
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
    _extract_owner_id,
)

from .conftest import FakeDB


# ── Module-level helpers ────────────────────────────────────────


class TestExtractOwnerId:
    """_extract_owner_id splits '{owner_id}:{bot_id}' on the first colon."""

    def test_splits_owner_and_bot(self):
        assert _extract_owner_id("user-1:bot-9") == "user-1"

    def test_no_colon_returns_input_unchanged(self):
        assert _extract_owner_id("noColonHere") == "noColonHere"


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

    import agentclaw.community.core.economy.governance.repositories.orm  # noqa: F401
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
) -> GovernanceNotificationOrm:
    """Insert a GovernanceNotificationOrm row directly via session."""
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
    """Single vertical-slice: seed data → exercise uncovered query methods."""

    def test_get_by_notification_id(self, session, engine):
        """get_by_notification_id: fetch by notification_id."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        _make_notify(session, notification_id="n-fetch-1", bot_id="bot-c",
                     owner_id="user-c")

        result = repo.get_by_notification_id("n-fetch-1")
        assert result is not None
        assert result.bot_id == "bot-c"

    def test_get_by_notification_id_not_found(self, session, engine):
        """get_by_notification_id: non-existent → None."""
        db = _db_from_engine(engine)
        repo = NotifyLogRepository(db=db)

        result = repo.get_by_notification_id("n-nonexistent")
        assert result is None

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

        row = GovernanceNotificationOrm(
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
            found = s.query(GovernanceNotificationOrm).filter_by(
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
    """Insert a GovernanceTicketOrm row directly."""
    worker_key = f"{owner_id}:{bot_id}"
    row = GovernanceTicketOrm(
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
            remaining = s.query(GovernanceTicketOrm).all()
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
            rows = s.query(GovernanceTicketOrm).all()
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
            row = s.query(GovernanceTicketOrm).filter_by(
                ticket_id="t-cnt-1",
            ).one()
            real_id = row.id

        count, not_found = repo.count_by_ids([real_id, 88888])
        assert count == 1
        assert 88888 in not_found