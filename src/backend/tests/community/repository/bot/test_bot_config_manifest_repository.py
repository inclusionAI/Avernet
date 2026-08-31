"""Unit tests for BotConfigManifestRepository.

Exercised against in-memory SQLite — the same single ORM body that runs on prod
OceanBase, so the UNIQUE guard and the upsert-not-duplicate behavior are tested
against a real database rather than a mock.
"""
import pytest
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.bot.config_manifest import (
    BotConfigManifestRepository,
)
# Imported for side effect: registers BotConfigManifestModel on Base.metadata
# so create_all() builds the ac_bot_config_manifest table.
from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
)


_DOC = "schema_version: 1\nmanifest:\n  skills: []\n"


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


def _write(repo, *, env="dev", entity_id="ent_a", bot_id="bot_1", document=_DOC,
           schema_version=1, modifier="u1"):
    return repo.upsert(
        env=env,
        entity_id=entity_id,
        bot_id=bot_id,
        document=document,
        size_bytes=len(document.encode("utf-8")),
        schema_version=schema_version,
        modifier=modifier,
    )


# --- table creation ---------------------------------------------------------

def test_table_is_created_on_a_clean_sqlite_boot(repo):
    """A clean create_all() must emit ac_bot_config_manifest.

    Guards the side-effect import in ``core/schema.py``: without the model
    registered on Base.metadata, the first request would hit "no such table".
    """
    assert repo.get(env="dev", entity_id="ent", bot_id="bot") is None


# --- get --------------------------------------------------------------------

def test_get_returns_none_when_never_set(repo):
    """Absent is not an error — the service turns this into an empty document."""
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is None


def test_get_is_scoped_by_env_entity_and_bot(repo):
    _write(repo)
    assert repo.get(env="dev", entity_id="ent_b", bot_id="bot_1") is None
    assert repo.get(env="prod", entity_id="ent_a", bot_id="bot_1") is None
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is not None


# --- upsert -----------------------------------------------------------------

def test_upsert_inserts_and_returns_record(repo):
    record = _write(repo)
    assert record.id is not None
    assert record.document == _DOC
    assert record.size_bytes == len(_DOC.encode("utf-8"))
    assert record.schema_version == 1
    assert record.modifier == "u1"
    assert record.gmt_modified is not None  # server-generated


def test_upsert_replaces_the_document_rather_than_inserting_a_second_row(repo):
    _write(repo, document="schema_version: 1\n")
    record = _write(repo, document=_DOC, modifier="u2")
    assert record.document == _DOC
    assert record.modifier == "u2"
    # One row, not two — the read would raise MultipleResultsFound otherwise.
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1").document == _DOC


def test_upsert_round_trips_a_script_body_byte_for_byte(repo):
    """The whole reason the document is stored as text and never re-serialised.

    Quoting, ``$(...)``, ``{token}`` and the block scalar's indentation are the
    script's meaning; a YAML round trip would preserve the value and not these
    bytes.
    """
    document = (
        "schema_version: 1\n"
        "script:\n"
        "  body: |\n"
        "    #!/bin/bash\n"
        "    echo '$(id)' \"HOOK_SCRIPT_EOF\" {token}\n"
        "    printf '%s\\n' \"a  b\"\n"
    )
    _write(repo, document=document)
    stored = repo.get(env="dev", entity_id="ent_a", bot_id="bot_1").document
    assert stored == document
    assert stored.encode("utf-8") == document.encode("utf-8")


# --- key --------------------------------------------------------------------

def test_rows_for_colliding_component_boundaries_stay_separate(repo):
    """Two logical keys that a *joined* key would conflate must stay apart.

    ``(entity_id="a\\0b", bot_id="c")`` and ``(entity_id="a", bot_id="b\\0c")``
    collide under any single-separator encoding, and nothing validates
    ``bot_id`` or ``entity_id`` against control characters. The key here is the
    columns themselves, so the question cannot arise — which is the argument for
    carrying the logical key directly instead of encoding it into one value.
    This test is what keeps that true if the key is ever encoded again.
    """
    _write(repo, entity_id="a\x00b", bot_id="c", document="schema_version: 1\n")
    _write(repo, entity_id="a", bot_id="b\x00c", document=_DOC)
    assert repo.get(env="dev", entity_id="a\x00b", bot_id="c").document == (
        "schema_version: 1\n"
    )
    assert repo.get(env="dev", entity_id="a", bot_id="b\x00c").document == _DOC


# --- delete -----------------------------------------------------------------

def test_delete_removes_the_row(repo):
    _write(repo)
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is True
    assert repo.get(env="dev", entity_id="ent_a", bot_id="bot_1") is None


def test_delete_is_idempotent(repo):
    """Clearing an absent manifest succeeds — no tombstone, no error."""
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is False
    _write(repo)
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is True
    assert repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1") is False


def test_delete_then_reinsert_works(repo):
    """A hard delete must leave nothing for the UNIQUE key to trip on."""
    _write(repo, document="schema_version: 1\n")
    repo.delete(env="dev", entity_id="ent_a", bot_id="bot_1")
    assert _write(repo, document=_DOC).document == _DOC


# --- races ------------------------------------------------------------------

def test_upsert_retries_as_an_update_when_the_insert_loses_a_race(monkeypatch):
    """Two first writes for one key can both read ``None`` and both insert; the
    UNIQUE constraint then fails one of them.

    That loser was making a perfectly valid replace, so a 500 is the wrong
    answer — it retries and takes the update branch.
    """
    from sqlalchemy.exc import IntegrityError

    from agentclaw.community.core.repository.implementations.bot import (
        config_manifest as mod,
    )

    repo = mod.BotConfigManifestRepository.__new__(mod.BotConfigManifestRepository)
    calls = []

    def _once(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return "replaced"

    monkeypatch.setattr(repo, "_upsert_once", _once)

    result = mod.BotConfigManifestRepository.upsert(
        repo,
        env="dev",
        entity_id="ent",
        bot_id="bot",
        document=_DOC,
        size_bytes=len(_DOC.encode("utf-8")),
        schema_version=1,
        modifier="u1",
    )

    assert result == "replaced"
    assert len(calls) == 2, "the conflict must be retried exactly once"
    assert calls[0] == calls[1], "the retry must carry the same write"


def test_upsert_does_not_retry_forever(monkeypatch):
    """A second conflict is not a race — it propagates rather than looping."""
    from sqlalchemy.exc import IntegrityError

    from agentclaw.community.core.repository.implementations.bot import (
        config_manifest as mod,
    )

    repo = mod.BotConfigManifestRepository.__new__(mod.BotConfigManifestRepository)
    calls = []

    def _once(**kwargs):
        calls.append(kwargs)
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(repo, "_upsert_once", _once)

    with pytest.raises(IntegrityError):
        mod.BotConfigManifestRepository.upsert(
            repo,
            env="dev",
            entity_id="ent",
            bot_id="bot",
            document=_DOC,
            size_bytes=len(_DOC.encode("utf-8")),
            schema_version=1,
            modifier="u1",
        )
    assert len(calls) == 2


# --- tenant isolation -------------------------------------------------------


def test_two_tenants_sharing_a_bot_id_cannot_read_or_overwrite_each_other(tmp_path):
    """``ac_bots`` is tenant-scoped, so a ``bot_id`` is unique only *within* a
    tenant — legacy ``default`` bots carry documented cross-tenant collision on
    that identifier. Without the tenant in the key these two writes land on one
    row, and either tenant's manifest decides what the other's bot is configured
    with.

    Uses a file-backed SQLite so the guard runs against a real connection, and
    drives the same tenant scope the request middleware binds.
    """
    from sqlalchemy import create_engine

    from agentclaw.community.core.base import Base
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    engine = create_engine(
        f"sqlite:///{tmp_path / 'manifests.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    repo = BotConfigManifestRepository(InMemorySqliteDB(engine))

    shared = dict(env="dev", entity_id="default", bot_id="shared-bot-id")

    with avernet_tenant_scope("tenant-a"):
        _write(repo, document="schema_version: 1\n# tenant a\n", **shared)
    with avernet_tenant_scope("tenant-b"):
        # B cannot see A's manifest …
        assert repo.get(**shared) is None
        _write(repo, document="schema_version: 1\n# tenant b\n", **shared)

    with avernet_tenant_scope("tenant-a"):
        assert repo.get(**shared).document == "schema_version: 1\n# tenant a\n"
    with avernet_tenant_scope("tenant-b"):
        assert repo.get(**shared).document == "schema_version: 1\n# tenant b\n"


def test_a_delete_in_one_tenant_leaves_the_other_alone(tmp_path):
    from sqlalchemy import create_engine

    from agentclaw.community.core.base import Base
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    engine = create_engine(
        f"sqlite:///{tmp_path / 'manifests.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    repo = BotConfigManifestRepository(InMemorySqliteDB(engine))
    shared = dict(env="dev", entity_id="default", bot_id="shared-bot-id")

    with avernet_tenant_scope("tenant-a"):
        _write(repo, **shared)
    with avernet_tenant_scope("tenant-b"):
        _write(repo, **shared)
        assert repo.delete(**shared) is True

    with avernet_tenant_scope("tenant-a"):
        assert repo.get(**shared) is not None
