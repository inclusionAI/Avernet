"""Tests for DistributedLockService."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from secbaas.core.service.distributed_lock import DistributedLockService
from secbaas.core.service.distributed_lock._service import LockContext


@pytest.fixture
def repository():
    return MagicMock()


@pytest.fixture
def lock_service(repository):
    return DistributedLockService(
        repository,
        default_expire_seconds=30,
        renew_interval_seconds=10,
    )


@pytest.fixture
def lock_service_no_renew(repository):
    return DistributedLockService(
        repository,
        default_expire_seconds=5,
        renew_interval_seconds=0,
    )


@pytest.fixture
def lock_service_short_renew(repository):
    return DistributedLockService(
        repository,
        default_expire_seconds=5,
        renew_interval_seconds=0.05,
    )


@pytest.fixture(autouse=True)
def reset_service_state(lock_service):
    """Reset service-level state before each test to ensure test isolation."""
    with lock_service._local_lock:
        lock_service._lock_contexts.clear()
    yield
    with lock_service._local_lock:
        for ctx in list(lock_service._lock_contexts.values()):
            if ctx.renew_thread and ctx.renew_thread.is_alive():
                ctx.stop_renew.set()
                ctx.renew_thread.join(timeout=2.0)
        lock_service._lock_contexts.clear()


# ── LockContext tests ────────────────────────────────────────────


class TestLockContext:
    def test_default_values(self):
        ctx = LockContext(lock_name="test", lock_holder="h1", expire_time=MagicMock())
        assert ctx.lock_name == "test"
        assert ctx.lock_holder == "h1"
        assert ctx.reentrant_count == 1
        assert ctx.renew_thread is None
        assert not ctx.stop_renew.is_set()
        assert ctx.acquired is False

    def test_explicit_values(self):
        stop_ev = threading.Event()
        ctx = LockContext(
            lock_name="lk1",
            lock_holder="h2",
            expire_time=MagicMock(),
            reentrant_count=3,
            renew_thread=None,
            stop_renew=stop_ev,
            acquired=True,
        )
        assert ctx.reentrant_count == 3
        assert ctx.acquired is True
        assert ctx.stop_renew is stop_ev

    def test_state_transitions(self):
        ctx = LockContext(lock_name="lk", lock_holder="h", expire_time=MagicMock())
        ctx.reentrant_count += 1
        assert ctx.reentrant_count == 2
        ctx.reentrant_count -= 1
        assert ctx.reentrant_count == 1
        ctx.acquired = True
        assert ctx.acquired is True
        ctx.acquired = False
        assert ctx.acquired is False


# ── acquire_lock tests ───────────────────────────────────────────


class TestAcquireLock:
    def test_acquire_success(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="holder1")

        assert ctx.acquired is True
        assert ctx.lock_name == "mylock"
        assert ctx.lock_holder == "holder1"
        assert ctx.reentrant_count == 1
        repository.insert_lock.assert_called_once()

    def test_acquire_auto_generates_holder(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock")

        assert ctx.acquired is True
        assert ctx.lock_holder is not None
        assert "_" in ctx.lock_holder

    def test_acquire_default_expire(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")

        assert ctx.acquired is True
        import datetime

        delta = ctx.expire_time - datetime.datetime.now()
        assert 28 <= delta.total_seconds() <= 32

    def test_acquire_custom_expire(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1", expire_seconds=60)

        assert ctx.acquired is True
        import datetime

        delta = ctx.expire_time - datetime.datetime.now()
        assert 58 <= delta.total_seconds() <= 62

    def test_acquire_fails_when_already_held_by_other(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() + datetime.timedelta(seconds=30)
        record.lock_holder = "other_holder"
        repository.get_by_lock_name.return_value = record

        ctx = lock_service.acquire_lock("mylock", lock_holder="holder1")

        assert ctx.acquired is False
        assert ctx.lock_name == "mylock"
        assert ctx.lock_holder == "holder1"

    def test_acquire_reentrant_same_holder(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx1 = lock_service.acquire_lock("mylock", lock_holder="h1")
        assert ctx1.reentrant_count == 1

        ctx2 = lock_service.acquire_lock("mylock", lock_holder="h1")
        assert ctx2 is ctx1
        assert ctx2.reentrant_count == 2

        ctx3 = lock_service.acquire_lock("mylock", lock_holder="h1")
        assert ctx3.reentrant_count == 3

    def test_acquire_expired_lock_taken_over(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() - datetime.timedelta(seconds=30)
        record.lock_holder = "old_holder"
        repository.get_by_lock_name.return_value = record
        repository.delete_lock.return_value = True
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="new_holder")

        assert ctx.acquired is True
        assert ctx.lock_holder == "new_holder"
        repository.delete_lock.assert_called_once_with("mylock")
        repository.insert_lock.assert_called_once()

    def test_acquire_expired_lock_insert_fails(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() - datetime.timedelta(seconds=30)
        record.lock_holder = "old_holder"
        repository.get_by_lock_name.return_value = record
        repository.delete_lock.return_value = True
        repository.insert_lock.return_value = 0

        ctx = lock_service.acquire_lock("mylock", lock_holder="new_holder")

        assert ctx.acquired is False

    def test_acquire_when_holder_already_owns_in_db(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() + datetime.timedelta(seconds=30)
        record.lock_holder = "holder1"
        repository.get_by_lock_name.return_value = record
        repository.update_expire_time.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="holder1")

        assert ctx.acquired is True
        assert ctx.lock_holder == "holder1"
        repository.update_expire_time.assert_called_once()

    def test_acquire_insert_lock_raises_exception(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.side_effect = Exception("duplicate")

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")

        assert ctx.acquired is False

    def test_acquire_general_exception(self, lock_service, repository):
        repository.get_by_lock_name.side_effect = Exception("db error")

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")

        assert ctx.acquired is False

    def test_acquire_blocking_success_after_retry(self, lock_service, repository):
        import datetime

        call_count = [0]

        def side_effect(lock_name):
            call_count[0] += 1
            if call_count[0] <= 1:
                record = MagicMock()
                record.expire_time = datetime.datetime.now() + datetime.timedelta(
                    seconds=300
                )
                record.lock_holder = "other"
                return record
            return None

        repository.get_by_lock_name.side_effect = side_effect
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock(
            "mylock", lock_holder="h1", block=True, block_timeout=5.0
        )

        assert ctx.acquired is True
        assert call_count[0] >= 2

    def test_acquire_blocking_timeout(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() + datetime.timedelta(seconds=300)
        record.lock_holder = "other"
        repository.get_by_lock_name.return_value = record

        ctx = lock_service.acquire_lock(
            "mylock", lock_holder="h1", block=True, block_timeout=0.05
        )

        assert ctx.acquired is False

    def test_acquire_lock_name_in_context_different_holder(
        self, lock_service, repository
    ):
        """Lock name in context but with different holder — falls through to DB."""
        import datetime

        with lock_service._local_lock:
            existing_ctx = LockContext(
                lock_name="mylock",
                lock_holder="other_holder",
                expire_time=datetime.datetime.now(),
            )
            lock_service._lock_contexts["mylock"] = existing_ctx

        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")

        assert ctx.acquired is True
        assert ctx.lock_holder == "h1"


# ── release_lock tests ───────────────────────────────────────────


class TestReleaseLock:
    def test_release_not_in_context(self, lock_service):
        result = lock_service.release_lock("nonexistent", lock_holder="h1")
        assert result is False

    def test_release_holder_mismatch(self, lock_service):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="holder_A",
            expire_time=datetime.datetime.now(),
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        result = lock_service.release_lock("mylock", lock_holder="holder_B")
        assert result is False

    def test_release_holder_in_context_uses_context_holder_when_none(
        self, lock_service, repository
    ):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="holder_A",
            expire_time=datetime.datetime.now(),
            reentrant_count=1,
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.delete_lock.return_value = True

        result = lock_service.release_lock("mylock", lock_holder=None)

        assert result is True
        repository.delete_lock.assert_called_once_with("mylock")
        assert "mylock" not in lock_service._lock_contexts

    def test_release_reentrant_decrements(self, lock_service):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
            reentrant_count=3,
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        result = lock_service.release_lock("mylock", lock_holder="h1")

        assert result is True
        assert ctx.reentrant_count == 2
        assert "mylock" in lock_service._lock_contexts

    def test_release_final_decrements_to_zero_then_deletes(
        self, lock_service, repository
    ):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
            reentrant_count=1,
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.delete_lock.return_value = True

        result = lock_service.release_lock("mylock", lock_holder="h1")

        assert result is True
        repository.delete_lock.assert_called_once_with("mylock")
        assert "mylock" not in lock_service._lock_contexts

    def test_release_lock_delete_lock_returns_false(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
            reentrant_count=1,
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.delete_lock.return_value = False

        result = lock_service.release_lock("mylock", lock_holder="h1")

        assert result is False

    def test_release_lock_exception(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
            reentrant_count=1,
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.delete_lock.side_effect = Exception("db error")

        result = lock_service.release_lock("mylock", lock_holder="h1")

        assert result is False


# ── try_lock context manager tests ───────────────────────────────


class TestTryLock:
    def test_acquired_then_releases(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1
        repository.delete_lock.return_value = True

        with lock_service.try_lock("mylock", lock_holder="h1") as ctx:
            assert ctx.acquired is True
            assert ctx.lock_name == "mylock"

        assert "mylock" not in lock_service._lock_contexts

    def test_not_acquired_does_not_release(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() + datetime.timedelta(seconds=30)
        record.lock_holder = "other"
        repository.get_by_lock_name.return_value = record

        with lock_service.try_lock("mylock", lock_holder="h1") as ctx:
            assert ctx.acquired is False

        assert "mylock" not in lock_service._lock_contexts

    def test_try_lock_with_blocking(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1
        repository.delete_lock.return_value = True

        with lock_service.try_lock(
            "mylock", lock_holder="h1", block=True, block_timeout=2.0
        ) as ctx:
            assert ctx.acquired is True

    def test_try_lock_reentrant_with_cm(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1
        repository.delete_lock.return_value = True

        with lock_service.try_lock("mylock", lock_holder="h1") as ctx1:
            assert ctx1.acquired is True
            assert ctx1.reentrant_count == 1

            with lock_service.try_lock("mylock", lock_holder="h1") as ctx2:
                assert ctx2 is ctx1
                assert ctx2.reentrant_count == 2

        assert "mylock" not in lock_service._lock_contexts


# ── Auto-renew thread tests ──────────────────────────────────────


class TestAutoRenew:
    def test_start_renew_starts_daemon_thread(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")

        assert ctx.renew_thread is not None
        assert ctx.renew_thread.is_alive()
        assert ctx.renew_thread.daemon is True

        _stop_thread(ctx)

    def test_renew_disabled_when_interval_zero(self, lock_service_no_renew, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service_no_renew.acquire_lock("mylock", lock_holder="h1")

        assert ctx.renew_thread is None

    def test_renew_updates_expire_time(self, lock_service_short_renew, repository):

        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        call_counter = [0]

        def delayed_update_expire_time(*, lock_name, expire_time):
            call_counter[0] += 1
            return 1

        repository.update_expire_time.side_effect = delayed_update_expire_time

        ctx = lock_service_short_renew.acquire_lock("mylock", lock_holder="h1")
        assert ctx.renew_thread is not None

        time.sleep(0.3)

        _stop_thread(ctx)

        assert call_counter[0] >= 1

    def test_renew_thread_stops_on_release(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1
        repository.delete_lock.return_value = True

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")
        assert ctx.renew_thread is not None
        assert ctx.renew_thread.is_alive()

        lock_service.release_lock("mylock", lock_holder="h1")

        ctx.renew_thread.join(timeout=2.0)
        assert ctx.renew_thread.is_alive() is False

    def test_renew_thread_breaks_on_failure(self, lock_service_short_renew, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1
        repository.update_expire_time.return_value = 0

        ctx = lock_service_short_renew.acquire_lock("mylock", lock_holder="h1")
        assert ctx.renew_thread is not None

        ctx.renew_thread.join(timeout=2.0)
        assert ctx.renew_thread.is_alive() is False

    def test_renew_thread_breaks_on_exception(
        self, lock_service_short_renew, repository
    ):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1
        repository.update_expire_time.side_effect = Exception("renew error")

        ctx = lock_service_short_renew.acquire_lock("mylock", lock_holder="h1")
        assert ctx.renew_thread is not None

        ctx.renew_thread.join(timeout=2.0)
        assert ctx.renew_thread.is_alive() is False

    def test_stop_renew_no_thread(self, lock_service):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        lock_service._stop_renew_thread(ctx)

    def test_renew_worker_stops_when_event_set(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None
        repository.insert_lock.return_value = 1

        ctx = lock_service.acquire_lock("mylock", lock_holder="h1")
        assert ctx.renew_thread is not None

        ctx.stop_renew.set()
        ctx.renew_thread.join(timeout=2.0)
        assert ctx.renew_thread.is_alive() is False


# ── is_lock_held tests ───────────────────────────────────────────


class TestIsLockHeld:
    def test_held_in_local_context(self, lock_service, repository):
        import datetime

        repository.get_by_lock_name.return_value = None

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        assert lock_service.is_lock_held("mylock") is True
        assert lock_service.is_lock_held("mylock", lock_holder="h1") is True
        assert lock_service.is_lock_held("mylock", lock_holder="h2") is False

    def test_not_held_local_or_db(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None

        assert lock_service.is_lock_held("mylock") is False

    def test_held_in_db_not_expired(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() + datetime.timedelta(seconds=30)
        record.lock_holder = "h1"
        repository.get_by_lock_name.return_value = record

        assert lock_service.is_lock_held("mylock") is True
        assert lock_service.is_lock_held("mylock", lock_holder="h1") is True
        assert lock_service.is_lock_held("mylock", lock_holder="h2") is False

    def test_held_in_db_expired(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.expire_time = datetime.datetime.now() - datetime.timedelta(seconds=30)
        record.lock_holder = "h1"
        repository.get_by_lock_name.return_value = record

        assert lock_service.is_lock_held("mylock") is False


# ── renew_lock tests ─────────────────────────────────────────────


class TestRenewLock:
    def test_renew_not_in_context(self, lock_service):
        result = lock_service.renew_lock("mylock", "h1")
        assert result is False

    def test_renew_holder_mismatch(self, lock_service):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        result = lock_service.renew_lock("mylock", "h2")
        assert result is False

    def test_renew_success(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.update_expire_time.return_value = 1

        result = lock_service.renew_lock("mylock", "h1", additional_seconds=60)

        assert result is True
        repository.update_expire_time.assert_called_once()

    def test_renew_default_seconds(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.update_expire_time.return_value = 1

        result = lock_service.renew_lock("mylock", "h1")

        assert result is True

    def test_renew_update_fails(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.update_expire_time.return_value = 0

        result = lock_service.renew_lock("mylock", "h1")
        assert result is False

    def test_renew_exception(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.update_expire_time.side_effect = Exception("error")

        result = lock_service.renew_lock("mylock", "h1")
        assert result is False


# ── force_unlock tests ───────────────────────────────────────────


class TestForceUnlock:
    def test_not_in_context(self, lock_service, repository):
        repository.delete_lock.return_value = True

        result = lock_service.force_unlock("mylock")

        assert result is True
        repository.delete_lock.assert_called_once_with("mylock")

    def test_in_context_stops_thread(self, lock_service, repository):
        import datetime

        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=datetime.datetime.now(),
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        repository.delete_lock.return_value = True

        result = lock_service.force_unlock("mylock")

        assert result is True
        assert "mylock" not in lock_service._lock_contexts
        repository.delete_lock.assert_called_once_with("mylock")

    def test_delete_lock_returns_false(self, lock_service, repository):
        repository.delete_lock.return_value = False

        result = lock_service.force_unlock("mylock")

        assert result is False


# ── get_lock_info tests ──────────────────────────────────────────


class TestGetLockInfo:
    def test_in_local_context(self, lock_service):
        import datetime

        ts = datetime.datetime(2024, 1, 1, 12, 0, 0)
        ctx = LockContext(
            lock_name="mylock",
            lock_holder="h1",
            expire_time=ts,
            reentrant_count=2,
            acquired=True,
        )
        with lock_service._local_lock:
            lock_service._lock_contexts["mylock"] = ctx

        info = lock_service.get_lock_info("mylock")

        assert info is not None
        assert info["lock_name"] == "mylock"
        assert info["lock_holder"] == "h1"
        assert info["reentrant_count"] == 2
        assert info["acquired"] is True
        assert info["source"] == "local_context"

    def test_in_database(self, lock_service, repository):
        import datetime

        record = MagicMock()
        record.lock_name = "db_lock"
        record.lock_holder = "db_holder"
        record.expire_time = datetime.datetime(2024, 6, 1, 12, 0, 0)
        record.gmt_create = datetime.datetime(2024, 1, 1, 0, 0, 0)
        record.gmt_modified = datetime.datetime(2024, 1, 2, 0, 0, 0)
        repository.get_by_lock_name.return_value = record

        info = lock_service.get_lock_info("db_lock")

        assert info is not None
        assert info["lock_name"] == "db_lock"
        assert info["lock_holder"] == "db_holder"
        assert info["source"] == "database"

    def test_not_found(self, lock_service, repository):
        repository.get_by_lock_name.return_value = None

        info = lock_service.get_lock_info("nonexistent")
        assert info is None


# ── Helpers ──────────────────────────────────────────────────────


def _stop_thread(ctx: LockContext) -> None:
    """Stop a renew thread and wait for it to finish."""
    ctx.stop_renew.set()
    if ctx.renew_thread and ctx.renew_thread.is_alive():
        ctx.renew_thread.join(timeout=2.0)
