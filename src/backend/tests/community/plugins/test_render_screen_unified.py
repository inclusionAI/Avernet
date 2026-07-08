"""Unified RenderScreen repository — behavior + cross-backend contract.

Round-2 spec criteria for this entity: single body, domain round-trip
(RenderScreenRecord ↔ ac_bot_render_screen), the (bot_id,name,env)
duplicate path converged to a uniform ValueError, soft-delete, auto-save
(verified against the real SqliteDB.orm_session). No ZDAS-skipped test
(zero skips policy); prod round-trip is the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.render_screen_repository import (
    RenderScreenRepository,
)

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """ac_bot_render_screen from a private MetaData copy of the model's
    columns — order-independent vs Base-patching tests (pilot precedent).
    No unique constraint: prod has none on (bot_id, name, env), so the
    model/local schema must not either (dedup is service-layer)."""
    from agentclaw.community.core.bot_management.render_screen.sqlite_models import (
        RenderScreenModel,
    )

    src = RenderScreenModel.__table__
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
            )
            for c in src.columns
        ],
    )
    md.create_all(engine)


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


def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rs.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return RenderScreenRepository(_make_db(tmp_path))


def _ins(repo, **over):
    base = dict(
        bot_id="bot-1",
        owner_id="owner-1",
        name="screen-1",
        cdn_url="https://cdn/x.png",
        creator_id="u1",
    )
    base.update(over)
    return repo.insert(**base)


def test_insert_and_get_round_trip(repo):
    rid = _ins(repo)
    assert isinstance(rid, int) and rid > 0
    rec = repo.get_by_id(rid)
    assert rec is not None
    assert rec.bot_id == "bot-1"
    assert rec.name == "screen-1"
    assert rec.cdn_url == "https://cdn/x.png"
    assert rec.is_delete == 0


def test_insert_auto_saves_without_explicit_commit(tmp_path):
    db = _make_db(tmp_path)
    rid = _ins(RenderScreenRepository(db), name="persist")
    assert RenderScreenRepository(db).get_by_id(rid) is not None


def test_repo_does_not_dedup_duplicates_are_allowed(repo):
    # Prod ac_bot_render_screen has no (bot_id,name,env) unique index,
    # so the repo must NOT reject a duplicate (dedup is the service
    # layer's job). Two inserts with the same name both succeed.
    id1 = _ins(repo, name="dup")
    id2 = _ins(repo, name="dup")
    assert id1 != id2
    assert {r.id for r in repo.list_by_bot_id(bot_id="bot-1", owner_id="owner-1")} == {id1, id2}


def test_list_by_bot_id_excludes_deleted(repo):
    a = _ins(repo, name="a")
    b = _ins(repo, name="b")
    c = _ins(repo, name="c")
    repo.delete_by_id(record_id=a)
    listed = repo.list_by_bot_id(bot_id="bot-1", owner_id="owner-1")
    # soft-deleted row excluded; the other two returned. (Order is
    # gmt_create desc; not asserted here — utcnow() ties have no
    # deterministic tiebreaker, so a strict-order assert would be flaky.)
    assert {r.id for r in listed} == {b, c}


def test_update_by_id(repo):
    rid = _ins(repo, name="orig")
    repo.update_by_id(record_id=rid, name="new", cdn_url="https://cdn/n.png")
    rec = repo.get_by_id(rid)
    assert rec.name == "new"
    assert rec.cdn_url == "https://cdn/n.png"


def test_update_missing_is_noop(repo):
    repo.update_by_id(record_id=999999, name="x", cdn_url="y")
    assert repo.get_by_id(999999) is None


def test_update_does_not_resurrect_soft_deleted_row(repo):
    # The is_delete=0 guard must stay in the UPDATE WHERE: a row
    # soft-deleted (concurrently) before update_by_id must NOT be
    # written/resurrected. This is the exact race the conditional
    # bulk-UPDATE fix restores.
    rid = _ins(repo, name="will-delete")
    repo.delete_by_id(record_id=rid)
    repo.update_by_id(record_id=rid, name="resurrected",
                      cdn_url="https://cdn/z.png")
    assert repo.get_by_id(rid) is None


def test_delete_by_id_soft_deletes(repo):
    rid = _ins(repo, name="del")
    repo.delete_by_id(record_id=rid)
    assert repo.get_by_id(rid) is None
    assert repo.list_by_bot_id(bot_id="bot-1", owner_id="owner-1") == []


def test_real_sqlitedb_orm_session_commits(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    db_mod = importlib.import_module("agentclaw.community.plugins.local.database")
    db_mod._engine = None
    db_mod._session_factory = None
    try:
        db_mod._get_session_factory()
        _create_schema(db_mod._engine)
        sqlite_db = db_mod.SqliteDB()
        rid = _ins(RenderScreenRepository(sqlite_db), name="real")
        RenderScreenRepository(sqlite_db).update_by_id(
            record_id=rid, name="real2", cdn_url="https://cdn/r.png"
        )
        got = RenderScreenRepository(sqlite_db).get_by_id(rid)
        assert got is not None and got.name == "real2"
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


def test_contract_local_backend(tmp_path):
    repo = RenderScreenRepository(_make_db(tmp_path))
    rid = _ins(repo, name="contract")
    rec = repo.get_by_id(rid)
    assert rec.name == "contract" and rec.id == rid
    repo.delete_by_id(record_id=rid)
    assert repo.get_by_id(rid) is None


# NOTE: no ZDAS/prod contract test — would be a permanently-skipped test
# (no live OceanBase in CI), which is not allowed. The single unified
# body is exercised in CI by the local leg + real-SqliteDB test; the ZDAS
# round-trip is the manual Pre acceptance gate (round-2 tasks.md Task 7).
