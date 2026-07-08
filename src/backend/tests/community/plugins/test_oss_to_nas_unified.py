"""Unified OssToNasRecord repository — behavior + cross-backend contract.

Locks round-3/session-1 criteria: a single repo body, full Protocol
surface parity with the prior prod twin, DB-side timestamps, single
atomic statements, the prod ``uk_staff_bot`` uniqueness enforced on
SQLite too, and write auto-save against the real
``SqliteDB.orm_session``. No ZDAS-skipped test — the prod round-trip
is a manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import (
    Column,
    MetaData,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.oss_to_nas_record_repository import (
    OssToNasRecordRepository,
)

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """Private MetaData copy of OssToNasRecord — copies server_default
    (DB-side timestamps) and BOTH unique constraints (uk_staff_bot_env
    and the prod-faithful uk_staff_bot)."""
    from agentclaw.community.plugin_api.models import OssToNasRecord

    src = OssToNasRecord.__table__
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
        UniqueConstraint(
            "staff_no", "bot_id", "env", name="uk_staff_bot_env"
        ),
        UniqueConstraint("staff_no", "bot_id", name="uk_staff_bot"),
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
        f"sqlite:///{tmp_path / 'oss.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.plugins.oss_to_nas_record_repository.get_current_env",
        lambda: "dev",
    )
    return OssToNasRecordRepository(_make_db(tmp_path))


def _insert(repo, **over):
    base = dict(
        staff_no="s1",
        bot_id="b1",
        env="dev",
        batch_no="batch01",
        sub_batch_no="sub01",
        bot_info={"name": "Bot One"},
        storage_status="oss",
    )
    base.update(over)
    return repo.insert_record(**base)


def test_insert_then_get_roundtrips(repo):
    created = _insert(repo)
    assert created["bot_info"] == {"name": "Bot One"}
    assert created["gmt_create"] is not None  # DB server_default
    assert created["gmt_modified"] is not None
    fetched = repo.get_record("s1", "b1", env="dev")
    assert fetched["id"] == created["id"]


def test_get_missing_returns_none(repo):
    assert repo.get_record("nope", "nope", env="dev") is None


def test_get_record_defaults_env_to_current(repo):
    _insert(repo, env="dev")
    assert repo.get_record("s1", "b1") is not None
    _insert(repo, staff_no="s2", bot_id="b2", env="prod")
    assert repo.get_record("s2", "b2") is None


def test_uk_staff_bot_is_enforced_prod_parity(repo):
    # Prod DDL's uk_staff_bot (staff_no, bot_id) is an *invisible* but
    # still-enforced UNIQUE; a second row for the same (staff_no, bot_id)
    # — even in a different env — must raise on SQLite too.
    _insert(repo, staff_no="dup", bot_id="dup", env="dev")
    with pytest.raises(IntegrityError):
        _insert(repo, staff_no="dup", bot_id="dup", env="prod")


def test_update_status_changes_only_target(repo):
    _insert(repo, staff_no="s1", bot_id="b1")
    _insert(repo, staff_no="s2", bot_id="b2")
    repo.update_status("s1", "b1", "nas", env="dev")
    assert repo.get_record("s1", "b1", env="dev")["storage_status"] == "nas"
    assert repo.get_record("s2", "b2", env="dev")["storage_status"] == "oss"


def test_update_status_missing_is_silent_noop(repo):
    repo.update_status("ghost", "ghost", "nas", env="dev")


def test_update_record_returns_updated_and_filters_unknown(repo):
    _insert(repo)
    updated = repo.update_record(
        "s1", "b1",
        {"storage_status": "nas", "not_a_column": "ignored"},
        env="dev",
    )
    assert updated["storage_status"] == "nas"


def test_update_record_no_valid_fields_raises(repo):
    _insert(repo)
    with pytest.raises(ValueError):
        repo.update_record("s1", "b1", {"not_a_column": "x"}, env="dev")


def test_update_record_bot_info_dict_is_jsonified(repo):
    _insert(repo)
    updated = repo.update_record(
        "s1", "b1", {"bot_info": {"k": "v2"}}, env="dev"
    )
    assert updated["bot_info"] == {"k": "v2"}


def test_query_records_by_batch_with_status_filter(repo):
    _insert(repo, staff_no="s1", bot_id="b1", storage_status="oss")
    _insert(repo, staff_no="s2", bot_id="b2", storage_status="nas")
    assert len(repo.query_records_by_batch("dev", "batch01", "sub01")) == 2
    only_nas = repo.query_records_by_batch(
        "dev", "batch01", "sub01", status_filter="nas"
    )
    assert [r["bot_id"] for r in only_nas] == ["b2"]


def test_batch_update_status_returns_count(repo):
    _insert(repo, staff_no="s1", bot_id="b1")
    _insert(repo, staff_no="s2", bot_id="b2")
    assert repo.batch_update_status("dev", "batch01", "sub01", "nas") == 2


def test_delete_existing_then_missing(repo):
    _insert(repo)
    assert repo.delete_record("s1", "b1", env="dev") is True
    assert repo.get_record("s1", "b1", env="dev") is None
    assert repo.delete_record("nope", "nope", env="dev") is False


def test_real_sqlitedb_orm_session_commits(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    monkeypatch.setattr(
        "agentclaw.community.plugins.oss_to_nas_record_repository.get_current_env",
        lambda: "dev",
    )
    db_mod = importlib.import_module("agentclaw.community.plugins.local.database")
    db_mod._engine = None
    db_mod._session_factory = None
    try:
        db_mod._get_session_factory()
        _create_schema(db_mod._engine)
        sqlite_db = db_mod.SqliteDB()
        OssToNasRecordRepository(sqlite_db).insert_record(
            staff_no="r", bot_id="r", env="dev",
            batch_no="x", sub_batch_no="y",
        )
        got = OssToNasRecordRepository(sqlite_db).get_record(
            "r", "r", env="dev"
        )
        assert got is not None and got["storage_status"] == "oss"
    finally:
        db_mod._engine = None
        db_mod._session_factory = None
