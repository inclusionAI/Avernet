"""Unit tests for ``plugins/local/database.py``.

Pins the in-memory-only contract: file-backed SQLite has been removed,
``DATABASE_URL`` is no longer consulted, ``StaticPool`` shares one
connection across checkouts so seeded rows are visible, and
``reset_for_tests()`` swaps in a fresh engine.
"""
from __future__ import annotations

from threading import Event, Thread

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
    first_entered = Event()
    allow_first_exit = Event()
    second_attempted = Event()
    second_entered = Event()
    errors: list[BaseException] = []

    def hold_session() -> None:
        try:
            with db.session():
                first_entered.set()
                if not allow_first_exit.wait(timeout=5):
                    raise RuntimeError("session lock coordination timed out")
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    def enter_orm_session() -> None:
        try:
            second_attempted.set()
            with db.orm_session():
                second_entered.set()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    first_thread = Thread(target=hold_session)
    second_thread = Thread(target=enter_orm_session)
    first_thread.start()
    assert first_entered.wait(timeout=5)
    second_thread.start()
    assert second_attempted.wait(timeout=5)
    entered_while_first_active = second_entered.wait(timeout=0.25)
    allow_first_exit.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert errors == []
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert entered_while_first_active is False
    assert second_entered.is_set()
