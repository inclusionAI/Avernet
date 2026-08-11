"""Unified ExpertChat repository — behavior + cross-backend contract.

Locks round-3/session-1 criteria: a single repo body, prod-twin
parity (atomic upsert add_chat_bot, blind-UPDATE save_session,
rowcount-based remove/delete), DB-side timestamps, write auto-save
against the real ``SqliteDB.orm_session``. No ZDAS-skipped test — the
prod round-trip is a manual Pre acceptance gate.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.chat.expert_chat import ExpertChatRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """Create private copies of both expert-chat session tables."""
    from agentclaw.community.core.expert_chat.sqlite_models import (
        AcExpertChatBotSession,
        AcExpertChatOwnedSession,
    )

    md = MetaData()
    for model, unique_columns, unique_name in (
        (
            AcExpertChatBotSession,
            ("user_id", "bot_id", "owner_id", "env"),
            "uk_user_bot_owner_env",
        ),
        (
            AcExpertChatOwnedSession,
            ("user_id", "bot_id", "owner_id", "env", "session_key"),
            "uk_user_bot_owner_env_session",
        ),
    ):
        src = model.__table__
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
            UniqueConstraint(*unique_columns, name=unique_name),
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
        f"sqlite:///{tmp_path / 'ec.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return ExpertChatRepository(_make_db(tmp_path))


def test_add_chat_bot_inserts(repo):
    r = repo.add_chat_bot("u1", "b1", "o1")
    assert r["id"] is not None
    assert r["status"] == "ACTIVE"
    assert r["user_id"] == "u1"


def test_add_chat_bot_is_atomic_upsert(repo):
    first = repo.add_chat_bot("u1", "b1", "o1")
    # second call on the same uk must NOT create a new row and must
    # keep/reset status ACTIVE (prod ON DUPLICATE KEY UPDATE parity).
    again = repo.add_chat_bot("u1", "b1", "o1")
    assert again["id"] == first["id"]
    assert again["status"] == "ACTIVE"
    assert len(repo.list_chat_bots("u1")) == 1


def test_add_chat_bot_reactivates_removed(repo):
    rid = repo.add_chat_bot("u1", "b1", "o1")["id"]
    assert repo.remove_chat_bot("u1", "b1", "o1") is True
    assert repo.list_chat_bots("u1") == []
    re = repo.add_chat_bot("u1", "b1", "o1")
    assert re["id"] == rid
    assert [x["bot_id"] for x in repo.list_chat_bots("u1")] == ["b1"]


def test_remove_chat_bot_rowcount(repo):
    repo.add_chat_bot("u1", "b1", "o1")
    assert repo.remove_chat_bot("u1", "b1", "o1") is True
    # second remove still matches the row (rowcount>0, prod parity:
    # CLIENT_FOUND_ROWS / SQLite matched-rows) — re-running is True.
    assert repo.remove_chat_bot("u1", "b1", "o1") is True
    assert repo.remove_chat_bot("u1", "nope", "o1") is False


def test_list_chat_bots_active_only_ordered(repo):
    repo.add_chat_bot("u1", "b1", "o1")
    repo.add_chat_bot("u1", "b2", "o1")
    repo.remove_chat_bot("u1", "b1", "o1")
    rows = repo.list_chat_bots("u1")
    assert [r["bot_id"] for r in rows] == ["b2"]


def test_get_and_save_session(repo):
    repo.add_chat_bot("u1", "b1", "o1")
    assert repo.get_session("u1", "b1", "o1") is None
    repo.save_session("u1", "b1", "o1", "session:abc")
    assert repo.get_session("u1", "b1", "o1") == "session:abc"


def test_save_session_blind_noop_when_absent(repo):
    # Prod twin: blind UPDATE, no row created, no error, returns None.
    assert repo.save_session("u1", "b1", "o1", "session:x") is None
    assert repo.get_session("u1", "b1", "o1") is None
    assert repo.list_chat_bots("u1") == []


def test_delete_session_rowcount(repo):
    repo.add_chat_bot("u1", "b1", "o1")
    repo.save_session("u1", "b1", "o1", "session:abc")
    assert repo.delete_session("u1", "b1", "o1") is True
    assert repo.get_session("u1", "b1", "o1") is None
    assert repo.delete_session("u1", "missing", "o1") is False


def test_owned_sessions_are_isolated_and_searchable(repo):
    first = repo.add_owned_session("u1", "b1", "o1", "session:first")
    second = repo.add_owned_session("u1", "b1", "o1", "session:second")
    repo.add_owned_session("u2", "b1", "o1", "session:other-user")

    assert first["id"] != second["id"]
    assert [
        row["session_key"] for row in repo.list_owned_sessions("u1", "b1", "o1")
    ] == ["session:second", "session:first"]
    assert (
        repo.get_owned_session("u1", "b1", "o1", "session:second")["session_key"]
        == "session:second"
    )
    assert repo.get_owned_session("u1", "b1", "o1", "session:other-user") is None


def test_owned_session_upsert_reactivates_soft_deleted_row(repo):
    original = repo.add_owned_session("u1", "b1", "o1", "session:one")
    assert repo.delete_owned_session("u1", "b1", "o1", "session:one") is True
    assert repo.list_owned_sessions("u1", "b1", "o1") == []

    restored = repo.add_owned_session("u1", "b1", "o1", "session:one")
    assert restored["id"] == original["id"]
    assert restored["status"] == "ACTIVE"


def test_owned_session_uses_mysql_atomic_upsert_contract():
    db_session = MagicMock()
    db_session.get_bind.return_value.dialect = mysql.dialect()
    db_session.execute.return_value.lastrowid = 42
    stored_row = MagicMock()
    stored_row.to_dict.return_value = {
        "id": 42,
        "session_key": "session:one",
        "status": "ACTIVE",
    }
    db_session.get.return_value = stored_row

    class _MysqlDB:
        @contextmanager
        def orm_session(self):
            yield db_session

    repository = ExpertChatRepository(_MysqlDB())

    result = repository.add_owned_session("u1", "b1", "o1", "session:one")

    statement = db_session.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=db_session.get_bind().dialect))
    assert "ON DUPLICATE KEY UPDATE" in compiled
    db_session.get.assert_called_once_with(repository.OwnedSessionModel, 42)
    assert result == stored_row.to_dict.return_value


def test_delete_all_owned_sessions_only_affects_requested_bot(repo):
    repo.add_owned_session("u1", "b1", "o1", "session:one")
    repo.add_owned_session("u1", "b1", "o1", "session:two")
    repo.add_owned_session("u1", "b2", "o1", "session:three")

    assert repo.delete_all_owned_sessions("u1", "b1", "o1") == 2
    assert repo.list_owned_sessions("u1", "b1", "o1") == []
    assert len(repo.list_owned_sessions("u1", "b2", "o1")) == 1


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
        ExpertChatRepository(sqlite_db).add_chat_bot("real", "b", "o")
        got = ExpertChatRepository(sqlite_db).list_chat_bots("real")
        assert got == [{"bot_id": "b", "owner_id": "o"}]
    finally:
        db_mod._engine = None
        db_mod._session_factory = None
