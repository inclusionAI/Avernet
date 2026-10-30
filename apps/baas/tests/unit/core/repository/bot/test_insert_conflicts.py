"""Insert failures are classified only after plugin rollback/close completes."""

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from secbaas.community.core.repository.bot import (
    BotRecordConflictError,
    OrmBotRepository,
)


def _driver_error(kind):
    if kind.startswith("sqlite"):
        error = sqlite3.IntegrityError(kind)
        error.sqlite_errorcode = (
            sqlite3.SQLITE_CONSTRAINT_UNIQUE
            if kind == "sqlite_unique"
            else sqlite3.SQLITE_CONSTRAINT_NOTNULL
        )
        return error
    if kind == "mysql_duplicate":
        return Exception(1062, "Duplicate entry")
    if kind == "mysql_notnull":
        return Exception(1048, "Column cannot be null")
    return Exception("Unclassified persistence error")


@pytest.mark.parametrize("phase", ["flush", "commit"])
@pytest.mark.parametrize(
    "kind",
    ["sqlite_unique", "mysql_duplicate", "sqlite_notnull", "mysql_notnull", "other"],
)
def test_only_duplicates_become_conflicts_after_cleanup(phase, kind):
    failure = IntegrityError("INSERT", {}, _driver_error(kind))
    session = MagicMock()
    events = []

    def flush():
        events.append("flush")
        if phase == "flush":
            raise failure
        session.add.call_args.args[0].id = 42

    session.flush.side_effect = flush

    @contextmanager
    def scope():
        events.append("enter")
        try:
            yield session
            events.append("commit")
            if phase == "commit":
                raise failure
        except BaseException:
            events.append("rollback")
            raise
        finally:
            events.append("close")

    repo = OrmBotRepository(SimpleNamespace(orm_session=scope), MagicMock())
    duplicate = kind in {"sqlite_unique", "mysql_duplicate"}
    expected = BotRecordConflictError if duplicate else IntegrityError
    with pytest.raises(expected) as caught:
        repo.insert_bot(
            bot_uuid="bot-1",
            tenant="tenant",
            env="test",
            domain="test",
            creator="user",
            modifier="user",
            name="bot",
        )
    assert events == (
        ["enter", "flush", "rollback", "close"]
        if phase == "flush"
        else ["enter", "flush", "commit", "rollback", "close"]
    )
    if duplicate:
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure
