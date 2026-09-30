"""Session isolation tests for ``@with_orm_session``.

Repositories are DI singletons, so one instance serves every request in the
process. These tests pin down that the session opened for one call is never
visible to another — the property that makes it safe for an ``async def``
handler to offload repository calls with ``asyncio.to_thread``.

The tests are deterministic rather than timing-based: a barrier holds both
callers inside their repository method at the same moment, which is exactly the
window in which a shared ``self._session`` attribute gets overwritten.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager

import pytest

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session


class _FakeSession:
    """Stand-in for a SQLAlchemy Session, distinguishable by identity."""

    def __init__(self, label: str) -> None:
        self.label = label
        # Keeps the decorator off the session.connection() path.
        self.is_active = False


class _FakeDatabase:
    """Yields a fresh session per ``orm_session()`` call, like the real manager."""

    def __init__(self) -> None:
        self._counter = 0
        self._lock = threading.Lock()

    @contextmanager
    def orm_session(self):
        with self._lock:
            self._counter += 1
            label = f"session-{self._counter}"
        yield _FakeSession(label)


class _FakeRepository(OrmConnectionMixin):
    """Minimal repository with the same shape as the ORM repositories."""

    def __init__(self, database: _FakeDatabase) -> None:
        self._database = database

    @with_orm_session
    def read_twice(self, barrier: threading.Barrier | None = None) -> tuple:
        """Read the bound session, yield to the other caller, read it again."""
        first = self._session
        if barrier is not None:
            barrier.wait(timeout=5)
        second = self._session
        return first, second

    @with_orm_session
    def read_once(self) -> _FakeSession:
        return self._session


@pytest.fixture
def repository() -> _FakeRepository:
    return _FakeRepository(_FakeDatabase())


class TestConcurrentThreads:
    """Concurrent worker threads must not share a session."""

    @pytest.mark.asyncio
    async def test_concurrent_to_thread_calls_keep_separate_sessions(self, repository):
        """Two `asyncio.to_thread` calls on one singleton stay isolated.

        Both threads sit inside `read_twice` simultaneously, so with the
        session stored on the shared instance the second binding clobbers the
        first and the earlier caller finishes against the later caller's
        session — another request's transaction, and potentially another
        tenant's data.
        """
        barrier = threading.Barrier(2)

        first_call, second_call = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(repository.read_twice, barrier),
                asyncio.to_thread(repository.read_twice, barrier),
            ),
            timeout=10,
        )

        # Neither caller's session changed under it while it was suspended.
        assert first_call[0] is first_call[1]
        assert second_call[0] is second_call[1]
        # And the two callers were handed genuinely different sessions.
        assert first_call[0] is not second_call[0]

    @pytest.mark.asyncio
    async def test_session_unbound_after_call_completes(self, repository):
        """The binding does not outlive the call that opened it."""
        await asyncio.to_thread(repository.read_once)

        with pytest.raises(RuntimeError, match="no ORM session bound"):
            _ = repository._session


class TestSequentialCalls:
    """The single-threaded contract is unchanged."""

    def test_each_call_sees_its_own_session(self, repository):
        first = repository.read_once()
        second = repository.read_once()

        assert first is not second
        assert (first.label, second.label) == ("session-1", "session-2")

    def test_direct_assignment_is_visible_to_the_same_context(self, repository):
        """Direct `_session` assignment still works for tests and callers."""
        injected = _FakeSession("injected")
        repository._session = injected

        assert repository._session is injected

    def test_two_repositories_do_not_share_a_binding(self):
        """A session is bound per repository instance, not globally."""
        database = _FakeDatabase()
        left, right = _FakeRepository(database), _FakeRepository(database)

        left_session = left.read_once()
        right_session = right.read_once()

        assert left_session is not right_session
