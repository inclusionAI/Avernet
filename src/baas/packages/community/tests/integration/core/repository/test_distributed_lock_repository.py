from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from secbaas.core.repository.distributed_lock import (
    DistributedLockRepository,
    LockRecord,
)
from secbaas.core.utils.env_utils import get_current_env

mysql_connector = pytest.importorskip("mysql.connector")

pytestmark = pytest.mark.integration

TEST_ENV = get_current_env()


def _generate_uuid() -> str:
    return uuid4().hex


class TestDistributedLockRepositoryProtocol:
    """Integration tests for DistributedLockRepository Protocol against ZDAS MySQL.

    Every test uses ONLY the DistributedLockRepository Protocol — no
    OrmDistributedLockRepository references allowed. db_transaction ensures
    all changes are rolled back.

    Tests cover all 6 methods:
      1. insert_lock + get_by_lock_name_for_update (SELECT FOR UPDATE)
      2. get_by_lock_name_for_update returns None for missing
      3. update_lock_holder (changes holder + expire_time)
      4. update_expire_time
      5. delete_lock (returns True for found, False for missing)
      6. delete_expired_locks (cleanup)
    """

    @pytest.fixture(autouse=True)
    def _cleanup_stale_locks(
        self, distributed_lock_repository: DistributedLockRepository
    ):
        """ORM backend auto-commits each test's inserts unlike ZDAS' rollback-based cleanup.
        Without this, test_delete_expired_locks sees expired locks from earlier insert tests."""
        distributed_lock_repository.delete_expired_locks(datetime(2099, 1, 1))

    # ── 1. insert_lock + get_by_lock_name_for_update ──

    def test_insert_lock_and_get_for_update(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        lock_name = _generate_uuid()
        lock_holder = _generate_uuid()
        expire_time = datetime.now() + timedelta(minutes=5)

        row_id = distributed_lock_repository.insert_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
        )
        assert isinstance(row_id, int)

        record = distributed_lock_repository.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert isinstance(record, LockRecord)
        assert record.id == row_id
        assert record.lock_name == lock_name
        assert record.lock_holder == lock_holder
        assert record.env == TEST_ENV
        assert record.expire_time is not None
        assert record.gmt_create is not None
        assert isinstance(record.gmt_create, datetime)
        assert record.gmt_modified is not None
        assert isinstance(record.gmt_modified, datetime)

    # ── 2. get_by_lock_name_for_update returns None for missing ──

    def test_get_by_lock_name_for_update_returns_none_for_missing(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        result = distributed_lock_repository.get_by_lock_name_for_update(
            "nonexistent-lock"
        )
        assert result is None

    # ── 3. update_lock_holder ──

    def test_update_lock_holder(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        lock_name = _generate_uuid()
        original_holder = _generate_uuid()
        new_holder = _generate_uuid()
        original_expire = datetime.now() + timedelta(minutes=5)

        distributed_lock_repository.insert_lock(
            lock_name=lock_name,
            lock_holder=original_holder,
            expire_time=original_expire,
        )

        new_expire = datetime.now() + timedelta(minutes=30)
        updated_rows = distributed_lock_repository.update_lock_holder(
            lock_name=lock_name,
            lock_holder=new_holder,
            expire_time=new_expire,
        )
        assert updated_rows == 1

        record = distributed_lock_repository.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert record.lock_holder == new_holder
        assert record.lock_name == lock_name
        assert record.env == TEST_ENV

    def test_update_lock_holder_on_nonexistent_lock(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        updated_rows = distributed_lock_repository.update_lock_holder(
            lock_name="nonexistent-lock",
            lock_holder="some-holder",
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert updated_rows == 0

    # ── 4. update_expire_time ──

    def test_update_expire_time(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        lock_name = _generate_uuid()
        lock_holder = _generate_uuid()
        original_expire = datetime.now() + timedelta(minutes=5)

        distributed_lock_repository.insert_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=original_expire,
        )

        new_expire = datetime.now() + timedelta(hours=2)
        updated_rows = distributed_lock_repository.update_expire_time(
            lock_name=lock_name,
            expire_time=new_expire,
        )
        assert updated_rows == 1

        record = distributed_lock_repository.get_by_lock_name_for_update(lock_name)
        assert record is not None
        # Holder should be unchanged
        assert record.lock_holder == lock_holder
        assert record.lock_name == lock_name

    def test_update_expire_time_on_nonexistent_lock(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        updated_rows = distributed_lock_repository.update_expire_time(
            lock_name="nonexistent-lock",
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert updated_rows == 0

    # ── 5. delete_lock ──

    def test_delete_lock_returns_true_for_found_lock(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        lock_name = _generate_uuid()

        distributed_lock_repository.insert_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(minutes=5),
        )

        deleted = distributed_lock_repository.delete_lock(lock_name)
        assert deleted is True

        # Verify the lock no longer exists
        record = distributed_lock_repository.get_by_lock_name_for_update(lock_name)
        assert record is None

    def test_delete_lock_returns_false_for_missing_lock(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        deleted = distributed_lock_repository.delete_lock("nonexistent-lock")
        assert deleted is False

    # ── 6. delete_expired_locks ──

    def test_delete_expired_locks_cleans_up_only_expired(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        active_name = _generate_uuid()
        expired_name_1 = _generate_uuid()
        expired_name_2 = _generate_uuid()

        now = datetime.now()
        active_expire = now + timedelta(hours=1)
        expired_expire = now - timedelta(hours=1)

        # Insert one active lock
        distributed_lock_repository.insert_lock(
            lock_name=active_name,
            lock_holder=_generate_uuid(),
            expire_time=active_expire,
        )
        # Insert two expired locks
        distributed_lock_repository.insert_lock(
            lock_name=expired_name_1,
            lock_holder=_generate_uuid(),
            expire_time=expired_expire,
        )
        distributed_lock_repository.insert_lock(
            lock_name=expired_name_2,
            lock_holder=_generate_uuid(),
            expire_time=expired_expire,
        )

        deleted_count = distributed_lock_repository.delete_expired_locks(datetime.now())
        assert deleted_count == 2

        # Active lock should still exist
        active_record = distributed_lock_repository.get_by_lock_name_for_update(
            active_name
        )
        assert active_record is not None
        assert active_record.lock_name == active_name

        # Expired locks should be gone
        assert (
            distributed_lock_repository.get_by_lock_name_for_update(expired_name_1)
            is None
        )
        assert (
            distributed_lock_repository.get_by_lock_name_for_update(expired_name_2)
            is None
        )

    def test_delete_expired_locks_returns_zero_when_none_expired(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        distributed_lock_repository.insert_lock(
            lock_name=_generate_uuid(),
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(hours=1),
        )

        deleted_count = distributed_lock_repository.delete_expired_locks(
            datetime.now() - timedelta(hours=1)
        )
        assert deleted_count == 0

    # ── 7. Full lifecycle: insert → get → update → delete ──

    def test_full_lifecycle_insert_get_update_delete(
        self, distributed_lock_repository: DistributedLockRepository, db_transaction
    ):
        lock_name = _generate_uuid()
        holder = _generate_uuid()
        expire = datetime.now() + timedelta(minutes=5)

        # Insert
        row_id = distributed_lock_repository.insert_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=expire,
        )
        assert isinstance(row_id, int)

        # Get (FOR UPDATE)
        record = distributed_lock_repository.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert record.lock_name == lock_name
        assert record.lock_holder == holder

        # Update holder
        new_holder = _generate_uuid()
        updated = distributed_lock_repository.update_lock_holder(
            lock_name=lock_name,
            lock_holder=new_holder,
            expire_time=datetime.now() + timedelta(minutes=10),
        )
        assert updated == 1

        record = distributed_lock_repository.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert record.lock_holder == new_holder

        # Update expire time (renew)
        renewed_expire = datetime.now() + timedelta(hours=3)
        updated = distributed_lock_repository.update_expire_time(
            lock_name=lock_name,
            expire_time=renewed_expire,
        )
        assert updated == 1

        # Delete
        deleted = distributed_lock_repository.delete_lock(lock_name)
        assert deleted is True

        # Verify gone
        assert (
            distributed_lock_repository.get_by_lock_name_for_update(lock_name) is None
        )
