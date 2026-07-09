"""Unit tests for DormantInternalService."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from agentclaw.community.core.bot_dormant.internal_service import DormantInternalService
from agentclaw.community.core.bot_dormant.sqlite_models import DormantNotifyLog
from agentclaw.community.plugin_api.models import Base, BotModel


def _make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


class FakeDB:
    def __init__(self, session: Session) -> None:
        self._session = session

    @contextmanager
    def orm_session(self):
        yield self._session


def _insert_bot(
    session, bot_id: str, status: str = "RECYCLED", owner_id: str = "ow",
) -> None:
    session.add(BotModel(
        bot_id=bot_id, entity_id="123", entity_type="staff",
        creator_id=owner_id, owner_id=owner_id, bot_name=bot_id,
        bot_type="personal", status=status, is_delete=0,
        gmt_create=datetime.now(), gmt_modified=datetime.now(),
    ))
    session.commit()


def _insert_notify(
    session, *, bot_id: str, owner_id: str = "ow", notify_type: str = "warn",
    send_status: str = "pending", dry_run: int = 0,
) -> int:
    row = DormantNotifyLog(
        bot_id=bot_id, owner_id=owner_id, entity_id="123",
        notify_type=notify_type,
        notify_target="ow_staff", notify_source="internal_scan",
        content=f"hello {bot_id}", dt="20260623",
        send_status=send_status, dry_run=dry_run,
    )
    session.add(row)
    session.commit()
    return row.id


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_list_pending_returns_only_pending_non_dryrun():
    session = _make_session()
    _insert_bot(session, "bot_a", status="ACTIVE")
    _insert_bot(session, "bot_a2", status="ACTIVE")
    _insert_bot(session, "bot_a3", status="ACTIVE")
    pending_id = _insert_notify(session, bot_id="bot_a", notify_type="warn",
                                send_status="pending", dry_run=0)
    _insert_notify(session, bot_id="bot_a2", notify_type="warn",
                   send_status="sent", dry_run=0)
    _insert_notify(session, bot_id="bot_a3", notify_type="warn",
                   send_status="pending", dry_run=1)  # dry_run skipped

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=10)
    assert [r["id"] for r in rows] == [pending_id]


@pytest.mark.unit
def test_list_pending_include_dry_run_returns_dryrun_rows():
    """include_dry_run=True: dry_run=1 rows are returned too (pre/dev test path)."""
    session = _make_session()
    _insert_bot(session, "bot_dr1", status="ACTIVE")
    _insert_bot(session, "bot_dr2", status="ACTIVE")
    real_id = _insert_notify(session, bot_id="bot_dr1", dry_run=0)
    dry_id = _insert_notify(session, bot_id="bot_dr2", dry_run=1)

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=10, include_dry_run=True)
    ids = sorted(r["id"] for r in rows)
    assert ids == sorted([real_id, dry_id])


@pytest.mark.unit
def test_list_pending_filters_recycle_when_bot_not_recycled_anymore():
    """A recycle notification whose bot was meanwhile reactivated must be
    cancelled (not returned)."""
    session = _make_session()
    _insert_bot(session, "bot_b", status="ACTIVE")  # NOT RECYCLED anymore
    nid = _insert_notify(session, bot_id="bot_b", notify_type="recycle",
                         send_status="pending")

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=10)
    assert rows == []
    # Row was marked cancelled
    row = (session.query(DormantNotifyLog)
           .filter(DormantNotifyLog.id == nid).first())
    assert row.send_status == "cancelled"


@pytest.mark.unit
def test_list_pending_returns_recycle_when_bot_is_recycled():
    session = _make_session()
    _insert_bot(session, "bot_c", status="RECYCLED")
    nid = _insert_notify(session, bot_id="bot_c", notify_type="recycle",
                         send_status="pending")

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=10)
    assert [r["id"] for r in rows] == [nid]


@pytest.mark.unit
def test_list_pending_filters_recycle_when_bot_missing():
    """No matching BotModel row → also cancelled."""
    session = _make_session()
    nid = _insert_notify(session, bot_id="ghost", notify_type="recycle",
                         send_status="pending")

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=10)
    assert rows == []
    row = (session.query(DormantNotifyLog)
           .filter(DormantNotifyLog.id == nid).first())
    assert row.send_status == "cancelled"


@pytest.mark.unit
def test_list_pending_recycle_gate_isolates_default_bot_per_owner():
    """bot_id='default' is per-owner; the recycle gate must match (bot_id,
    owner_id), not bot_id alone, or else owner A's gate decision leaks
    onto owner B's default bot.

    Setup:
      - owner A has a default bot still ACTIVE (the recycle notify must
        be cancelled)
      - owner B has a default bot already RECYCLED (the recycle notify
        must be returned to the in-container sender)

    A bot_id-only gate would pick whichever default bot the DB returned
    first and apply that single status to both notifies — wrong.
    """
    session = _make_session()
    _insert_bot(session, "default", owner_id="ownerA", status="ACTIVE")
    _insert_bot(session, "default", owner_id="ownerB", status="RECYCLED")
    a_nid = _insert_notify(session, bot_id="default", owner_id="ownerA",
                           notify_type="recycle", send_status="pending")
    b_nid = _insert_notify(session, bot_id="default", owner_id="ownerB",
                           notify_type="recycle", send_status="pending")

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=10)

    # Only owner B's recycle notify survives the gate
    assert [r["id"] for r in rows] == [b_nid]

    a_row = (session.query(DormantNotifyLog)
             .filter(DormantNotifyLog.id == a_nid).first())
    b_row = (session.query(DormantNotifyLog)
             .filter(DormantNotifyLog.id == b_nid).first())
    assert a_row.send_status == "cancelled"
    assert b_row.send_status == "pending"


@pytest.mark.unit
def test_list_pending_respects_limit_and_orders_by_gmt_create():
    session = _make_session()
    # Use distinct bot_ids to avoid the UniqueConstraint(bot_id, dt, notify_type)
    bot_ids = [f"bot_d{i}" for i in range(5)]
    for bid in bot_ids:
        _insert_bot(session, bid, status="ACTIVE")
    ids = [_insert_notify(session, bot_id=bid) for bid in bot_ids]

    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    rows = svc.list_pending(limit=3)
    assert [r["id"] for r in rows] == ids[:3]


# ---------------------------------------------------------------------------
# mark_sent
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_mark_sent_success_path():
    session = _make_session()
    _insert_bot(session, "bot_e", status="ACTIVE")
    nid = _insert_notify(session, bot_id="bot_e")
    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    assert svc.mark_sent(notify_log_id=nid, success=True) == "sent"
    row = session.query(DormantNotifyLog).filter(DormantNotifyLog.id == nid).first()
    assert row.send_status == "sent"
    assert row.error_msg is None


@pytest.mark.unit
def test_mark_sent_failure_requires_error_msg():
    svc = DormantInternalService(db=FakeDB(_make_session()),
                                  bot_service=MagicMock())
    with pytest.raises(ValueError):
        svc.mark_sent(notify_log_id=1, success=False)
    with pytest.raises(ValueError):
        svc.mark_sent(notify_log_id=1, success=False, error_msg="")


@pytest.mark.unit
def test_mark_sent_failure_records_error_msg():
    session = _make_session()
    _insert_bot(session, "bot_f", status="ACTIVE")
    nid = _insert_notify(session, bot_id="bot_f")
    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    assert svc.mark_sent(notify_log_id=nid, success=False,
                          error_msg="dingtalk 500") == "failed"
    row = session.query(DormantNotifyLog).filter(DormantNotifyLog.id == nid).first()
    assert row.send_status == "failed"
    assert row.error_msg == "dingtalk 500"


@pytest.mark.unit
def test_mark_sent_idempotent_on_already_resolved():
    session = _make_session()
    _insert_bot(session, "bot_g", status="ACTIVE")
    nid = _insert_notify(session, bot_id="bot_g", send_status="sent")
    svc = DormantInternalService(db=FakeDB(session), bot_service=MagicMock())
    assert svc.mark_sent(notify_log_id=nid, success=True) == "already_resolved"


@pytest.mark.unit
def test_mark_sent_not_found():
    svc = DormantInternalService(db=FakeDB(_make_session()),
                                  bot_service=MagicMock())
    assert svc.mark_sent(notify_log_id=999, success=True) == "not_found"
