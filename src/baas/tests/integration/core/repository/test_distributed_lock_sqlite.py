from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from secbaas.community.bootstrap import get_container
from secbaas.community.core.repository.distributed_lock import (
    DistributedLockRepository,
    LockRecord,
)

pytestmark = pytest.mark.integration


def _generate_uuid() -> str:
    return uuid4().hex


class TestDistributedLockSqliteOrmEquivalence:
    def test_try_acquire_and_get_roundtrip(self):
        repo: DistributedLockRepository = (
            get_container().repository.distributed_lock_repository()
        )
        lock_name = f"sqlite_lock_{_generate_uuid()[:12]}"
        lock_holder = _generate_uuid()
        expire_time = datetime.now() + timedelta(minutes=5)

        acquired = repo.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
        )
        assert acquired is True

        record = repo.get_by_lock_name(lock_name)
        assert isinstance(record, LockRecord)
        assert record.lock_name == lock_name
        assert record.lock_holder == lock_holder
        assert record.expire_time is not None

    def test_get_nonexistent(self):
        repo = get_container().repository.distributed_lock_repository()
        assert repo.get_by_lock_name(f"nonexistent_{_generate_uuid()}") is None

    def test_try_acquire_fails_when_held_by_other(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"held_lock_{_generate_uuid()[:12]}"
        holder_a = _generate_uuid()
        holder_b = _generate_uuid()
        expire_time = datetime.now() + timedelta(minutes=5)

        acquired_a = repo.try_acquire_lock(
            lock_name=lock_name, lock_holder=holder_a, expire_time=expire_time
        )
        assert acquired_a is True

        acquired_b = repo.try_acquire_lock(
            lock_name=lock_name, lock_holder=holder_b, expire_time=expire_time
        )
        assert acquired_b is False

    def test_try_acquire_reentrant_renew(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"reentrant_{_generate_uuid()[:12]}"
        holder = _generate_uuid()
        expire_time = datetime.now() + timedelta(minutes=5)

        acquired_1 = repo.try_acquire_lock(
            lock_name=lock_name, lock_holder=holder, expire_time=expire_time
        )
        assert acquired_1 is True

        acquired_2 = repo.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=10),
        )
        assert acquired_2 is True

    def test_try_acquire_takes_over_expired(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"expired_{_generate_uuid()[:12]}"
        old_holder = _generate_uuid()
        new_holder = _generate_uuid()

        acquired_old = repo.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=old_holder,
            expire_time=datetime.now() - timedelta(minutes=10),
        )
        assert acquired_old is True

        acquired_new = repo.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=new_holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        assert acquired_new is True

        record = repo.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_holder == new_holder

    def test_update_expire_time(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"expire_{_generate_uuid()[:12]}"
        holder = _generate_uuid()
        new_expire = datetime.now() + timedelta(minutes=15)

        repo.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=holder,
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        repo.update_expire_time(
            lock_name=lock_name,
            expire_time=new_expire,
        )

        record = repo.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_holder == holder

    def test_delete_lock(self):
        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"delete_{_generate_uuid()[:12]}"

        repo.try_acquire_lock(
            lock_name=lock_name,
            lock_holder=_generate_uuid(),
            expire_time=datetime.now() + timedelta(minutes=5),
        )
        deleted = repo.delete_lock(lock_name)
        assert deleted is True

        result = repo.get_by_lock_name(lock_name)
        assert result is None

    def test_delete_nonexistent_lock(self):
        repo = get_container().repository.distributed_lock_repository()
        assert repo.delete_lock(f"nonexistent_{_generate_uuid()}") is False

    def test_concurrent_acquire_same_new_lock_no_conflict_raised(self):
        """Two holders racing for the same brand-new lock must not raise and
        must serialize via the upsert: exactly one acquires, the other gets
        ``False``, and no IntegrityError escapes the repository (the upsert
        path replaces the former three-step INSERT that produced 1062).
        """
        from concurrent.futures import ThreadPoolExecutor

        repo = get_container().repository.distributed_lock_repository()
        lock_name = f"race_{_generate_uuid()[:12]}"
        expire_time = datetime.now() + timedelta(minutes=5)
        holders = [f"holder-{_generate_uuid()[:6]}", f"holder-{_generate_uuid()[:6]}"]

        def _bid(holder):
            # Each thread uses its own holder; the repository yields bool and
            # must never propagate a unique-constraint IntegrityError.
            return repo.try_acquire_lock(
                lock_name=lock_name, lock_holder=holder, expire_time=expire_time
            )

        with ThreadPoolExecutor(max_workers=2) as ex:
            results = list(ex.map(_bid, holders))

        assert all(isinstance(r, bool) for r in results)
        assert sum(1 for r in results if r is True) == 1, results
        assert sum(1 for r in results if r is False) == 1, results

        record = repo.get_by_lock_name(lock_name)
        assert record is not None
        assert record.lock_holder in holders
