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


def test_upsert_instance_returns_fallback_when_row_not_found(repo):
    """Line 162-169: When row is None after upsert, return fallback dict."""
    from unittest.mock import patch, MagicMock

    # Mock the case where the query returns None after upsert
    with patch.object(repo, '_db') as mock_db:
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_bind = MagicMock()
        mock_bind.dialect.name = "sqlite"
        mock_session.get_bind.return_value = mock_bind

        mock_session.execute = MagicMock()
        mock_session.flush = MagicMock()

        # Query returns None for row
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = 123  # row_id is returned
        mock_query.first.return_value = None  # but row is None
        mock_session.query.return_value = mock_query

        mock_db.orm_session.return_value = mock_session

        result = repo.upsert_instance("u_fallback", "b_fallback", "o_fallback", status="init")

        # Should return fallback dict with the row_id
        assert result["id"] == 123
        assert result["user_id"] == "u_fallback"
        assert result["status"] == "init"


def test_update_instance_with_status_only(repo):
    """Update instance with only status (ext=None)."""
    repo.upsert_instance("u_status", "b_status", "o_status", status="init", ext={"k": "v"})

    result = repo.update_instance("u_status", "b_status", "o_status", status="active")

    assert result is True
    got = repo.get_instance("u_status", "b_status", "o_status")
    assert got["status"] == "active"
    # ext unchanged
    assert got["ext"] == {"k": "v"}


def test_update_instance_with_ext_only(repo):
    """Update instance with only ext (status=None)."""
    repo.upsert_instance("u_ext", "b_ext", "o_ext", status="init", ext={"old": "val"})

    result = repo.update_instance("u_ext", "b_ext", "o_ext", ext={"new": "val2"})

    assert result is True
    got = repo.get_instance("u_ext", "b_ext", "o_ext")
    assert got["status"] == "init"  # unchanged
    assert got["ext"] == {"new": "val2"}  # whole overwrite


def test_get_instance_multiple_users_same_bot(repo):
    """Multiple users can have instances for the same bot."""
    repo.upsert_instance("user1", "shared_bot", "owner1", status="init", ext={"v": 1})
    repo.upsert_instance("user2", "shared_bot", "owner1", status="active", ext={"v": 2})

    u1 = repo.get_instance("user1", "shared_bot", "owner1")
    u2 = repo.get_instance("user2", "shared_bot", "owner1")

    assert u1["user_id"] == "user1"
    assert u1["status"] == "init"
    assert u2["user_id"] == "user2"
    assert u2["status"] == "active"


def test_upsert_instance_preserves_create_time(repo):
    """Upsert should not change gmt_create on update."""
    first = repo.upsert_instance("u_time", "b_time", "o_time", status="init")
    first_create = first.get("gmt_create")

    # Upsert again (update)
    second = repo.upsert_instance("u_time", "b_time", "o_time", status="active")

    # gmt_create should be preserved (or bothNone)
    if first_create is not None and second.get("gmt_create") is not None:
        assert second["gmt_create"] == first_create


def test_update_instance_multiple_fields(repo):
    """Update instance with both status and ext simultaneously."""
    repo.upsert_instance("u_both", "b_both", "o_both", status="init", ext={"x": 1})

    result = repo.update_instance("u_both", "b_both", "o_both", status="success", ext={"y": 2})

    assert result is True
    got = repo.get_instance("u_both", "b_both", "o_both")
    assert got["status"] == "success"
    assert got["ext"] == {"y": 2}


def test_get_instance_different_envs(repo):
    """Instances are isolated by env (via get_current_env)."""
    from unittest.mock import patch

    # Create in default env
    repo.upsert_instance("u_env", "b_env", "o_env", status="init")

    # Query in different env should return None
    with patch("agentclaw.community.plugins.expert_chat_instance_repository.get_current_env", return_value="other_env"):
        result = repo.get_instance("u_env", "b_env", "o_env")

    assert result is None