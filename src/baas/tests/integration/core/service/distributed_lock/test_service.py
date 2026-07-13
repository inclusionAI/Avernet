"""Integration tests for DistributedLockService."""

import time
from datetime import datetime, timedelta

import pytest

from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.distributed_lock import (
    OrmDistributedLockRepository,
)
from secbaas.community.core.service.distributed_lock import DistributedLockService


@pytest.fixture(scope="session")
def lock_repository(db_manager: DatabaseManager) -> OrmDistributedLockRepository:
    return OrmDistributedLockRepository(db_manager)


@pytest.fixture(scope="session")
def lock_service(
    lock_repository: OrmDistributedLockRepository,
) -> DistributedLockService:
    """Create DistributedLockService instance with auto-renew disabled for tests."""
    return DistributedLockService(
        repository=lock_repository,
        default_expire_seconds=30,
        renew_interval_seconds=0,  # Disable auto-renew for tests
    )


def generate_unique_lock_name() -> str:
    """Generate unique lock name for testing."""
    return f"test_service_lock_{int(time.time() * 1000000) % 10000000000}"


@pytest.fixture
def lock_name() -> str:
    """Generate a unique lock name for each test."""
    return generate_unique_lock_name()


@pytest.mark.integration
class TestDistributedLockServiceIntegration:
    """Integration tests for DistributedLockService."""

    def test_acquire_lock_success(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test successfully acquiring a lock."""
        try:
            lock = lock_service.acquire_lock(lock_name, expire_seconds=30)

            assert lock.acquired is True
            assert lock.lock_name == lock_name
            assert lock.reentrant_count == 1

            # Verify lock exists in database
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is not None
            assert record.lock_holder == lock.lock_holder
        finally:
            lock_service.force_unlock(lock_name)

    def test_acquire_lock_non_blocking_failure(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test that non-blocking acquire fails when lock is held by another."""
        try:
            # First lock
            lock1 = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock1.acquired is True

            # Second lock attempt (different holder, non-blocking)
            lock2 = lock_service.acquire_lock(
                lock_name, lock_holder="holder_2", block=False
            )

            assert lock2.acquired is False
        finally:
            lock_service.force_unlock(lock_name)

    def test_release_lock_success(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test successfully releasing a lock."""
        lock = lock_service.acquire_lock(lock_name, expire_seconds=30)
        assert lock.acquired is True

        # Release lock
        released = lock_service.release_lock(lock_name, lock.lock_holder)
        assert released is True

        # Verify lock is removed from database
        record = lock_repository.get_by_lock_name(lock_name)
        assert record is None

    def test_release_lock_wrong_holder(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test that releasing with wrong holder fails."""
        try:
            lock = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock.acquired is True

            # Try to release with wrong holder
            released = lock_service.release_lock(lock_name, "wrong_holder")
            assert released is False

            # Verify lock still exists
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is not None
        finally:
            lock_service.force_unlock(lock_name)

    def test_reentrant_lock(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test reentrant lock behavior."""
        try:
            # First acquire
            lock1 = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock1.acquired is True
            assert lock1.reentrant_count == 1

            # Second acquire (reentrant)
            lock2 = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock2.acquired is True
            assert lock2.reentrant_count == 2

            # Third acquire (reentrant)
            lock3 = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock3.acquired is True
            assert lock3.reentrant_count == 3

            # First release (decrements count)
            released = lock_service.release_lock(lock_name, "holder_1")
            assert released is True

            # Lock still exists
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is not None

            # Second release
            released = lock_service.release_lock(lock_name, "holder_1")
            assert released is True

            # Lock still exists
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is not None

            # Third release (actually releases)
            released = lock_service.release_lock(lock_name, "holder_1")
            assert released is True

            # Lock is now removed
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is None
        finally:
            lock_service.force_unlock(lock_name)

    def test_try_lock_context_manager(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test try_lock context manager."""
        with lock_service.try_lock(lock_name, expire_seconds=30) as lock:
            assert lock.acquired is True

            # Verify lock exists in database
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is not None

        # After context, lock should be released
        record = lock_repository.get_by_lock_name(lock_name)
        assert record is None

    def test_try_lock_failure_context_manager(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test try_lock context manager when lock is held by another."""
        try:
            # First lock
            with lock_service.try_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            ) as lock1:
                assert lock1.acquired is True

                # Second lock attempt (different holder)
                with lock_service.try_lock(
                    lock_name, lock_holder="holder_2", block=False
                ) as lock2:
                    assert lock2.acquired is False
        finally:
            lock_service.force_unlock(lock_name)

    def test_is_lock_held(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test is_lock_held method."""
        try:
            # No lock initially
            assert lock_service.is_lock_held(lock_name) is False

            # Acquire lock
            lock = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock.acquired is True

            # Check lock is held
            assert lock_service.is_lock_held(lock_name) is True
            assert lock_service.is_lock_held(lock_name, "holder_1") is True
            assert lock_service.is_lock_held(lock_name, "wrong_holder") is False
        finally:
            lock_service.force_unlock(lock_name)

    def test_get_lock_info(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test get_lock_info method."""
        try:
            # No lock initially
            info = lock_service.get_lock_info(lock_name)
            assert info is None

            # Acquire lock
            lock = lock_service.acquire_lock(
                lock_name, lock_holder="holder_1", expire_seconds=30
            )
            assert lock.acquired is True

            # Get lock info
            info = lock_service.get_lock_info(lock_name)
            assert info is not None
            assert info["lock_name"] == lock_name
            assert info["lock_holder"] == "holder_1"
            assert info["source"] == "local_context"
        finally:
            lock_service.force_unlock(lock_name)

    def test_force_unlock(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test force_unlock method."""
        # Acquire lock with different holder
        lock = lock_service.acquire_lock(
            lock_name, lock_holder="holder_1", expire_seconds=30
        )
        assert lock.acquired is True

        # Force unlock (admin operation)
        unlocked = lock_service.force_unlock(lock_name)
        assert unlocked is True

        # Verify lock is removed
        record = lock_repository.get_by_lock_name(lock_name)
        assert record is None

    def test_expired_lock_can_be_acquired(
        self,
        lock_service: DistributedLockService,
        lock_repository: OrmDistributedLockRepository,
        lock_name: str,
    ) -> None:
        """Test that expired locks can be acquired by another holder."""
        try:
            # Create lock with very short expiration (already expired)
            lock_repository.insert_lock(
                lock_name=lock_name,
                lock_holder="old_holder",
                expire_time=datetime.now() - timedelta(seconds=10),
            )

            # New holder should be able to acquire
            lock = lock_service.acquire_lock(
                lock_name, lock_holder="new_holder", expire_seconds=30
            )
            assert lock.acquired is True

            # Verify holder is updated
            record = lock_repository.get_by_lock_name(lock_name)
            assert record is not None
            assert record.lock_holder == "new_holder"
        finally:
            lock_service.force_unlock(lock_name)
