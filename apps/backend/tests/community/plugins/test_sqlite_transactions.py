"""Both real SQLite plugins honor the multi-statement transaction contract."""

import asyncio

import pytest
from sqlalchemy import text

from agentclaw.community.plugins.community.database import CommunityDatabase
from agentclaw.community.plugins.local import database as local_database


@pytest.fixture(params=["community", "local"])
def database(request, tmp_path):
    if request.param == "community":
        plugin = CommunityDatabase(f"sqlite:///{tmp_path}/transactions.db")
    else:
        local_database.reset_for_tests()
        plugin = local_database.SqliteDB()
    asyncio.run(plugin.bootstrap())
    with plugin.orm_session() as session:
        session.execute(text("CREATE TABLE transaction_parent (id INTEGER PRIMARY KEY)"))
        session.execute(text(
            "CREATE TABLE transaction_child (id INTEGER PRIMARY KEY, "
            "parent_id INTEGER REFERENCES transaction_parent(id) ON DELETE CASCADE)"
        ))
    try:
        yield plugin
    finally:
        if request.param == "community":
            plugin._engine.dispose()
        else:
            local_database.reset_for_tests()


def test_outer_failure_rolls_back_the_first_released_savepoint(database):
    with pytest.raises(RuntimeError, match="write after savepoint failed"):
        with database.transactional_orm_session() as session:
            with session.begin_nested():
                session.execute(text("INSERT INTO transaction_parent (id) VALUES (1)"))
            raise RuntimeError("write after savepoint failed")

    with database.session() as session:
        assert session.execute(text("SELECT id FROM transaction_parent")).all() == []


def test_successful_savepoint_is_committed_and_foreign_keys_still_cascade(database):
    with database.transactional_orm_session() as session:
        with session.begin_nested():
            session.execute(text("INSERT INTO transaction_parent (id) VALUES (1)"))
            session.execute(text("INSERT INTO transaction_child (id, parent_id) VALUES (2, 1)"))

    with database.session() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert session.execute(text("SELECT id FROM transaction_child")).scalars().all() == [2]
    with database.transactional_orm_session() as session:
        session.execute(text("DELETE FROM transaction_parent WHERE id = 1"))
    with database.session() as session:
        assert session.execute(text("SELECT id FROM transaction_child")).all() == []
