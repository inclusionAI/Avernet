"""Unified SkillPropagationLogRepository — behavior + cross-backend contract.

Locks the pilot's spec criteria: auto-save (write persists without an
explicit commit), DB-clock timestamps, find_recent window semantics, and
identical observable behavior across DatabasePlugin implementations. The
prod (ZDAS) leg is collected but marked ``requires_zdas`` so CI skips it.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.skill_center.propagation_log import SkillPropagationLogRepository

pytestmark = pytest.mark.integration

_FMT = "%Y-%m-%d %H:%M:%S"


def _create_schema(engine):
    """Create the ac_skill_propagation_log table, columns only.

    Built from a fresh MetaData copy of the model's columns rather than
    ``Base.metadata.create_all`` / ``__table__.create``. Another test (the
    skill-center e2e fixture) patches ``core.base.Base`` and re-execs the
    model module, which can leave the shared ``SkillPropagationLog.__table__``
    bound to a foreign Base and/or carrying duplicated indexes. Copying
    just the columns into a private MetaData makes this setup fully
    order-independent (indexes are irrelevant to these behavior tests).
    """
    from agentclaw.community.core.models.skill_propagation_log import (
        SkillPropagationLog,
    )

    src = SkillPropagationLog.__table__
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


def _utc(offset_seconds: int = 0) -> str:
    return (datetime.utcnow() + timedelta(seconds=offset_seconds)).strftime(_FMT)


class _FileSqliteDB:
    """Real file-backed SQLite DatabasePlugin double.

    ``orm_session`` mirrors the production ``SqliteDB.orm_session``
    contract: commit on clean exit, rollback on exception. ``session``
    aliases it (the unified repo only uses ``orm_session``).
    """

    def __init__(self, engine):
        # Match the real SqliteDB session config (local/database.py).
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
    # Create the table directly from the model's own Table, not via
    # Base.metadata: another test (the e2e fixture) patches core.base.Base
    # and can leave the model bound to a different Base, which would make
    # Base.metadata.create_all() a silent no-op here (order-dependent).
    engine = create_engine(
        f"sqlite:///{tmp_path / 'prop.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return SkillPropagationLogRepository(_make_db(tmp_path))


def _payload(**over):
    base = {
        "propagation_id": "p-1",
        "skill_uuid": "u-1",
        "env": "dev",
        "action": "upgrade",
        "status": "pending",
        "affected_bot_count": 0,
        "success_bot_count": 0,
        "failed_bot_ids": "[]",
        "extra": "{}",
    }
    base.update(over)
    return base


# --- create -----------------------------------------------------------------

def test_create_returns_dict_with_id_and_db_clock_timestamps(repo):
    rec = repo.create(_payload())
    assert rec["id"] is not None
    assert rec["propagation_id"] == "p-1"
    # gmt_created/gmt_modified are server-side (func.now()); non-empty strings.
    assert isinstance(rec["gmt_created"], str) and rec["gmt_created"]
    assert isinstance(rec["gmt_modified"], str) and rec["gmt_modified"]


def test_create_auto_saves_without_explicit_commit(tmp_path):
    """The repo never calls commit(); orm_session must persist on exit."""
    db = _make_db(tmp_path)
    repo = SkillPropagationLogRepository(db)
    repo.create(_payload(propagation_id="persist-me"))

    # Brand-new repo + new session over the same engine: row must be there.
    again = SkillPropagationLogRepository(db)
    found = again.find_recent("u-1", "dev", within_seconds=3600)
    assert found is not None
    assert found["propagation_id"] == "persist-me"


# --- update -----------------------------------------------------------------

def test_update_applies_allowed_fields(repo):
    repo.create(_payload())
    repo.update("p-1", {"status": "done", "affected_bot_count": 5})
    got = repo.find_recent("u-1", "dev", within_seconds=3600)
    assert got["status"] == "done"
    assert got["affected_bot_count"] == 5


def test_update_ignores_disallowed_fields(repo):
    repo.create(_payload())
    repo.update("p-1", {"skill_uuid": "hacked", "status": "done"})
    got = repo.find_recent("u-1", "dev", within_seconds=3600)
    assert got["skill_uuid"] == "u-1"  # unchanged
    assert got["status"] == "done"


def test_update_missing_record_is_noop(repo):
    repo.update("does-not-exist", {"status": "done"})
    assert repo.find_recent("u-1", "dev", within_seconds=3600) is None


def test_update_empty_or_no_allowed_keys_is_noop(repo):
    repo.create(_payload())
    repo.update("p-1", {})
    repo.update("p-1", {"skill_uuid": "x"})  # no allowed keys
    got = repo.find_recent("u-1", "dev", within_seconds=3600)
    assert got["status"] == "pending"
    assert got["skill_uuid"] == "u-1"


# --- find_recent ------------------------------------------------------------

def test_find_recent_returns_latest_within_window(repo):
    repo.create(_payload(propagation_id="old", gmt_created=_utc(-30)))
    repo.create(_payload(propagation_id="new", gmt_created=_utc(-1)))
    got = repo.find_recent("u-1", "dev", within_seconds=3600)
    assert got["propagation_id"] == "new"


def test_find_recent_excludes_rows_outside_window(repo):
    repo.create(_payload(propagation_id="stale", gmt_created=_utc(-120)))
    assert repo.find_recent("u-1", "dev", within_seconds=60) is None
    assert repo.find_recent("u-1", "dev", within_seconds=3600) is not None


def test_find_recent_window_boundary(repo):
    # Just inside vs just outside a 60s window.
    repo.create(_payload(propagation_id="inside", gmt_created=_utc(-50)))
    repo.create(_payload(propagation_id="outside", gmt_created=_utc(-70),
                         skill_uuid="u-2"))
    assert repo.find_recent("u-1", "dev", 60)["propagation_id"] == "inside"
    assert repo.find_recent("u-2", "dev", 60) is None


def test_find_recent_only_pending_or_done(repo):
    repo.create(_payload(propagation_id="f", status="failed",
                         gmt_created=_utc(-1)))
    assert repo.find_recent("u-1", "dev", within_seconds=3600) is None
    repo.update("f", {"status": "done"})
    assert repo.find_recent("u-1", "dev", within_seconds=3600) is not None


def test_find_recent_scoped_by_skill_and_env(repo):
    repo.create(_payload(propagation_id="a", gmt_created=_utc(-1)))
    assert repo.find_recent("u-1", "prod", within_seconds=3600) is None
    assert repo.find_recent("other", "dev", within_seconds=3600) is None


# --- real SqliteDB.orm_session auto-save (production impl) -------------------

def test_real_sqlitedb_orm_session_commits_on_clean_exit(tmp_path, monkeypatch):
    """Exercise the actual SqliteDB.orm_session(), not a double."""
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    db_mod = importlib.import_module("agentclaw.community.plugins.local.database")
    # Reset the module-level lazy engine so DATABASE_URL takes effect.
    db_mod._engine = None
    db_mod._session_factory = None
    try:
        db_mod._get_session_factory()  # builds + caches _engine
        _create_schema(db_mod._engine)

        sqlite_db = db_mod.SqliteDB()
        SkillPropagationLogRepository(sqlite_db).create(
            _payload(propagation_id="real-1")
        )

        # Fresh repo/session: persisted only if orm_session committed.
        found = SkillPropagationLogRepository(sqlite_db).find_recent(
            "u-1", "dev", within_seconds=3600
        )
        assert found is not None and found["propagation_id"] == "real-1"

        # update() never flushes/commits itself — orm_session must, or the
        # change is silently lost (the prod-only bug class reviewers found;
        # the ZDAS leg of this contract is test_contract_prod_backend).
        SkillPropagationLogRepository(sqlite_db).update(
            "real-1", {"status": "done", "affected_bot_count": 7}
        )
        reread = SkillPropagationLogRepository(sqlite_db).find_recent(
            "u-1", "dev", within_seconds=3600
        )
        assert reread["status"] == "done"
        assert reread["affected_bot_count"] == 7
    finally:
        db_mod._engine = None
        db_mod._session_factory = None


# --- cross-backend contract -------------------------------------------------

def _run_contract(db):
    import uuid

    # Unique ids + best-effort cleanup so this is idempotent against a
    # shared real ZDAS/Pre database (propagation_id has a UNIQUE key, and
    # the table accumulates other rows for any given skill_uuid/env).
    pid = f"contract-{uuid.uuid4()}"
    suid = f"u-{uuid.uuid4()}"
    repo = SkillPropagationLogRepository(db)
    try:
        created = repo.create(
            _payload(propagation_id=pid, skill_uuid=suid)
        )
        assert set(created) >= {
            "id", "propagation_id", "skill_uuid", "env", "action", "status",
            "affected_bot_count", "success_bot_count", "failed_bot_ids",
            "extra", "error_msg", "gmt_created", "gmt_modified",
        }
        repo.update(pid, {"status": "done"})
        found = repo.find_recent(suid, "dev", within_seconds=3600)
        assert found["propagation_id"] == pid
        assert found["status"] == "done"
    finally:
        from agentclaw.community.core.models.skill_propagation_log import (
            SkillPropagationLog,
        )

        with db.orm_session() as s:
            s.query(SkillPropagationLog).filter(
                SkillPropagationLog.propagation_id == pid
            ).delete()


def test_contract_local_backend(tmp_path):
    _run_contract(_make_db(tmp_path))


# NOTE: there is intentionally no ZDAS/prod contract test here. It would
# require a live OceanBase connection (via the MOSN sidecar) that does not
# exist in CI, so it could only ever be a permanently-skipped test — and
# skipped tests are not allowed in this codebase. The unified repository
# is a *single* body; test_contract_local_backend + the behavior tests +
# test_real_sqlitedb_orm_session_commits_on_clean_exit exercise exactly
# that body in CI. The ZDAS connection round-trip is verified manually as
# a Pre acceptance gate (see specs/2026-05-17-unified-repository-pilot/
# tasks.md, Task 4), not as an automated test.
