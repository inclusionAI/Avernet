from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from secbaas.community.core.repository.distributed_lock import (
    DistributedLockRepository,
    LockRecord,
)

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration


def _generate_uuid() -> str:
    return uuid4().hex


class TestDistributedLockRepositoryProtocol:
    """Integration tests for DistributedLockRepository Protocol against ZDAS MySQL.

    Every test uses ONLY the DistributedLockRepository Protocol — no
    OrmDistributedLockRepository references allowed. db_transaction ensures
    all changes are rolled back.

    Tests cover all protocol methods:
      1. try_acquire_lock + get_by_lock_name
      2. get_by_lock_name returns None for missing
      3. try_acquire_lock fails when held by other
      4. try_acquire_lock reentrant renew
      5. try_acquire_lock takes over expired lock
      6. update_expire_time
      7. delete_lock (returns True for found, False for missing)
      8. Full lifecycle: acquire → get → renew → delete
    """

    @pytest.fixture(autouse=True)
    def _cleanup_stale_locks(
        self, distributed_lock_repository: DistributedLockRepository
    ):
        """Clean up any stale locks before each test."""
        # Delete all locks with names starting with test prefix
        # Since delete_expired_locks was removed, we use delete_lock per test
        pass

    # ── 1. try_acquire_lock + get_by_lock_name ──

    def test_try_acquire_and_get(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"test_lock_{_generate_uuid()[:12]}"
        lock_holder = f"holder_{_generate_uuid()[:8]}"
        expire_time = datetime.now() + timedelta(minutes=5)

        acquired = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
        )
        assert acquired is True

        record = distributed_lock_repository.get_by_lock_name(lock_name)
        assert isinstance(record, LockRecord)
        assert record.lock_name == lock_name
        assert record.lock_holder == lock_holder
        assert record.expire_time is not None
        assert record.gmt_create is not None
        assert isinstance(record.gmt_modified, datetime)

    # ── 2. get_by_lock_name returns None for missing ──

    def test_get_by_lock_name_returns_none_for_missing(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        result = distributed_lock_repository.get_by_lock_name("nonexistent-lock")
        assert result is None

    # ── 3. try_acquire_lock fails when held by other ──

    def test_try_acquire_fails_when_held_by_other(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"held_lock_{_generate_uuid()[:12]}"
        holder_a = f"holder_{_generate_uuid()[:8]}"
        holder_b = f"holder_{_generate_uuid()[:8]}"
        expire_time = datetime.now() + timedelta(minutes=5)

        acquired_a = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder_a,
            expire_time=expire_time,
        )
        assert acquired_a is True

        acquired_b = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder_b,
            expire_time=expire_time,
        )
        assert acquired_b is False

    # ── 4. try_acquire_lock reentrant renew ──

    def test_try_acquire_reentrant_renew(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"reentrant_{_generate_uuid()[:12]}"
        holder = f"holder_{_generate_uuid()[:8]}"

        acquired_1 = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert acquired_1 is True

        acquired_2 = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=10),
        )
        assert acquired_2 is True

        record = distributed_lock_repository.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_holder == holder

    # ── 5. try_acquire_lock takes over expired lock ──

    def test_try_acquire_takes_over_expired(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"expired_{_generate_uuid()[:12]}"
        old_holder = f"holder_{_generate_uuid()[:8]}"
        new_holder = f"holder_{_generate_uuid()[:8]}"

        acquired_old = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=old_holder,
            expire_time=datetime.now() - timedelta(minutes=10),
        )
        assert acquired_old is True

        acquired_new = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=new_holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert acquired_new is True

        record = distributed_lock_repository.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_holder == new_holder

    # ── 6. update_expire_time ──

    def test_update_expire_time(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"expire_{_generate_uuid()[:12]}"
        holder = f"holder_{_generate_uuid()[:8]}"
        new_expire = datetime.now() + timedelta(minutes=15)

        distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )

        updated_rows = distributed_lock_repository.update_expire_time(
            lock_name=lock_name,
            expire_time=new_expire,
        )
        assert updated_rows == 1

        record = distributed_lock_repository.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_holder == holder

    def test_update_expire_time_on_nonexistent_lock(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        updated_rows = distributed_lock_repository.update_expire_time(
            lock_name="nonexistent-lock",
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert updated_rows == 0

    # ── 7. delete_lock ──

    def test_delete_lock_returns_true_for_found_lock(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"delete_{_generate_uuid()[:12]}"
        holder = f"holder_{_generate_uuid()[:8]}"

        distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )

        deleted = distributed_lock_repository.delete_lock(lock_name)
        assert deleted is True

    def test_delete_lock_returns_false_for_missing_lock(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        deleted = distributed_lock_repository.delete_lock("nonexistent-lock")
        assert deleted is False

    # ── 8. Full lifecycle: acquire → get → renew → delete ──

    def test_full_lifecycle_acquire_get_renew_delete(
        self,
        distributed_lock_repository: DistributedLockRepository,
        db_transaction,
    ):
        lock_name = f"lifecycle_{_generate_uuid()[:12]}"
        holder = f"holder_{_generate_uuid()[:8]}"

        # Acquire
        acquired = distributed_lock_repository.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert acquired is True

        # Get
        record = distributed_lock_repository.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_name == lock_name
        assert record.lock_holder == holder

        # Renew
        updated = distributed_lock_repository.update_expire_time(
            lock_name=lock_name,
            expire_time=datetime.now() + timedelta(minutes=30),
        )
        assert updated == 1

        # Delete
        deleted = distributed_lock_repository.delete_lock(lock_name)
        assert deleted is True

        # Verify deleted
        assert distributed_lock_repository.get_by_lock_name(lock_name) is None
