"""Transaction ownership and deterministic plugin context cleanup."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.database import DatabaseManager, TransactionRollbackError


@pytest.fixture
def database():
    events = []

    @contextmanager
    def scope():
        session = MagicMock()
        events.append(("enter", session))
        try:
            yield session
            events.append(("commit", session))
        except BaseException:
            events.append(("rollback", session))
            raise
        finally:
            events.append(("close", session))

    plugin = MagicMock()
    plugin.orm_session = scope
    manager = object.__new__(DatabaseManager)
    manager._plugin = plugin
    return manager, events


def test_plugin_owns_transaction_and_nested_scopes_join(database):
    manager, events = database
    with manager.scoped_orm_session() as outer:
        assert [name for name, _ in events] == ["enter"]
        with manager.scoped_orm_session() as nested:
            assert nested is outer
        with manager.orm_session() as repo:
            assert repo is outer
    assert [name for name, _ in events] == ["enter", "commit", "close"]
    outer.commit.assert_not_called()
    outer.rollback.assert_not_called()
    outer.close.assert_not_called()


@pytest.mark.parametrize("kind", ["repo", "scope"])
def test_caught_inner_error_marks_outer_rollback_only(database, kind):
    manager, events = database
    with pytest.raises(TransactionRollbackError):
        with manager.scoped_orm_session():
            try:
                scope = (
                    manager.orm_session
                    if kind == "repo"
                    else manager.scoped_orm_session
                )
                with scope():
                    raise ValueError("inner failed")
            except ValueError:
                pass
    assert [name for name, _ in events] == ["enter", "rollback", "close"]
    with manager.orm_session():
        pass
    assert [name for name, _ in events][-3:] == ["enter", "commit", "close"]


def test_uncaught_error_rolls_back_and_clears_context(database):
    manager, events = database
    with pytest.raises(ValueError, match="failed"):
        with manager.scoped_orm_session():
            raise ValueError("failed")
    assert [name for name, _ in events] == ["enter", "rollback", "close"]
    assert manager._transaction.get() is None


def test_normal_calls_are_independent_even_when_nested(database):
    manager, events = database
    with manager.orm_session() as outer:
        with manager.orm_session() as inner:
            assert outer is not inner
    assert len([name for name, _ in events if name == "enter"]) == 2


def test_two_threads_never_join_each_others_transactions(database):
    manager, _ = database
    barrier = Barrier(2)

    def run():
        with manager.scoped_orm_session() as owned:
            barrier.wait(timeout=5)
            with manager.orm_session() as joined:
                assert joined is owned
            return owned

    with ThreadPoolExecutor(max_workers=2) as executor:
        left, right = list(executor.map(lambda _: run(), range(2)))
    assert left is not right


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["thread", "task"])
async def test_inherited_transaction_context_rejects_other_execution_owner(
    database, kind
):
    manager, events = database

    def access():
        with manager.orm_session():
            pytest.fail("must not share a Session across execution owners")

    async def task_access():
        access()

    with manager.scoped_orm_session() as owned:
        with pytest.raises(RuntimeError, match="cannot cross"):
            if kind == "thread":
                await asyncio.to_thread(access)
            else:
                await asyncio.create_task(task_access())
        with manager.orm_session() as local:
            assert local is owned
    assert [name for name, _ in events] == ["enter", "commit", "close"]


def test_commit_failure_closes_plugin_scope_and_clears_transaction(database):
    manager, _ = database
    events = []

    @contextmanager
    def failing_commit():
        try:
            yield MagicMock()
            events.append("commit")
            raise RuntimeError("commit failed")
        except BaseException:
            events.append("rollback")
            raise
        finally:
            events.append("close")

    manager._plugin.orm_session = failing_commit
    with pytest.raises(RuntimeError, match="commit failed"):
        with manager.scoped_orm_session():
            with manager.orm_session():
                pass
    assert events == ["commit", "rollback", "close"]
    assert manager._transaction.get() is None
