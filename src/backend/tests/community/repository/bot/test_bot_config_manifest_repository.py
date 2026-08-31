"""Unit tests for BotConfigManifestRepository.

Exercised against in-memory SQLite — the same single ORM body that runs on prod
OceanBase, so the UNIQUE guard and the whole-replace behavior are tested against
a real database rather than a mock.

The document round-trip tests are load-bearing, not decoration: the stored
document's script body is later executed, so a round-trip that normalizes or
re-quotes anything ("{token}", "$(id)"). would break invisibly at apply time.
"""
import json

import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.bot.config_manifest import (
    BotConfigManifestRepository,
    _manifest_key,
)
# Imported for side effect: registers BotConfigManifestModel on Base.metadata
# so create_all() builds the ac_bot_config_manifest table.
from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
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
    return BotConfigManifestRepository(InMemorySqliteDB(engine))


def _document() -> str:
    """A representative v1 document with a hostile script body inside."""
    return json.dumps(
        {
            "schema_version": 1,
            "script": {"body": "#!/bin/bash\necho '$(id)' \"X\" {token}\n"},
            "manifest": {"identity": [], "skills": [], "resources": [], "mcp": []},
        }
    )


# --- table creation ---------------------------------------------------------


def test_table_is_created_on_a_clean_sqlite_boot(repo):
    """Guards the eager import in ``core/schema.py::import_all_models``.

    Without the model registered on Base.metadata, the first request would hit
    "no such table".
    """
    assert repo.get(env="dev", entity_id="ent", bot_id="bot") is None


# --- get --------------------------------------------------------------------


def test_get_returns_none_when_never_set(repo):
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is None


def test_get_is_scoped_by_env_entity_and_bot(repo):
    repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=_document(),
        size_bytes=10,
        modifier="u1",
    )
    assert repo.get(env="dev", entity_id="ent_b", bot_id="bot_1") is None
    assert repo.get(env="prod", entity_id="ent_a", bot_id="bot_1") is None
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is not None


# --- upsert -----------------------------------------------------------------


def test_upsert_inserts_and_returns_record(repo):
    document = _document()
    rec = repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=document,
        size_bytes=len(document.encode()),
        modifier="u1",
    )
    assert rec.id is not None
    assert rec.schema_version == 1
    assert rec.document == document
    assert rec.gmt_modified is not None  # server-generated, not client-supplied


def test_upsert_replaces_whole_document_rather_than_inserting_a_second_row(repo):
    first = _document()
    second = json.dumps(
        {
            "schema_version": 1,
            "sources": {"content": {"git": "https://git/x.git", "ref": "v2"}},
        }
    )
    repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=first,
        size_bytes=7,
        modifier="u1",
    )
    rec = repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=second,
        size_bytes=11,
        modifier="u2",
    )
    assert rec.document == second
    # One row, not two — the read would raise MultipleResultsFound otherwise.
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1").document == second


def test_upsert_round_trips_the_document_byte_exact(repo):
    """The document is storage, not normalization: $(id)/quotes/{token} inside a
    script body must survive verbatim — that body is later executed."""
    document = _document()
    repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=document,
        size_bytes=len(document.encode()),
        modifier="u1",
    )
    stored = repo.get(env="dev", entity_id="ent_a", bot_id="bot_1").document
    assert stored == document
    body = json.loads(stored)["script"]["body"]
    assert body == "#!/bin/bash\necho '$(id)' \"X\" {token}\n"


# --- delete -----------------------------------------------------------------


def test_delete_removes_the_row(repo):
    repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=_document(),
        size_bytes=7,
        modifier="u1",
    )
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is True
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is None


def test_delete_is_idempotent(repo):
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is False


def test_delete_then_reinsert_works(repo):
    """A hard delete must leave nothing behind for the UNIQUE key to trip on."""
    repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=_document(),
        size_bytes=7,
        modifier="u1",
    )
    repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1")
    rec = repo.upsert(
        env="dev",
        entity_id="ent_a",
        bot_id="bot_1",
        schema_version=1,
        document=_document(),
        size_bytes=7,
        modifier="u2",
    )
    assert rec.modifier == "u2"


# --- key surrogate ----------------------------------------------------------


def test_the_key_separates_ids_that_would_concatenate_alike():
    """Length-prefixed, so ("a","bc") and ("ab","c") cannot collide — the
    collision would hand one bot's declared script to another bot."""
    assert _manifest_key(env="dev", entity_id="a", bot_id="bc") != _manifest_key(
        env="dev", entity_id="ab", bot_id="c"
    )
    assert len(_manifest_key(env="dev", entity_id="e", bot_id="b")) == 64


def test_every_read_filters_on_the_indexed_surrogate(repo):
    """Reads must key on ``manifest_key``, the table's only index — a plain
    round-trip passes either way, so the emitted SQL is asserted instead."""
    statements: list[str] = []

    from sqlalchemy import event

    engine = repo._db.orm_session().__enter__().get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    try:
        repo.upsert(
            env="dev",
            entity_id="ent",
            bot_id="bot",
            schema_version=1,
            document=_document(),
            size_bytes=7,
            modifier="alice",
        )
        statements.clear()
        repo.get(env="dev", entity_id="ent", bot_id="bot")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert selects, "expected the read to emit a SELECT"
    assert any("manifest_key" in s for s in selects), (
        f"read did not filter on the indexed surrogate: {selects}"
    )
