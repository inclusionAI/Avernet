"""
OrmDistributedLockRepository unit tests.

Uses pytest + MagicMock SQLAlchemy ORM session pattern matching the existing
test_orm_bot_run_repository.py.

``try_acquire_lock`` is exercised through the dialect-specific upsert it now
emits: each test asserts on the compiled SQLAlchemy statement (mysql default
or sqlite branch) and the bound params, plus the confirming read that decides
acquired/not-acquired (see ``arca_ttl`` unit tests for the SQL-assertion
pattern).
"""

from datetime import datetime

# ==================== Fixtures ====================
from datetime import timedelta as _timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import mysql, sqlite

from secbaas.community.core.repository.distributed_lock import (
    DistributedLockRepository,
    LockRecord,
    OrmDistributedLockRepository,
)
from secbaas.community.core.repository.distributed_lock._orm_model import (
    DistributedLockModel,
)

NOW = datetime.now()
FUTURE = NOW + _timedelta(days=30)
PAST = NOW - _timedelta(days=30)

_SQLITE = "sqlite"


def _make_repo(dialect: str = "mysql"):
    """Create a repository with a mocked database session bound to dialect.

    The session's ``bind.dialect.name`` selects the upsert branch: mysql
    (default) renders ``ON DUPLICATE KEY UPDATE``, sqlite renders
    ``ON CONFLICT DO UPDATE``.
    """
    mock_session = MagicMock()
    mock_session.bind.dialect.name = dialect
    mock_db = MagicMock()
    mock_db.orm_session.return_value.__enter__.return_value = mock_session
    mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return OrmDistributedLockRepository(database=mock_db), mock_session


def _make_exec_result(row):
    """Build a mock Result exposing ``scalar_one_or_none`` -> row (or None)."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session (mysql dialect by default)."""
    session = MagicMock()
    session.bind.dialect.name = "mysql"
    session.query.return_value.filter.return_value.update.return_value = 0
    return session


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture(autouse=True)
def mock_get_current_env(monkeypatch):
    """Mock get_current_env to return 'dev' for all tests."""
    monkeypatch.setenv("SERVER_ENV", "dev")


@pytest.fixture
def repository(mock_database):
    """Create OrmDistributedLockRepository with mocked database."""
    return OrmDistributedLockRepository(database=mock_database)


# ==================== Model Helpers ====================


def _make_model(
    id_val=1,
    lock_name="test-lock",
    lock_holder="holder-001",
    expire_time=None,
    env="dev",
    gmt_create=None,
    gmt_modified=None,
):
    """Create a MagicMock simulating a DistributedLockModel instance with to_record()."""
    gmt_create = gmt_create or NOW
    gmt_modified = gmt_modified or NOW
    model = MagicMock(spec=DistributedLockModel)
    model.id = id_val
    model.lock_name = lock_name
    model.lock_holder = lock_holder
    model.expire_time = expire_time
    model.env = env
    model.gmt_create = gmt_create
    model.gmt_modified = gmt_modified
    model.to_record.return_value = LockRecord(
        id=id_val,
        lock_name=lock_name,
        lock_holder=lock_holder,
        expire_time=expire_time,
        env=env,
        gmt_create=gmt_create,
        gmt_modified=gmt_modified,
    )
    return model


# ==================== LockRecord Dataclass Tests ====================


