from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from secbaas.bootstrap import get_container
from secbaas.core.repository.distributed_lock import (
    DistributedLockRepository,
    LockRecord,
)
from secbaas.core.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


def _generate_uuid() -> str:
    return uuid4().hex


class TestDistributedLockSqliteOrmEquivalence:
    def test_insert_and_get_roundtrip(self):
        repo: DistributedLockRepository = (
            get_container().repository.distributed_lock_repository()
        )
        lock_name = f"sqlite_lock_{_generate_uuid()[:12]}"
        lock_holder = _generate_uuid()
        expire_time = datetime.now() + timedelta(minutes=5)

        lock_id = repo.insert_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
        )
        assert lock_id > 0

        record = repo.get_by_lock_name_for_update(lock_name)
        assert isinstance(record, LockRecord)
        assert record.lock_name == lock_name
        assert record.lock_holder == lock_holder
        assert record.expire_time is not None
        assert record.gmt_create is not None

    def test_get_for_update_nonexistent(self):
        repo = get_container().repository.distributed_lock_repository()
        assert (
            repo.get_by_lock_name_for_update(f"nonexistent_{_generate_uuid()}") is None
        )

    def test_deep_update_lock_holder(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"sqlite_lock_{_generate_uuid()[:12]}"
        new_holder = _generate_uuid()

        repo.insert_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        repo.update_lock_holder(
            lock_name=lock_name,
            lock_holder=new_holder,
            expire_time=datetime.now() + timedelta(minutes=10),
        )
        record = repo.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert record.lock_holder == new_holder

    def test_deep_delete_lock(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"sqlite_lock_{_generate_uuid()[:12]}"

        repo.insert_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        deleted = repo.delete_lock(lock_name)
        assert deleted is True
        assert repo.get_by_lock_name_for_update(lock_name) is None

    def test_insert_lock_and_get_for_update(self):
        repo: DistributedLockRepository = (
            get_container().repository.distributed_lock_repository()
        )
        lock_name = f"equiv_lock_{_generate_uuid()[:12]}"
        lock_holder = _generate_uuid()
        expire_time = datetime.now() + timedelta(minutes=5)

        lock_id = repo.insert_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
        )
        assert lock_id > 0

        record = repo.get_by_lock_name_for_update(lock_name)
        assert isinstance(record, LockRecord)
        assert record.id == lock_id
        assert record.lock_name == lock_name
        assert record.lock_holder == lock_holder
        assert record.expire_time is not None
        assert record.gmt_create is not None

    def test_update_lock_holder(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"equiv_lock_{_generate_uuid()[:12]}"
        new_holder = _generate_uuid()
        new_expire = datetime.now() + timedelta(minutes=10)

        repo.insert_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        repo.update_lock_holder(
            lock_name=lock_name,
            lock_holder=new_holder,
            expire_time=new_expire,
        )

        record = repo.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert record.lock_holder == new_holder

    def test_update_expire_time(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"equiv_lock_{_generate_uuid()[:12]}"
        original_holder = _generate_uuid()
        new_expire = datetime.now() + timedelta(minutes=15)

        repo.insert_lock(
            lock_name=lock_name,
            lock_holder=original_holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        repo.update_expire_time(
            lock_name=lock_name,
            expire_time=new_expire,
        )

        record = repo.get_by_lock_name_for_update(lock_name)
        assert record is not None
        assert record.lock_holder == original_holder
        assert record.expire_time is not None

    def test_delete_lock(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"equiv_lock_{_generate_uuid()[:12]}"

        repo.insert_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        deleted = repo.delete_lock(lock_name)
        assert deleted is True

        result = repo.get_by_lock_name_for_update(lock_name)
        assert result is None

    def test_delete_expired_locks(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"equiv_lock_{_generate_uuid()[:12]}"

        repo.insert_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() - timedelta(minutes=10),
        )
        count = repo.delete_expired_locks(datetime.now())
        assert count >= 0

        result = repo.get_by_lock_name_for_update(lock_name)
        assert result is None

    def test_delete_nonexistent_lock(self):
        repo = get_container().repository.distributed_lock_repository()
        assert repo.delete_lock(f"nonexistent_{_generate_uuid()}") is False
