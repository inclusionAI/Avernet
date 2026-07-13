"""Unified ExpertChatInstance repository — behavior + cross-backend contract.

Mirrors ``test_expert_chat_unified.py``: real ``SqliteDB.orm_session``
round-trip, no ZDAS-skipped test. Locks the caller-instance ledger
contracts: atomic upsert on ``uk_bi_oi_ui_e``, ``ext`` JSON
round-trip, blind partial ``update_instance`` (no-op when absent).
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.expert_chat_instance_repository import (
    ExpertChatInstanceRepository,
)

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """Private MetaData copy of AcExpertChatInstance — copies server_default
    (DB-side timestamps) and the uk_bi_oi_ui_e unique constraint (the
    upsert's conflict target)."""
    from agentclaw.community.core.expert_chat.sqlite_models import (
        AcExpertChatInstance,
    )

    src = AcExpertChatInstance.__table__
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
            "bot_id", "owner_id", "user_id", "env", name="uk_bi_oi_ui_e",
        ),
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
        f"sqlite:///{tmp_path / 'ec_instance.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return ExpertChatInstanceRepository(_make_db(tmp_path))


# ---------------------------------------------------------------------------
# get_instance / upsert_instance
# ---------------------------------------------------------------------------

def test_get_instance_absent_returns_none(repo):
    assert repo.get_instance("u1", "b1", "o1") is None


def test_upsert_instance_inserts_with_ext_json(repo):
    r = repo.upsert_instance(
        "u1", "b1", "o1", status="init",
        ext={"bot_uuid": "uuid-1", "service_bot_publish_id": 123},
    )
    assert r["id"] is not None
    assert r["status"] == "init"
    assert r["ext"] == {"bot_uuid": "uuid-1", "service_bot_publish_id": 123}

    got = repo.get_instance("u1", "b1", "o1")
    assert got["ext"] == {"bot_uuid": "uuid-1", "service_bot_publish_id": 123}


def test_upsert_instance_is_atomic_and_full_overwrite(repo):
    first = repo.upsert_instance(
        "u1", "b1", "o1", status="init", ext={"bot_uuid": "uuid-1"},
    )
    # second upsert on the same uk → same row, whole-overwrite ext+status.
    again = repo.upsert_instance(
        "u1", "b1", "o1", status="active",
        ext={"bot_uuid": "uuid-2", "binding_id": 9},
    )
    assert again["id"] == first["id"]
    assert again["status"] == "active"
    assert again["ext"] == {"bot_uuid": "uuid-2", "binding_id": 9}
    # exactly one row for the uk
    assert repo.get_instance("u1", "b1", "o1")["ext"] == {
        "bot_uuid": "uuid-2", "binding_id": 9,
    }


def test_upsert_instance_none_ext(repo):
    r = repo.upsert_instance("u1", "b1", "o1", status="init", ext=None)
    assert r["ext"] is None
    assert repo.get_instance("u1", "b1", "o1")["ext"] is None


# ---------------------------------------------------------------------------
# update_instance
# ---------------------------------------------------------------------------

def test_update_instance_status_only(repo):
    repo.upsert_instance("u1", "b1", "o1", status="init", ext={"bot_uuid": "u"})
    assert repo.update_instance("u1", "b1", "o1", status="active") is True
    got = repo.get_instance("u1", "b1", "o1")
    assert got["status"] == "active"
    # ext untouched
    assert got["ext"] == {"bot_uuid": "u"}


def test_update_instance_ext_whole_overwrite(repo):
    repo.upsert_instance(
        "u1", "b1", "o1", status="init",
        ext={"bot_uuid": "u", "service_bot_publish_id": 1},
    )
    # whole-overwrite, not merge
    repo.update_instance("u1", "b1", "o1", ext={"bot_uuid": "u", "binding_id": 7})
    got = repo.get_instance("u1", "b1", "o1")
    assert got["ext"] == {"bot_uuid": "u", "binding_id": 7}
    assert "service_bot_publish_id" not in got["ext"]


def test_update_instance_absent_is_noop(repo):
    # blind UPDATE — no row, no error, False.
    assert repo.update_instance("u1", "b1", "o1", status="active") is False
    assert repo.get_instance("u1", "b1", "o1") is None


def test_update_instance_neither_field_is_noop(repo):
    repo.upsert_instance("u1", "b1", "o1", status="init", ext={"k": "v"})
    # only gmt_modified bump; status/ext both None
    assert repo.update_instance("u1", "b1", "o1") is True
    got = repo.get_instance("u1", "b1", "o1")
    assert got["status"] == "init"
    assert got["ext"] == {"k": "v"}


# ---------------------------------------------------------------------------
# MySQL dialect coverage (lines 142, 144, 145, 151)
# ---------------------------------------------------------------------------

def test_upsert_instance_mysql_dialect_with_mock():
    """Test MySQL dialect path in upsert_instance (lines 142, 144, 145, 151).

    This test mocks the session to report MySQL dialect, covering the
    on_duplicate_key_update branch which uses mysql insert.
    """
    from unittest.mock import MagicMock, patch

    # Create a fully mocked session that simulates MySQL dialect
    mock_db = MagicMock()
    mock_session = MagicMock()

    # Mock the context manager
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    # Mock get_bind to return MySQL dialect
    mock_bind = MagicMock()
    mock_bind.dialect.name = "mysql"
    mock_session.get_bind.return_value = mock_bind

    # Mock execute to return a result with lastrowid for MySQL
    mock_result = MagicMock()
    mock_result.lastrowid = 42
    mock_session.execute.return_value = mock_result

    # Mock query for final row fetch
    mock_row = MagicMock()
    mock_row.to_dict.return_value = {
        "id": 42,
        "user_id": "u_mysql",
        "bot_id": "b_mysql",
        "owner_id": "o_mysql",
        "status": "active",
        "ext": {"mysql": "test"},
        "env": "test",
    }
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = mock_row
    mock_session.query.return_value = mock_query

    mock_db.orm_session.return_value = mock_session

    # Create repository with mocked DB
    repo = ExpertChatInstanceRepository(mock_db)

    # Execute upsert - this should go through the MySQL dialect branch
    result = repo.upsert_instance(
        "u_mysql", "b_mysql", "o_mysql",
        status="active",
        ext={"mysql": "test"}
    )

    # Verify the MySQL branch was taken (execute was called)
    assert mock_session.execute.called
    assert mock_session.query.called
    assert result["id"] == 42
    assert result["status"] == "active"


def test_update_instance_with_both_fields_mysql():
    """Test update_instance works correctly with mocked session."""
    # update_instance uses raw query, not dialect-specific
    # This test ensures update_instance works with mocked session
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    # Mock query for update
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.update.return_value = 1  # 1 row updated
    mock_session.query.return_value = mock_query

    mock_db.orm_session.return_value = mock_session

    repo = ExpertChatInstanceRepository(mock_db)
    result = repo.update_instance(
        "u1", "b1", "o1",
        status="success",
        ext={"new": "value"}
    )

    assert result is True
    mock_session.query.assert_called_once()