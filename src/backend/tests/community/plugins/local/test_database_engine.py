"""Unit tests for ``plugins/local/database.py``.

Pins the in-memory-only contract: file-backed SQLite has been removed,
``DATABASE_URL`` is no longer consulted, ``StaticPool`` shares one
connection across checkouts so seeded rows are visible, and
``reset_for_tests()`` swaps in a fresh engine.
"""
from __future__ import annotations

import threading
from threading import Event, Thread, current_thread

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool

from agentclaw.community.plugins.local import database as db_mod


@pytest.fixture(autouse=True)
def _reset_between_tests():
    """Guarantee a fresh engine per test in this module."""
    db_mod.reset_for_tests()
    yield
    db_mod.reset_for_tests()


def test_engine_uses_static_pool() -> None:
    """The lazy engine must be backed by ``StaticPool`` so a single
    in-memory database is shared across all connection checkouts.
    Without this, each checkout would get its own empty ``:memory:`` DB.
    """
    db_mod._get_session_factory()  # force engine creation
    assert db_mod._engine is not None
    assert isinstance(db_mod._engine.pool, StaticPool)


def test_seeded_row_visible_across_session_checkouts() -> None:
    """A row inserted via one ``SessionLocal()`` must be readable via a
    subsequent ``SessionLocal()`` — proves StaticPool connection sharing.
    """
    s1 = db_mod.SessionLocal()
    try:
        s1.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        s1.execute(text("INSERT INTO t (id, v) VALUES (1, 'hello')"))
        s1.commit()
    finally:
        s1.close()

    s2 = db_mod.SessionLocal()
    try:
        row = s2.execute(text("SELECT v FROM t WHERE id = 1")).first()
        assert row is not None and row[0] == "hello"
    finally:
        s2.close()


def test_reset_swaps_engine_to_a_fresh_one() -> None:
    """After ``reset_for_tests()``, the next ``_get_session_factory()``
    call must produce a factory bound to a different engine object.
    """
    db_mod._get_session_factory()
    engine_before = db_mod._engine
    assert engine_before is not None

    db_mod.reset_for_tests()
    assert db_mod._engine is None
    assert db_mod._session_factory is None

    db_mod._get_session_factory()
    engine_after = db_mod._engine
    assert engine_after is not None
    assert engine_after is not engine_before


def test_reset_is_idempotent() -> None:
    """Calling ``reset_for_tests()`` twice in a row must not raise,
    including when no engine has ever been created.
    """
    db_mod.reset_for_tests()
    db_mod.reset_for_tests()  # no-op second call
    db_mod._get_session_factory()
    db_mod.reset_for_tests()
    db_mod.reset_for_tests()  # no-op second call after disposal


def test_database_url_env_var_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DATABASE_URL`` is no longer consulted — setting it must not
    change the engine URL. This pins the contract so future regressions
    that re-introduce env-var indirection are caught.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/should_not_be_used.db")
    db_mod.reset_for_tests()
    db_mod._get_session_factory()
    assert db_mod._engine is not None
    assert str(db_mod._engine.url) == "sqlite:///:memory:"


def test_no_backend_db_file_on_disk_after_use(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check: exercising the engine from a clean cwd must not
    create ``backend.db`` on disk. Belt-and-suspenders for the
    "in-memory only" guarantee.
    """
    monkeypatch.chdir(tmp_path)
    db_mod.reset_for_tests()
    s = db_mod.SessionLocal()
    try:
        s.execute(text("CREATE TABLE t (id INTEGER)"))
        s.commit()
    finally:
        s.close()
    assert not (tmp_path / "backend.db").exists()


def test_session_and_orm_session_share_one_process_lock() -> None:
    """Both public context managers must serialize the shared connection.

    ``StaticPool`` exposes one DBAPI connection process-wide.  A writer that
    enters through ``orm_session()`` must therefore wait until a concurrent
    ``session()`` context has released that connection.
    """
    db = db_mod.SqliteDB()
    lock_results: list[bool] = []
    second_entered = Event()
    errors: list[BaseException] = []

    def probe_lock() -> None:
        acquired = db_mod._session_lock.acquire(blocking=False)
        lock_results.append(acquired)
        if acquired:
            db_mod._session_lock.release()

    def enter_after_release() -> None:
        try:
            with db.orm_session():
                second_entered.set()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    with db.session():
        probe_thread = Thread(target=probe_lock)
        probe_thread.start()
        probe_thread.join(timeout=5)

    second_thread = Thread(target=enter_after_release)
    second_thread.start()
    second_thread.join(timeout=5)

    assert errors == []
    assert not probe_thread.is_alive()
    assert not second_thread.is_alive()
    assert lock_results == [False]
    assert second_entered.is_set()


def test_reset_for_tests_waits_for_active_session_lock() -> None:
    """Reset must acquire the same lock before disposing the shared engine."""
    reset_acquire_attempted = Event()
    session_active = Event()
    allow_session_exit = Event()
    reset_finished = Event()
    original_lock = db_mod._session_lock

    class _ObservedRLock:
        def __init__(self) -> None:
            self._lock = threading.RLock()

        def __enter__(self):
            if current_thread().name == "database-reset":
                reset_acquire_attempted.set()
            self._lock.acquire()
            return self

        def __exit__(self, *_args) -> None:
            self._lock.release()

        def acquire(self, blocking: bool = True):
            return self._lock.acquire(blocking=blocking)

        def release(self) -> None:
            self._lock.release()

    db_mod._session_lock = _ObservedRLock()
    db = db_mod.SqliteDB()
    db_mod._get_session_factory()

    def hold_session() -> None:
        with db.session():
            session_active.set()
            if not allow_session_exit.wait(timeout=10):
                raise RuntimeError("reset coordination timed out")

    def reset_database() -> None:
        try:
            db_mod.reset_for_tests()
        finally:
            reset_finished.set()

    holder_thread = Thread(target=hold_session, name="session-holder")
    reset_thread = Thread(target=reset_database, name="database-reset")
    holder_thread.start()
    assert session_active.wait(timeout=5)
    reset_thread.start()
    try:
        assert reset_acquire_attempted.wait(timeout=2)
        assert reset_finished.is_set() is False
    finally:
        allow_session_exit.set()
        holder_thread.join(timeout=5)
        reset_thread.join(timeout=5)
        db_mod._session_lock = original_lock

    assert not holder_thread.is_alive()
    assert not reset_thread.is_alive()
    assert reset_finished.is_set()
    assert db_mod._engine is None