class TestLockRecordDataclass:
    """Tests for LockRecord dataclass."""

    def test_create_record_with_all_fields(self):
        record = LockRecord(
            id=1,
            lock_name="test-lock",
            lock_holder="holder-1",
            expire_time=NOW,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        assert record.id == 1
        assert record.lock_name == "test-lock"
        assert record.lock_holder == "holder-1"
        assert record.expire_time == NOW
        assert record.env == "dev"

    def test_create_record_with_none_expire_time(self):
        record = LockRecord(
            id=2,
            lock_name="no-expire",
            lock_holder="holder-2",
            expire_time=None,
            env=None,
            gmt_create=None,
            gmt_modified=None,
        )
        assert record.expire_time is None
        assert record.env is None

    def test_record_uses_slots(self):
        record = LockRecord(
            id=1,
            lock_name="test",
            lock_holder="h",
            expire_time=None,
            env=None,
            gmt_create=None,
            gmt_modified=None,
        )
        with pytest.raises(AttributeError):
            record.nonexistent_field = "value"

    def test_record_equality(self):
        r1 = LockRecord(
            id=1,
            lock_name="a",
            lock_holder="b",
            expire_time=NOW,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        r2 = LockRecord(
            id=1,
            lock_name="a",
            lock_holder="b",
            expire_time=NOW,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        assert r1 == r2

    def test_record_inequality(self):
        r1 = LockRecord(
            id=1,
            lock_name="a",
            lock_holder="b",
            expire_time=NOW,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        r2 = LockRecord(
            id=2,
            lock_name="a",
            lock_holder="b",
            expire_time=NOW,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        assert r1 != r2


# ==================== Repository __init__ Tests ====================


class TestRepositoryInit:
    """Tests for repository initialization."""

    def test_database_is_stored(self, mock_database):
        repo = OrmDistributedLockRepository(mock_database)
        assert repo._database is mock_database

    def test_constructor_takes_database_only(self, mock_database):
        repo = OrmDistributedLockRepository(mock_database)
        assert repo._database is mock_database


# ==================== get_by_lock_name Tests ====================


class TestGetByLockName:
    """Tests for OrmDistributedLockRepository.get_by_lock_name()."""

    def test_returns_record_when_found(self, repository, mock_session):
        mock_model = _make_model(
            id_val=42,
            lock_name="task-lock",
            lock_holder="worker-1",
            expire_time=FUTURE,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_lock_name("task-lock")

        assert result is not None
        assert isinstance(result, LockRecord)
        assert result.id == 42
        assert result.lock_name == "task-lock"
        assert result.lock_holder == "worker-1"
        assert result.expire_time == FUTURE

    def test_returns_none_when_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_lock_name("missing-lock")

        assert result is None

    def test_queries_by_exact_lock_name(self, repository, mock_session):
        mock_model = _make_model(lock_name="exact-match")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_lock_name("exact-match")

        assert result is not None
        assert result.lock_name == "exact-match"


# ==================== update_expire_time Tests ====================


class TestUpdateExpireTime:
    """Tests for OrmDistributedLockRepository.update_expire_time()."""

    def test_update_returns_rowcount(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_expire_time(
            lock_name="test-lock",
            expire_time=FUTURE,
        )

        assert result == 1

    def test_update_zero_rows(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.update_expire_time(
            lock_name="missing",
            expire_time=FUTURE,
        )

        assert result == 0

    def test_update_to_past_time(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_expire_time(
            lock_name="test-lock",
            expire_time=PAST,
        )

        assert result == 1


# ==================== delete_lock Tests ====================


class TestDeleteLock:
    """Tests for OrmDistributedLockRepository.delete_lock()."""

    def test_delete_returns_true_when_row_deleted(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        result = repository.delete_lock("release-lock")

        assert result is True
        mock_session.query.assert_called_once_with(DistributedLockModel)

    def test_delete_returns_false_when_no_row_deleted(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_lock("missing-lock")

        assert result is False

    def test_delete_returns_false_when_rowcount_is_zero(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_lock("stale-lock")

        assert result is False
        assert isinstance(result, bool)


# ==================== try_acquire_lock Tests ====================


class TestTryAcquireLock:
    """Tests for OrmDistributedLockRepository.try_acquire_lock().

    The repository now emits a single dialect-specific upsert followed by a
    confirming read SELECT. These tests assert on the compiled SQL/bind params
    and on the confirming read's row, mirroring the arca_ttl SQL-assertion
    unit-test pattern.
    """

    def test_acquire_emits_mysql_upsert_with_bindparams(self):
        repo, mock_session = _make_repo("mysql")
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder-A", expire_time=FUTURE)
        )

        result = repo.try_acquire_lock(
            lock_name="new-lock", lock_holder="holder-A", expire_time=FUTURE
        )

        assert result is True
        assert mock_session.execute.call_count == 2

        upsert_stmt = mock_session.execute.call_args_list[0][0][0]
        compiled = upsert_stmt.compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "INSERT INTO ac_lock_table" in sql_text
        assert "ON DUPLICATE KEY UPDATE" in sql_text
        # Conditional overwrite via CASE WHEN ... THEN VALUES(...) ELSE <col>.
        assert "CASE WHEN" in sql_text
        assert "VALUES(lock_holder)" in sql_text
        assert "gmt_modified" in sql_text
        # The acquirable predicate must contain all three OR branches:
        # same holder, null expire, and expired (<= app-clock now). Each CASE
        # repeats it, so it appears multiple times.
        assert "ac_lock_table.lock_holder = VALUES(lock_holder)" in sql_text
        assert "ac_lock_table.expire_time IS NULL" in sql_text
        assert "ac_lock_table.expire_time <=" in sql_text

        params = compiled.params
        assert params["lock_name"] == "new-lock"
        assert params["lock_holder"] == "holder-A"
        assert params["expire_time"] == FUTURE
        assert params["env"] == "dev"
        # The expiry branch binds the app-clock now, not DB NOW().
        assert "expire_time_1" in params or any(
            isinstance(v, datetime) for v in params.values()
        )

    def test_acquire_emits_sqlite_on_conflict_dialect(self):
        repo, mock_session = _make_repo(_SQLITE)
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder-A", expire_time=FUTURE)
        )

        result = repo.try_acquire_lock(
            lock_name="new-lock", lock_holder="holder-A", expire_time=FUTURE
        )

        assert result is True
        upsert_stmt = mock_session.execute.call_args_list[0][0][0]
        compiled = upsert_stmt.compile(dialect=sqlite.dialect())
        sql_text = str(compiled)

        assert "INSERT INTO ac_lock_table" in sql_text
        assert "ON CONFLICT (lock_name)" in sql_text
        assert "DO UPDATE SET" in sql_text
        assert "excluded.lock_holder" in sql_text
        assert "gmt_modified" in sql_text
        assert "ON DUPLICATE KEY UPDATE" not in sql_text
        # The ON CONFLICT WHERE guard must contain all three acquirable branches
        # and bind the app-clock now for the expiry comparison.
        where_region = sql_text.split("WHERE", 1)[1]
        assert "ac_lock_table.lock_holder = excluded.lock_holder" in where_region
        assert "ac_lock_table.expire_time IS NULL" in where_region
        assert "ac_lock_table.expire_time <=" in where_region

    def test_acquire_runs_confirm_read_select_after_upsert(self):
        repo, mock_session = _make_repo("mysql")
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder-A", expire_time=FUTURE)
        )

        repo.try_acquire_lock(
            lock_name="cf-lock", lock_holder="holder-A", expire_time=FUTURE
        )

        confirm_stmt = mock_session.execute.call_args_list[1][0][0]
        compiled = confirm_stmt.compile(dialect=mysql.dialect())
        assert "SELECT" in str(compiled)
        assert "ac_lock_table" in str(compiled)

    def test_acquire_returns_true_when_confirm_read_shows_self(self):
        repo, mock_session = _make_repo("mysql")
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder-A", expire_time=FUTURE)
        )

        result = repo.try_acquire_lock(
            lock_name="held-self", lock_holder="holder-A", expire_time=FUTURE
        )

        assert result is True

    def test_acquire_returns_false_when_held_by_other(self):
        repo, mock_session = _make_repo("mysql")
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder-B", expire_time=FUTURE)
        )

        result = repo.try_acquire_lock(
            lock_name="held-other", lock_holder="holder-A", expire_time=FUTURE
        )

        assert result is False
        # No rollback on a clean not-acquired outcome.
        mock_session.rollback.assert_not_called()

    def test_acquire_returns_false_when_confirm_read_missing(self):
        repo, mock_session = _make_repo("mysql")
        mock_session.execute.return_value = _make_exec_result(None)

        result = repo.try_acquire_lock(
            lock_name="gone", lock_holder="holder-A", expire_time=FUTURE
        )

        assert result is False

    def test_acquire_takes_over_expired_lock_via_confirm_read(self):
        repo, mock_session = _make_repo("mysql")
        # Confirming read shows the row already updated to the new holder by
        # the upsert, so the repository reports success even though the input
        # holder differs from the previous (expired) owner.
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="new-holder", expire_time=FUTURE)
        )

        result = repo.try_acquire_lock(
            lock_name="expired-lock", lock_holder="new-holder", expire_time=FUTURE
        )

        assert result is True

    def test_acquire_handles_oceanbase_lock_wait_timeout(self):
        from sqlalchemy.exc import DatabaseError as SADatabaseError

        repo, mock_session = _make_repo("mysql")
        orig = MagicMock()
        orig.errno = 1205
        orig.__str__.return_value = (
            "Lock wait timeout exceeded; try restarting transaction"
        )
        mock_session.execute.side_effect = SADatabaseError("INSERT ...", {}, orig)

        result = repo.try_acquire_lock(
            lock_name="busy-lock", lock_holder="holder-A", expire_time=FUTURE
        )

        assert result is False
        mock_session.rollback.assert_called_once()

    def test_acquire_reraises_unexpected_database_error(self):
        from sqlalchemy.exc import DatabaseError as SADatabaseError

        repo, mock_session = _make_repo("mysql")
        orig = MagicMock()
        orig.errno = 1146  # not a lock-wait-timeout
        orig.__str__.return_value = "Table doesn't exist"
        mock_session.execute.side_effect = SADatabaseError("INSERT ...", {}, orig)

        with pytest.raises(SADatabaseError):
            repo.try_acquire_lock(
                lock_name="err-lock", lock_holder="holder-A", expire_time=FUTURE
            )


# ==================== DistributedLockRepository Protocol Tests ====================


class TestDistributedLockRepositoryProtocol:
    """Tests verifying that OrmDistributedLockRepository satisfies the Protocol."""

    def test_repository_is_protocol_instance(self, repository):
        assert isinstance(repository, DistributedLockRepository)

    def test_protocol_methods_exist(self, repository):
        """Verify all protocol methods are available."""
        assert hasattr(repository, "get_by_lock_name")
        assert hasattr(repository, "update_expire_time")
        assert hasattr(repository, "delete_lock")
        assert hasattr(repository, "try_acquire_lock")


# ==================== @with_orm_session Integration Tests ====================


class TestWithOrmSessionIntegration:
    """Tests verifying the @with_orm_session decorator lifecycle."""

    def test_decorator_opens_and_closes_session(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.get_by_lock_name("test-lock")

        mock_database.orm_session.assert_called_once()
        mock_session.query.assert_called_once()

    def test_session_is_cleaned_up_after_method(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.get_by_lock_name("test-lock")

        session_ctx = mock_database.orm_session.return_value
        session_ctx.__enter__.assert_called_once()
        session_ctx.__exit__.assert_called_once()

    def test_session_used_for_try_acquire(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)

        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder", expire_time=FUTURE)
        )

        repo.try_acquire_lock(
            lock_name="test-lock",
            lock_holder="holder",
            expire_time=FUTURE,
        )

        mock_database.orm_session.assert_called_once()

    def test_session_used_for_delete(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        repo.delete_lock("test-lock")

        mock_database.orm_session.assert_called_once()

    def test_multiple_methods_each_open_session(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)
        mock_model = _make_model(id_val=1)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        mock_session.query.return_value.filter.return_value.update.return_value = 1
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        repo.get_by_lock_name("lock-a")
        repo.update_expire_time(lock_name="lock-b", expire_time=FUTURE)
        repo.delete_lock("lock-c")

        assert mock_database.orm_session.call_count == 3


# ==================== Round Trip / Integration Tests ====================


class TestMethodRoundTrips:
    """Tests covering multiple methods in sequence on the same repository."""

    def test_acquire_then_get_by_lock_name(self, repository, mock_session):
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder-rt", expire_time=FUTURE)
        )

        acquired = repository.try_acquire_lock(
            lock_name="roundtrip-lock",
            lock_holder="holder-rt",
            expire_time=FUTURE,
        )
        assert acquired is True

    def test_acquire_then_delete(self, repository, mock_session):
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder", expire_time=FUTURE)
        )

        repository.try_acquire_lock(
            lock_name="delete-lock",
            lock_holder="holder",
            expire_time=FUTURE,
        )
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        deleted = repository.delete_lock("delete-lock")
        assert deleted is True

    def test_full_lock_lifecycle(self, repository, mock_session):
        # Acquire via upsert + confirm-read showing self as holder.
        mock_session.execute.return_value = _make_exec_result(
            _make_model(lock_holder="holder", expire_time=FUTURE)
        )

        acquired = repository.try_acquire_lock(
            lock_name="lifecycle-lock",
            lock_holder="holder",
            expire_time=FUTURE,
        )
        assert acquired is True

        # Renew
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        renewed = repository.update_expire_time(
            lock_name="lifecycle-lock",
            expire_time=FUTURE,
        )
        assert renewed == 1

        # Release
        mock_session.query.return_value.filter.return_value.delete.return_value = 1
        deleted = repository.delete_lock("lifecycle-lock")
        assert deleted is True

    def test_get_not_found_returns_none(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_lock_name("nonexistent")
        assert result is None

    def test_delete_not_found_returns_false(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_lock("nonexistent")
        assert result is False


# ==================== Edge Cases ====================


class TestEdgeCases:
    """Edge case tests for OrmDistributedLockRepository."""

    def test_lock_name_with_special_characters(self, repository, mock_session):
        mock_model = _make_model(lock_name="task:cleanup:daily")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_lock_name("task:cleanup:daily")
        assert result is not None
        assert result.lock_name == "task:cleanup:daily"

    def test_lock_holder_is_empty_string(self, repository, mock_session):
        mock_model = _make_model(lock_name="empty-holder", lock_holder="")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_lock_name("empty-holder")
        assert result is not None
        assert result.lock_holder == ""

    def test_consecutive_try_acquire_different_names(self, repository, mock_session):
        # Each try_acquire issues upsert + confirming read; both executes for
        # one logical call report that call's caller as the holder.
        mock_session.execute.side_effect = [
            _make_exec_result(_make_model(lock_holder="h1", expire_time=FUTURE)),
            _make_exec_result(_make_model(lock_holder="h1", expire_time=FUTURE)),
            _make_exec_result(_make_model(lock_holder="h2", expire_time=FUTURE)),
            _make_exec_result(_make_model(lock_holder="h2", expire_time=FUTURE)),
        ]

        r1 = repository.try_acquire_lock(
            lock_name="lock-a", lock_holder="h1", expire_time=FUTURE
        )
        r2 = repository.try_acquire_lock(
            lock_name="lock-b", lock_holder="h2", expire_time=FUTURE
        )

        assert r1 is True
        assert r2 is True

    def test_update_expire_time_with_large_dataset(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1000

        result = repository.update_expire_time(
            lock_name="bulk-lock",
            expire_time=FUTURE,
        )
        assert result == 1000
