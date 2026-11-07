"""Both real SQLite plugins honor the multi-statement transaction contract."""

import asyncio

import pytest
from sqlalchemy import text

from agentclaw.community.di.config_community import CommunityDatabaseConfig
from agentclaw.community.di.local_skill_security_config import LocalSkillSecurityObservationConfig
from agentclaw.community.di.modules.infrastructure.community.database import CommunityDatabaseModule
from agentclaw.community.plugins.community.sqlite_database import CommunitySqliteDatabase
from agentclaw.community.plugins.local import database as local_database


@pytest.fixture(params=["community", "local"])
def database(request, tmp_path):
    if request.param == "community":
        plugin = CommunityDatabaseModule().database(
            CommunityDatabaseConfig(url=f"sqlite:///{tmp_path}/transactions.db"),
            LocalSkillSecurityObservationConfig(enabled=False),
        )
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


@pytest.fixture
def community_pair(tmp_path):
    from sqlalchemy import event

    first = CommunitySqliteDatabase(f"sqlite:///{tmp_path}/concurrent.db")
    other = CommunitySqliteDatabase(first._url)

    @event.listens_for(other._engine, "connect")
    def reject_contending_writes_without_waiting(connection, _record):
        connection.execute("PRAGMA busy_timeout=0").close()

    asyncio.run(first.bootstrap())
    try:
        yield first, other
    finally:
        first._engine.dispose()
        other._engine.dispose()


def test_ordinary_reads_do_not_block_an_independent_writer(community_pair):
    first, other = community_pair
    with first.orm_session() as session:
        session.execute(text("SELECT count(*) FROM ac_bot_mcp_config")).scalar_one()
        with other.orm_session() as writer:
            writer.execute(text(
                "INSERT INTO ac_bot_mcp_config "
                "(bot_id, owner_id, server_code, config, env) "
                "VALUES ('reader-test', 'owner', 'mcp.reader', '{}', 'dev')"
            ))
        assert session.execute(text("SELECT count(*) FROM ac_bot_mcp_config")).scalar_one() == 1


def test_transactional_session_reserves_the_writer_before_application_reads(community_pair):
    from sqlalchemy.exc import OperationalError

    first, other = community_pair
    with first.transactional_orm_session() as session:
        session.execute(text("SELECT count(*) FROM ac_bot_mcp_config")).scalar_one()
        with pytest.raises(OperationalError, match="locked"):
            with other.transactional_orm_session():
                pytest.fail("A competing writer must not enter before the first UoW finishes")
