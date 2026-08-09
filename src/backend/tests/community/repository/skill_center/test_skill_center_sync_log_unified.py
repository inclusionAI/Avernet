"""Unified SkillCenterSyncLog repository — behavior + cross-backend contract.

Locks the round-2 spec criteria for this entity: a single repo body, CRUD
parity, auto-save (write persists without an explicit commit, verified
against the real ``SqliteDB.orm_session``), and ``find_latest`` ordering.
No ZDAS-skipped test — the prod round-trip is a manual Pre acceptance gate
(zero skipped tests in this codebase).
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.skill_center.sync_log import SkillCenterSyncLogRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """Create ac_skill_center_sync_log from a private MetaData copy of the
    model's columns — order-independent vs other tests that patch
    core.base.Base / re-exec the model module (pilot precedent)."""
    from agentclaw.community.core.models.skill_center_sync_log import SkillCenterSyncLog

    src = SkillCenterSyncLog.__table__
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
        f"sqlite:///{tmp_path / 'sync.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return SkillCenterSyncLogRepository(_make_db(tmp_path))


def _payload(**over):
    base = {
        "skill_uuid": "u-1",
        "version": "1.0.0",
        "env": "dev",
        "status": "pending",
    }
    base.update(over)
    return base


def test_create_returns_dict_with_id_and_timestamps(repo):
    rec = repo.create(_payload())
    assert rec["id"] is not None
    assert rec["skill_uuid"] == "u-1"
    assert rec["status"] == "pending"
    assert rec["gmt_created"] is not None
    assert rec["gmt_modified"] is not None


def test_create_auto_saves_without_explicit_commit(tmp_path):
    db = _make_db(tmp_path)
    SkillCenterSyncLogRepository(db).create(_payload(skill_uuid="persist"))
    found = SkillCenterSyncLogRepository(db).find_latest("persist", "dev")
    assert found is not None and found["skill_uuid"] == "persist"


def test_mark_success_only_affects_pending(repo):
    repo.create(_payload())
    repo.mark_success("u-1", "1.0.0", "dev", checksum="abc")
    got = repo.find_latest("u-1", "dev")
    assert got["status"] == "success"
    assert got["checksum"] == "abc"
    # second mark_success is a no-op (no pending row left)
    repo.mark_success("u-1", "1.0.0", "dev", checksum="zzz")
    assert repo.find_latest("u-1", "dev")["checksum"] == "abc"


def test_mark_failed_sets_error_and_only_pending(repo):
    repo.create(_payload())
    repo.mark_failed("u-1", "1.0.0", "dev", error_msg="boom")
    got = repo.find_latest("u-1", "dev")
    assert got["status"] == "failed"
    assert got["error_msg"] == "boom"


def test_mark_missing_row_is_noop(repo):
    repo.mark_success("nope", "9.9.9", "dev")
    repo.mark_failed("nope", "9.9.9", "dev", error_msg="x")
    assert repo.find_latest("nope", "dev") is None


def test_find_latest_returns_newest_and_scoped(repo):
    # Explicit distinct gmt_created so ordering is deterministic:
    # find_latest is ORDER BY gmt_created DESC (prod parity, no id
    # tiebreaker), and func.now() is only second-resolution.
    repo.create(_payload(version="1.0.0",
                         gmt_created="2026-01-01 00:00:00"))
    repo.create(_payload(version="2.0.0",
                         gmt_created="2026-01-02 00:00:00"))
    latest = repo.find_latest("u-1", "dev")
    assert latest["version"] == "2.0.0"
    assert repo.find_latest("u-1", "prod") is None
    assert repo.find_latest("other", "dev") is None


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
        SkillCenterSyncLogRepository(sqlite_db).create(
            _payload(skill_uuid="real-1")
        )
        SkillCenterSyncLogRepository(sqlite_db).mark_success(
            "real-1", "1.0.0", "dev", checksum="ck"
        )
        got = SkillCenterSyncLogRepository(sqlite_db).find_latest(
            "real-1", "dev"
        )
        assert got is not None and got["status"] == "success"
        assert got["checksum"] == "ck"
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


def test_contract_local_backend(tmp_path):
    repo = SkillCenterSyncLogRepository(_make_db(tmp_path))
    created = repo.create(_payload(skill_uuid="contract"))
    assert set(created) >= {
        "id", "skill_uuid", "version", "env", "status", "checksum",
        "error_msg", "extra", "gmt_created", "gmt_modified",
    }
    repo.mark_success("contract", "1.0.0", "dev", checksum="c1")
    found = repo.find_latest("contract", "dev")
    assert found["status"] == "success"


# NOTE: no ZDAS/prod contract test — it would need a live OceanBase
# connection absent from CI, i.e. a permanently-skipped test, which is not
# allowed here. The unified body is a single implementation; the local
# leg above + the real-SqliteDB test exercise it in CI. The ZDAS
# round-trip is a manual Pre acceptance gate (round-2 tasks.md Task 7).
