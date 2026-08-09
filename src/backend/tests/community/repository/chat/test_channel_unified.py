"""Unified Channel repository — behavior + cross-backend contract.

Locks the round-3/session-1 criteria for this entity: a single repo
body, CRUD parity with the prior prod twin, DB-side timestamps, single
atomic UPDATEs, and write auto-save against the real
``SqliteDB.orm_session``. No ZDAS-skipped test — the prod round-trip is
a manual Pre acceptance gate (zero skipped tests in this codebase).
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.chat.channel import ChannelRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """Private MetaData copy of ChannelConfig's columns (order-independent
    vs other tests that patch core.base.Base) — copies server_default so
    DB-side gmt_create/gmt_modified work under SQLite."""
    from agentclaw.community.plugin_api.models import ChannelConfig

    src = ChannelConfig.__table__
    md = MetaData()
    Table(
        src.name,
        md,
        *[
            Column(
                c.name,
                c.type,
                primary_key=c.primary_key,
                nullable=c.nullable,
                autoincrement=c.autoincrement,
                server_default=c.server_default.arg
                if c.server_default is not None
                else None,
            )
            for c in src.columns
        ],
    )
    md.create_all(engine)


class _FileSqliteDB:
    """Faithful DatabasePlugin double: orm_session commits on clean exit /
    rolls back on exception (the SqliteDB.orm_session contract)."""

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


def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ch.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return ChannelRepository(_make_db(tmp_path))


def _ins(repo, **over):
    base = dict(
        type="dingding",
        description="desc",
        identity_id="id1",
        bind_bot_id="bot1",
        config={"k": "v"},
        status="ACTIVE",
        stage=None,
    )
    base.update(over)
    return repo.insert_channel(**base)


def test_insert_returns_id_and_db_timestamps(repo):
    cid = _ins(repo)
    assert isinstance(cid, int) and cid > 0
    rec = repo.get_by_id(cid)
    assert rec.gmt_create is not None  # server_default
    assert rec.gmt_modified is not None


def test_insert_persists_fields(repo):
    cid = _ins(repo, description="hello", config={"a": 1}, stage="draft")
    rec = repo.get_by_id(cid)
    assert rec.type == "dingding"
    assert rec.description == "hello"
    assert rec.config == {"a": 1}
    assert rec.deleted == 0
    assert rec.stage == "draft"


def test_get_by_type_and_identity_ids_filters(repo):
    _ins(repo, type="dingding", identity_id="a")
    _ins(repo, type="feishu", identity_id="a")
    _ins(repo, identity_id="b")
    rows = repo.get_by_type_and_identity_ids(
        type="dingding", identity_ids=["a", "b"], bind_bot_id="bot1"
    )
    assert {r.identity_id for r in rows} == {"a", "b"}
    assert all(r.type == "dingding" for r in rows)


def test_get_by_type_excludes_deleted(repo):
    cid = _ins(repo, identity_id="a")
    repo.delete_by_id(channel_id=cid)
    rows = repo.get_by_type_and_identity_ids(
        type="dingding", identity_ids=["a"], bind_bot_id="bot1"
    )
    assert rows == []


def test_empty_identity_list_raises(repo):
    with pytest.raises(ValueError):
        repo.get_by_type_and_identity_ids(
            type="dingding", identity_ids=[], bind_bot_id="bot1"
        )


def test_get_by_id_missing_returns_none(repo):
    assert repo.get_by_id(99999) is None


def test_get_by_id_has_no_deleted_guard_prod_parity(repo):
    # Prod twin's get_by_id did NOT filter deleted=0; unified must match.
    cid = _ins(repo)
    repo.delete_by_id(channel_id=cid)
    rec = repo.get_by_id(cid)
    assert rec is not None and rec.deleted == 1


def test_update_by_id_replaces_all_fields(repo):
    cid = _ins(repo, stage="draft")
    repo.update_by_id(
        channel_id=cid,
        type="feishu",
        description="new",
        identity_id="id2",
        bind_bot_id="bot2",
        config={"x": 2},
        status="DISABLED",
        stage="online",
    )
    rec = repo.get_by_id(cid)
    assert rec.type == "feishu"
    assert rec.identity_id == "id2"
    assert rec.config == {"x": 2}
    assert rec.status == "DISABLED"
    assert rec.stage == "online"


def test_update_by_id_is_blind_no_deleted_guard(repo):
    # Prod's UPDATE has no deleted guard — a soft-deleted row is still
    # updatable.
    cid = _ins(repo)
    repo.delete_by_id(channel_id=cid)
    repo.update_status_by_id(channel_id=cid, status="REENABLED")
    assert repo.get_by_id(cid).status == "REENABLED"


def test_delete_soft_deletes(repo):
    cid = _ins(repo)
    repo.delete_by_id(channel_id=cid)
    assert repo.get_by_id(cid).deleted == 1


def test_real_sqlitedb_orm_session_commits(tmp_path, monkeypatch):
    """Exercise the actual SqliteDB.orm_session() (not the double)."""
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    db_mod = importlib.import_module("agentclaw.community.plugins.local.database")
    db_mod._engine = None
    db_mod._session_factory = None
    try:
        db_mod._get_session_factory()
        _create_schema(db_mod._engine)
        sqlite_db = db_mod.SqliteDB()
        cid = ChannelRepository(sqlite_db).insert_channel(
            type="t",
            description=None,
            identity_id="i",
            bind_bot_id="b",
            config={"q": 1},
            status="ACTIVE",
            stage="draft",
        )
        rec = ChannelRepository(sqlite_db).get_by_id(cid)
        assert rec is not None and rec.config == {"q": 1}
        assert rec.stage == "draft"
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


# ========== stage 字段测试 ==========

def test_insert_persists_stage(repo):
    """覆盖 insert_channel 的 stage 参数写入"""
    cid = _ins(repo, stage="draft")
    rec = repo.get_by_id(cid)
    assert rec.stage == "draft"


def test_insert_stage_null(repo):
    """覆盖 stage 默认为 None"""
    cid = _ins(repo, stage=None)
    rec = repo.get_by_id(cid)
    assert rec.stage is None


def test_update_by_id_updates_stage(repo):
    """覆盖 update_by_id 的 stage 参数更新"""
    cid = _ins(repo, stage="draft")
    repo.update_by_id(
        channel_id=cid,
        type="dingding",
        description="new",
        identity_id="id1",
        bind_bot_id="bot1",
        config={},
        status="ACTIVE",
        stage="online",
    )
    rec = repo.get_by_id(cid)
    assert rec.stage == "online"


def test_update_by_id_clears_stage(repo):
    """覆盖 update_by_id 清空 stage (设为 None)"""
    cid = _ins(repo, stage="draft")
    repo.update_by_id(
        channel_id=cid,
        type="dingding",
        description="new",
        identity_id="id1",
        bind_bot_id="bot1",
        config={},
        status="ACTIVE",
        stage=None,
    )
    rec = repo.get_by_id(cid)
    assert rec.stage is None


def test_get_by_type_and_identity_ids_includes_stage(repo):
    """覆盖 _row_to_record 返回 stage 字段"""
    _ins(repo, identity_id="user1", stage="verify")
    _ins(repo, identity_id="user2", stage="online")
    rows = repo.get_by_type_and_identity_ids(
        type="dingding",
        identity_ids=["user1", "user2"],
        bind_bot_id="bot1",
    )
    assert len(rows) == 2
    stages = {r.identity_id: r.stage for r in rows}
    assert stages["user1"] == "verify"
    assert stages["user2"] == "online"
