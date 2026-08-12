"""Unit tests for BotStartupScriptRepository.

Exercised against in-memory SQLite — the same single ORM body that runs on prod
OceanBase, so the UNIQUE guard and the upsert-not-duplicate behavior are tested
against a real database rather than a mock.
"""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.bot.startup_script import (
    BotStartupScriptRepository,
)
# Imported for side effect: registers BotStartupScriptModel on Base.metadata
# so create_all() builds the ac_bot_startup_script table.
from agentclaw.community.core.bot_startup_script.repository.models import (  # noqa: F401
    BotStartupScriptModel,
)


class InMemorySqliteDB:
    """In-memory SQLite DB for unit testing."""

    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def repo():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return BotStartupScriptRepository(InMemorySqliteDB(engine))


# --- table creation ---------------------------------------------------------

def test_table_is_created_on_a_clean_sqlite_boot(repo):
    """A clean create_all() must emit ac_bot_startup_script.

    Guards the side-effect import in plugins/local/database.py: without the
    model registered on Base.metadata, the first request would hit
    "no such table".
    """
    assert repo.get(env="dev", entity_id="ent", bot_id="bot") is None


# --- get --------------------------------------------------------------------

def test_get_returns_none_when_never_set(repo):
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is None


def test_get_is_scoped_by_env_entity_and_bot(repo):
    repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="echo a", size_bytes=6, modifier="u1",
    )
    # Same bot id, different entity or env, is a different row.
    assert repo.get(env="dev", entity_id="ent_b", bot_id="bot_1") is None
    assert repo.get(env="prod", entity_id="ent_a", bot_id="bot_1") is None
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is not None


# --- upsert -----------------------------------------------------------------

def test_upsert_inserts_and_returns_record(repo):
    rec = repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="echo hello", size_bytes=10, modifier="u1",
    )
    assert rec.id is not None
    assert rec.script == "echo hello"
    assert rec.size_bytes == 10
    assert rec.modifier == "u1"
    assert rec.gmt_modified is not None  # server-generated, not client-supplied


def test_upsert_replaces_body_rather_than_inserting_a_second_row(repo):
    repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="old", size_bytes=3, modifier="u1",
    )
    rec = repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="new body", size_bytes=8, modifier="u2",
    )
    assert rec.script == "new body"
    assert rec.size_bytes == 8
    assert rec.modifier == "u2"
    # One row, not two — the read would raise MultipleResultsFound otherwise.
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1").script == "new body"


def test_upsert_preserves_a_body_with_shell_metacharacters(repo):
    """The repository stores bytes; it must not normalize or escape anything."""
    body = "#!/bin/bash\necho '$(id)' \"HOOK_SCRIPT_EOF\" {token}\n"
    repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script=body, size_bytes=len(body.encode()), modifier="u1",
    )
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1").script == body


# --- delete -----------------------------------------------------------------

def test_delete_removes_the_row(repo):
    repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="echo a", size_bytes=6, modifier="u1",
    )
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is True
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is None


def test_delete_is_idempotent(repo):
    """Clearing an absent script succeeds — no tombstone, no error."""
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is False
    repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="echo a", size_bytes=6, modifier="u1",
    )
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is True
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is False


def test_delete_then_reinsert_works(repo):
    """A hard delete must leave nothing behind for the UNIQUE key to trip on."""
    repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="first", size_bytes=5, modifier="u1",
    )
    repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1")
    rec = repo.upsert(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        script="second", size_bytes=6, modifier="u1",
    )
    assert rec.script == "second"
