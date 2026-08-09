"""Unified BotFriend repository — behavior + contract.

Round-3/session-4 criteria: single ORM body,
``BotFriendRepositoryProtocol`` parity. Covers: plain INSERT (no
upsert despite uk_entity_bot_id_env), env-scoped reads/updates (adopt
prod — the old SQLite twin did NO env filtering), the accept/reject/
cancel state machine, soft_delete→cancel (status flip, row stays),
and the approval-in-ext operations.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_public.repository.models import (
    BotFriendModel,
    BotFriendStatus,
)
from agentclaw.community.core.repository.implementations.bot.friend import BotFriendRepository
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bf.db'}",
        connect_args={"check_same_thread": False},
    )
    BotFriendModel.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(db):
    return BotFriendRepository(db)


def _data(**ov):
    base = dict(
        requester_entity_id="staff_req",
        requester_name="Req",
        target_entity_id="staff_tgt",
        target_name="Tgt",
        target_bot_id="bot-123",
        target_owner_name="Owner",
        status=BotFriendStatus.PENDING,
        ext={"approvals": [{"uuid": "u1", "status": "PENDING",
                            "approval_type": "MANUAL"}]},
    )
    base.update(ov)
    return base


# ── insert (plain INSERT, env forced) ───────────────────────────────

def test_insert_and_get(repo):
    rec = repo.insert(_data())
    assert rec["id"] > 0
    assert rec["env"] == get_current_env()
    got = repo.get_by_id(rec["id"])
    assert got["requester_entity_id"] == "staff_req"
    assert got["ext"]["approvals"][0]["uuid"] == "u1"


def test_insert_is_plain_not_upsert(repo):
    a = repo.insert(_data())
    b = repo.insert(_data())
    assert a["id"] != b["id"]


# ── env-scoping (adopt prod) ────────────────────────────────────────

def test_get_by_id_env_scoped(repo, db):
    rec = repo.insert(_data())
    # Flip env on the row → env-scoped get must no longer see it.
    with db.orm_session() as s:
        s.query(BotFriendModel).filter(
            BotFriendModel.id == rec["id"]
        ).update({BotFriendModel.env: "other-env"})
    assert repo.get_by_id(rec["id"]) is None


def test_list_by_requester_env_scoped(repo, db):
    r1 = repo.insert(_data())
    repo.insert(_data(requester_entity_id="staff_req"))
    with db.orm_session() as s:
        s.query(BotFriendModel).filter(
            BotFriendModel.id == r1["id"]
        ).update({BotFriendModel.env: "other-env"})
    total, rows = repo.list_by_requester("staff_req")
    assert total == 1
    assert all(x["env"] == get_current_env() for x in rows)


def test_list_by_target_and_bot_id(repo):
    repo.insert(_data())
    t, rows = repo.list_by_target("staff_tgt")
    assert t == 1
    t2, rows2 = repo.list_by_target_bot_id("bot-123")
    assert t2 == 1


def test_get_by_entity_ids_and_batch(repo):
    from agentclaw.community.core.bot_public.repository.models import (
        BotFriendQueryKey,
    )

    repo.insert(_data())
    one = repo.get_by_entity_ids("staff_req", "staff_tgt", "bot-123")
    assert one is not None
    batch = repo.get_by_entity_ids_batch([
        BotFriendQueryKey(
            requester_entity_id="staff_req",
            target_entity_id="staff_tgt",
            target_bot_id="bot-123",
        )
    ])
    assert len(batch) == 1


# ── state machine ───────────────────────────────────────────────────

def test_accept_reject_cancel(repo):
    rec = repo.insert(_data())
    out = repo.accept(rec["id"], approval_uuid="u1")
    assert out["status"] == BotFriendStatus.ACCEPTED
    assert out["ext"]["approvals"][0]["status"] == "APPROVED"

    rec2 = repo.insert(_data())
    out2 = repo.reject(rec2["id"], reject_reason="nope",
                       approval_uuid="u1")
    assert out2["status"] == BotFriendStatus.REJECTED
    assert out2["ext"]["approvals"][0]["reject_reason"] == "nope"

    rec3 = repo.insert(_data())
    out3 = repo.cancel(rec3["id"], approval_uuid="u1")
    assert out3["status"] == BotFriendStatus.CANCELLED


def test_soft_delete_is_cancel_not_hard(repo):
    rec = repo.insert(_data())
    assert repo.soft_delete(rec["id"]) is True
    got = repo.get_by_id(rec["id"])  # row still present
    assert got is not None
    assert got["status"] == BotFriendStatus.CANCELLED


def test_update_missing_returns_none(repo):
    assert repo.accept(999999) is None
    assert repo.cancel(999999) is None


# ── approvals in ext ────────────────────────────────────────────────

def test_approval_ops(repo):
    rec = repo.insert(_data(ext=None))
    created = repo.create_approval(
        rec["id"], {"uuid": "ux", "request_message": "hi"}
    )
    assert created is not None
    assert repo.get_approval_by_uuid(rec["id"], "ux")["uuid"] == "ux"
    assert repo.get_latest_approval(rec["id"])["uuid"] == "ux"
    assert len(repo.list_approvals(rec["id"])) == 1

    upd = repo.update_approval_status(rec["id"], "ux", "APPROVED")
    assert upd["status"] == BotFriendStatus.ACCEPTED
