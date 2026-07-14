"""
OrmDistributedLockRepository unit tests.

Uses pytest + MagicMock SQLAlchemy ORM session pattern matching the existing
test_orm_bot_run_repository.py.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.distributed_lock import (
    DistributedLockRepository,
    LockRecord,
    OrmDistributedLockRepository,
)
from secbaas.community.core.repository.distributed_lock._orm_model import (
    DistributedLockModel,
)

# ==================== Fixtures ====================

NOW = datetime(2026, 5, 23, 12, 0, 0)
FUTURE = datetime(2026, 6, 1, 12, 0, 0)
PAST = datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    session = MagicMock()
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
            lock_name="my-lock",
            lock_holder="holder-abc",
            expire_time=FUTURE,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        assert record.id == 1
        assert record.lock_name == "my-lock"
        assert record.lock_holder == "holder-abc"
        assert record.expire_time == FUTURE
        assert record.env == "dev"
        assert record.gmt_create == NOW
        assert record.gmt_modified == NOW

    def test_create_record_with_none_expire_time(self):
        record = LockRecord(
            id=2,
            lock_name="lock2",
            lock_holder="holder2",
            expire_time=None,
            env=None,
            gmt_create=None,
            gmt_modified=None,
        )
        assert record.id == 2
        assert record.lock_name == "lock2"
        assert record.expire_time is None
        assert record.env is None
        assert record.gmt_create is None
        assert record.gmt_modified is None

    def test_record_uses_slots(self):
        record = LockRecord(
            id=1,
            lock_name="l",
            lock_holder="h",
            expire_time=None,
            env="e",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        with pytest.raises(AttributeError):
            _ = record.__dict__

    def test_record_equality(self):
        r1 = LockRecord(
            id=1,
            lock_name="a",
            lock_holder="h",
            expire_time=None,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        r2 = LockRecord(
            id=1,
            lock_name="a",
            lock_holder="h",
            expire_time=None,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        assert r1 == r2

    def test_record_inequality(self):
        r1 = LockRecord(
            id=1,
            lock_name="a",
            lock_holder="h",
            expire_time=None,
            env="dev",
            gmt_create=NOW,
            gmt_modified=NOW,
        )
        r2 = LockRecord(
            id=2,
            lock_name="a",
            lock_holder="h",
            expire_time=None,
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


# ==================== get_by_lock_name_for_update Tests ====================


class TestGetByLockNameForUpdate:
    """Tests for OrmDistributedLockRepository.get_by_lock_name_for_update()."""

    def test_returns_record_when_found(self, repository, mock_session):
        mock_model = _make_model(
            id_val=42,
            lock_name="task-lock",
            lock_holder="worker-1",
            expire_time=FUTURE,
        )
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        result = repository.get_by_lock_name_for_update("task-lock")

        assert result is not None
        assert isinstance(result, LockRecord)
        assert result.id == 42
        assert result.lock_name == "task-lock"
        assert result.lock_holder == "worker-1"
        assert result.expire_time == FUTURE
        mock_model.to_record.assert_called_once()
        mock_session.query.assert_called_once_with(DistributedLockModel)

    def test_returns_none_when_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None

        result = repository.get_by_lock_name_for_update("missing-lock")

        assert result is None

    def test_queries_by_exact_lock_name(self, repository, mock_session):
        mock_model = _make_model(lock_name="exact-match")
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        result = repository.get_by_lock_name_for_update("exact-match")

        assert result is not None
        assert result.lock_name == "exact-match"

    def test_handles_model_with_none_expire_and_env(self, repository, mock_session):
        mock_model = _make_model(
            id_val=1,
            lock_name="no-expire",
            expire_time=None,
            env=None,
        )
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        result = repository.get_by_lock_name_for_update("no-expire")

        assert result is not None
        assert result.expire_time is None
        assert result.env is None


# ==================== insert_lock Tests ====================


class TestInsertLock:
    """Tests for OrmDistributedLockRepository.insert_lock()."""

    def test_insert_returns_model_id(self, repository, mock_session):
        # Simulate auto-increment: mock the added model's id
        def _add_side_effect(model):
            model.id = 100

        mock_session.add.side_effect = _add_side_effect

        result = repository.insert_lock(
            lock_name="new-lock",
            lock_holder="holder-xyz",
            expire_time=FUTURE,
        )

        assert result == 100
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_has_env_field(self, repository, mock_session):
        def _add_side_effect(model):
            model.id = 77

        mock_session.add.side_effect = _add_side_effect

        result = repository.insert_lock(
            lock_name="env-lock",
            lock_holder="holder-env",
            expire_time=FUTURE,
        )

        assert result == 77
        added_model = mock_session.add.call_args[0][0]
        assert added_model.lock_name == "env-lock"
        assert added_model.lock_holder == "holder-env"
        assert added_model.expire_time == FUTURE
        assert added_model.env == "dev"

    def test_insert_with_past_expire_time(self, repository, mock_session):
        def _add_side_effect(model):
            model.id = 3

        mock_session.add.side_effect = _add_side_effect

        result = repository.insert_lock(
            lock_name="expired-lock",
            lock_holder="holder-1",
            expire_time=PAST,
        )

        assert result == 3
        added_model = mock_session.add.call_args[0][0]
        assert added_model.expire_time == PAST

    def test_insert_id_is_cast_to_int(self, repository, mock_session):
        def _add_side_effect(model):
            model.id = 42

        mock_session.add.side_effect = _add_side_effect

        result = repository.insert_lock(
            lock_name="test-name",
            lock_holder="test-holder",
            expire_time=FUTURE,
        )

        assert isinstance(result, int)
        assert result == 42


# ==================== update_lock_holder Tests ====================


class TestUpdateLockHolder:
    """Tests for OrmDistributedLockRepository.update_lock_holder()."""

    def test_update_returns_rowcount(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_lock_holder(
            lock_name="active-lock",
            lock_holder="new-holder",
            expire_time=FUTURE,
        )

        assert result == 1
        mock_session.query.assert_called_once_with(DistributedLockModel)
        mock_session.query.return_value.filter.return_value.update.assert_called_once()

    def test_update_zero_rows_affected(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.update_lock_holder(
            lock_name="nonexistent-lock",
            lock_holder="some-holder",
            expire_time=FUTURE,
        )

        assert result == 0

    def test_update_multiple_rows(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 3

        result = repository.update_lock_holder(
            lock_name="multi-lock",
            lock_holder="batch-holder",
            expire_time=FUTURE,
        )

        assert result == 3

    def test_update_rowcount_is_cast_to_int(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_lock_holder(
            lock_name="test-lock",
            lock_holder="test-holder",
            expire_time=FUTURE,
        )

        assert isinstance(result, int)
        assert result == 1


# ==================== update_expire_time Tests ====================


class TestUpdateExpireTime:
    """Tests for OrmDistributedLockRepository.update_expire_time()."""

    def test_update_returns_rowcount(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_expire_time(
            lock_name="renew-lock",
            expire_time=FUTURE,
        )

        assert result == 1
        mock_session.query.assert_called_once_with(DistributedLockModel)
        mock_session.query.return_value.filter.return_value.update.assert_called_once()

    def test_update_zero_rows(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.update_expire_time(
            lock_name="missing-lock",
            expire_time=FUTURE,
        )

        assert result == 0

    def test_update_to_past_time(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_expire_time(
            lock_name="force-expire-lock",
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
        mock_session.query.return_value.filter.return_value.delete.assert_called_once()

    def test_delete_returns_false_when_no_row_deleted(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_lock("missing-lock")

        assert result is False

    def test_delete_returns_false_when_rowcount_is_zero(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_lock("stale-lock")

        assert result is False
        assert isinstance(result, bool)


# ==================== delete_expired_locks Tests ====================


class TestDeleteExpiredLocks:
    """Tests for OrmDistributedLockRepository.delete_expired_locks()."""

    def test_deletes_expired_returns_rowcount(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 5

        result = repository.delete_expired_locks(NOW)

        assert result == 5
        mock_session.query.assert_called_once_with(DistributedLockModel)
        mock_session.query.return_value.filter.return_value.delete.assert_called_once()

    def test_returns_zero_when_no_expired(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_expired_locks(PAST)

        assert result == 0

    def test_delete_with_different_current_time(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 2

        result = repository.delete_expired_locks(FUTURE)

        assert result == 2

    def test_returns_int(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 3

        result = repository.delete_expired_locks(NOW)

        assert isinstance(result, int)
        assert result == 3


# ==================== DistributedLockRepository Protocol Tests ====================


class TestDistributedLockRepositoryProtocol:
    """Tests verifying that OrmDistributedLockRepository satisfies the Protocol."""

    def test_repository_is_protocol_instance(self, repository):
        assert isinstance(repository, DistributedLockRepository)

    def test_protocol_methods_exist(self, repository):
        """Verify all protocol methods are available."""
        assert hasattr(repository, "get_by_lock_name_for_update")
        assert hasattr(repository, "insert_lock")
        assert hasattr(repository, "update_lock_holder")
        assert hasattr(repository, "update_expire_time")
        assert hasattr(repository, "delete_lock")
        assert hasattr(repository, "delete_expired_locks")


# ==================== @with_orm_session Integration Tests ====================


class TestWithOrmSessionIntegration:
    """Tests verifying the @with_orm_session decorator lifecycle."""

    def test_decorator_opens_and_closes_session(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)
        mock_model = _make_model(id_val=1)
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        repo.get_by_lock_name_for_update("test-lock")

        mock_database.orm_session.assert_called_once()
        mock_session.query.assert_called_once()

    def test_session_is_cleaned_up_after_method(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)
        mock_model = _make_model(id_val=1)
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        repo.get_by_lock_name_for_update("test-lock")

        session_ctx = mock_database.orm_session.return_value
        session_ctx.__enter__.assert_called_once()
        session_ctx.__exit__.assert_called_once()

    def test_session_used_for_insert(self, mock_database, mock_session):
        repo = OrmDistributedLockRepository(mock_database)

        def _add_side_effect(model):
            model.id = 1

        mock_session.add.side_effect = _add_side_effect

        repo.insert_lock(
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
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        def _add_side_effect(model):
            model.id = 1

        mock_session.add.side_effect = _add_side_effect
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        repo.get_by_lock_name_for_update("lock-a")
        repo.insert_lock(lock_name="lock-b", lock_holder="h", expire_time=FUTURE)
        repo.delete_lock("lock-c")

        assert mock_database.orm_session.call_count == 3


# ==================== Round Trip / Integration Tests ====================


class TestMethodRoundTrips:
    """Tests covering multiple methods in sequence on the same repository."""

    def test_insert_then_get_by_lock_name(self, repository, mock_session):
        mock_model = _make_model(
            id_val=55,
            lock_name="roundtrip-lock",
            lock_holder="holder-rt",
            expire_time=FUTURE,
        )

        # First call: insert
        def _add_side_effect(model):
            model.id = 55

        mock_session.add.side_effect = _add_side_effect

        new_id = repository.insert_lock(
            lock_name="roundtrip-lock",
            lock_holder="holder-rt",
            expire_time=FUTURE,
        )
        assert new_id == 55

        # Reset side effects for the next get call
        mock_session.add.side_effect = None
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        record = repository.get_by_lock_name_for_update("roundtrip-lock")
        assert record is not None
        assert record.id == 55
        assert record.lock_name == "roundtrip-lock"
        assert record.lock_holder == "holder-rt"

    def test_insert_then_update_holder(self, repository, mock_session):
        def _add_side_effect(model):
            model.id = 10

        mock_session.add.side_effect = _add_side_effect
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repository.insert_lock(
            lock_name="update-lock",
            lock_holder="old-holder",
            expire_time=PAST,
        )
        result = repository.update_lock_holder(
            lock_name="update-lock",
            lock_holder="new-holder",
            expire_time=FUTURE,
        )
        assert result == 1

    def test_insert_then_delete(self, repository, mock_session):
        def _add_side_effect(model):
            model.id = 20

        mock_session.add.side_effect = _add_side_effect
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        repository.insert_lock(
            lock_name="temp-lock",
            lock_holder="temp-holder",
            expire_time=FUTURE,
        )
        deleted = repository.delete_lock("temp-lock")
        assert deleted is True

    def test_full_lock_lifecycle(self, repository, mock_session):
        """Simulate: insert -> get (acquire) -> update_holder (renew) -> update_expire_time -> delete (release)."""
        mock_model = _make_model(
            id_val=99,
            lock_name="lifecycle-lock",
            lock_holder="proc-1",
            expire_time=FUTURE,
        )

        def _add_side_effect(model):
            model.id = 99

        mock_session.add.side_effect = _add_side_effect
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        # Step 1: Insert
        lock_id = repository.insert_lock(
            lock_name="lifecycle-lock",
            lock_holder="proc-1",
            expire_time=FUTURE,
        )
        assert lock_id == 99

        # Step 2: Get by lock_name (with FOR UPDATE)
        record = repository.get_by_lock_name_for_update("lifecycle-lock")
        assert record is not None
        assert record.id == 99

        # Step 3: Renew (update holder + expire)
        updated = repository.update_lock_holder(
            lock_name="lifecycle-lock",
            lock_holder="proc-1",
            expire_time=FUTURE,
        )
        assert updated == 1

        # Step 4: Extend expire
        extended = repository.update_expire_time(
            lock_name="lifecycle-lock",
            expire_time=FUTURE,
        )
        assert extended == 1

        # Step 5: Release
        released = repository.delete_lock("lifecycle-lock")
        assert released is True

    def test_delete_expired_then_check(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 2

        count = repository.delete_expired_locks(NOW)
        assert count == 2

    def test_get_not_found_returns_none(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None

        result = repository.get_by_lock_name_for_update("nonexistent")
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
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        result = repository.get_by_lock_name_for_update("task:cleanup:daily")
        assert result is not None
        assert result.lock_name == "task:cleanup:daily"

    def test_lock_holder_is_empty_string(self, repository, mock_session):
        mock_model = _make_model(lock_name="empty-holder", lock_holder="")
        mock_session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_model

        result = repository.get_by_lock_name_for_update("empty-holder")
        assert result is not None
        assert result.lock_holder == ""

    def test_consecutive_inserts_with_different_names(self, repository, mock_session):
        def _add_side_effect(model):
            model.id = 1

        mock_session.add.side_effect = _add_side_effect

        id1 = repository.insert_lock(
            lock_name="lock-1",
            lock_holder="h1",
            expire_time=FUTURE,
        )
        id2 = repository.insert_lock(
            lock_name="lock-2",
            lock_holder="h2",
            expire_time=FUTURE,
        )
        assert id1 == 1
        assert id2 == 1  # mock returns same id

    def test_update_expire_time_with_large_dataset(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1000

        result = repository.update_expire_time(
            lock_name="bulk-lock",
            expire_time=FUTURE,
        )
        assert result == 1000

    def test_delete_expired_with_zero_rows(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_expired_locks(PAST)
        assert result == 0
