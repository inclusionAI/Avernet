"""Unit tests for BotCliToolRepository (W9).

Exercised against in-memory SQLite — the same single ORM body that runs on prod
OceanBase, so the UNIQUE guard, the upsert-not-duplicate behavior and the
deterministic ordering are tested against a real database rather than a mock.
"""
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

# Imported for side effect: registers BotCliToolModel on Base.metadata so
# create_all() builds the ac_bot_cli_tool table.
from agentclaw.community.core.bot_config_manifest.cli_tools.models import (  # noqa: F401
    INSTALLED_BY_MANIFEST,
    BotCliToolModel,
)
from agentclaw.community.core.repository.implementations.bot.cli_tool import (
    BotCliToolRepository,
    _tool_key,
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
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def repo(db_engine):
    return BotCliToolRepository(InMemorySqliteDB(db_engine))


def _install(repo, *, name="mycli", digest="sha256:aa", subpath=None, **over):
    bot_id = over.pop("bot_id", "bot")
    fields = dict(
        env="dev",
        entity_id="ent",
        bot_id=bot_id,
        name=name,
        source="https://example.com/mycli",
        digest=digest,
        subpath=subpath,
        md5="9f" * 16,
        size_bytes=1024,
        version="1.4.2",
        # Scoped to the bot, as the store's real key layout is. delete_all
        # hands these keys to a caller as safe to delete, and that is only true
        # because no two bots share an object.
        oss_key=f"tools/{bot_id}/{name}",
        installed_by=INSTALLED_BY_MANIFEST,
        modifier="u1",
    )
    fields.update(over)
    return repo.upsert(**fields)


# --- table creation ---------------------------------------------------------

def test_table_is_created_on_a_clean_sqlite_boot(repo):
    """Guards the side-effect import in core/schema.py: without the model on
    Base.metadata the first request would hit "no such table"."""
    assert repo.list(env="dev", entity_id="ent", bot_id="bot") == []


# --- the surrogate key ------------------------------------------------------

def test_tool_key_is_injective_across_component_boundaries():
    """Length-prefixed, so a component containing the delimiter cannot forge
    another bot's key."""
    assert _tool_key(env="dev", entity_id="a:b", bot_id="c", name="d") != _tool_key(
        env="dev", entity_id="a", bot_id="b:c", name="d"
    )
    assert _tool_key(env="dev", entity_id="e", bot_id="b", name="ab") != _tool_key(
        env="dev", entity_id="e", bot_id="ba", name="b"
    )


def test_tool_key_separates_tools_of_the_same_bot():
    assert _tool_key(env="d", entity_id="e", bot_id="b", name="one") != _tool_key(
        env="d", entity_id="e", bot_id="b", name="two"
    )


# --- get / list -------------------------------------------------------------

def test_get_returns_none_when_never_installed(repo):
    assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="nope") is None


def test_get_returns_the_installed_tool(repo):
    _install(repo)
    got = repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli")
    assert got is not None
    assert got.name == "mycli"
    assert got.digest == "sha256:aa"
    assert got.oss_key == "tools/bot/mycli"
    assert got.installed_by == INSTALLED_BY_MANIFEST


def test_list_is_scoped_to_one_bot(repo):
    _install(repo, name="a")
    _install(repo, name="b", bot_id="other")
    names = [r.name for r in repo.list(env="dev", entity_id="ent", bot_id="bot")]
    assert names == ["a"]


def test_list_orders_by_code_point_not_db_collation(repo):
    """The composed artifact's ref list comes from this sequence and its
    byte-identity is asserted, so the order must not depend on the database's
    collation — SQLite is BINARY, OceanBase's default is case-insensitive."""
    for name in ("aws", "Zip", "mycli"):
        _install(repo, name=name)
    names = [r.name for r in repo.list(env="dev", entity_id="ent", bot_id="bot")]
    assert names == sorted(names)
    assert names == ["Zip", "aws", "mycli"]


# --- upsert -----------------------------------------------------------------

def test_upsert_replaces_rather_than_duplicating(repo):
    _install(repo, digest="sha256:old")
    _install(repo, digest="sha256:new")
    rows = repo.list(env="dev", entity_id="ent", bot_id="bot")
    assert len(rows) == 1
    assert rows[0].digest == "sha256:new"


def test_upsert_touches_gmt_modified_even_when_nothing_changed(repo, db_engine):
    """SQLAlchemy emits no UPDATE when every assigned value equals the stored
    one, so ``onupdate`` never fires and a re-install would show the previous
    write's timestamp. The repository force-stamps it.

    Asserted by back-dating the stored row and re-upserting identical values —
    no sleep, and it stays correct if the column ever gains sub-second
    precision.
    """
    _install(repo)
    stale = datetime(2020, 1, 1, 0, 0, 0)
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE ac_bot_cli_tool SET gmt_modified = :t"), {"t": stale}
        )
    again = _install(repo)  # byte-identical values
    assert again.gmt_modified > stale


def test_two_bots_may_share_a_tool_name(repo):
    _install(repo, name="mycli")
    _install(repo, name="mycli", bot_id="other")
    assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli")
    assert repo.get(env="dev", entity_id="ent", bot_id="other", name="mycli")


def test_convergence_key_is_digest_and_subpath(repo):
    """Same archive, different member, is a real change — keying on digest
    alone would report it unchanged and leave the old binary in place."""
    old = _install(repo, digest="sha256:same", subpath="bin/old")
    new = _install(repo, digest="sha256:same", subpath="bin/new")
    assert old.convergence_key != new.convergence_key


def test_version_does_not_affect_the_convergence_key(repo):
    a = _install(repo, version="1.0.0")
    b = _install(repo, version="2.0.0")
    assert a.convergence_key == b.convergence_key


# --- delete -----------------------------------------------------------------

def test_delete_is_idempotent(repo):
    _install(repo)
    assert repo.delete(env="dev", entity_id="ent", bot_id="bot", name="mycli") is True
    assert repo.delete(env="dev", entity_id="ent", bot_id="bot", name="mycli") is False
    assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli") is None


def test_delete_removes_only_the_named_tool(repo):
    _install(repo, name="keep")
    _install(repo, name="drop")
    repo.delete(env="dev", entity_id="ent", bot_id="bot", name="drop")
    assert [r.name for r in repo.list(env="dev", entity_id="ent", bot_id="bot")] == [
        "keep"
    ]


def test_delete_all_returns_the_oss_keys_it_removed(repo):
    """Returning keys rather than a count is what stops the objects being
    orphaned: oss_key lives only on these rows."""
    _install(repo, name="a")
    _install(repo, name="b")
    _install(repo, name="a", bot_id="other")  # another bot's object must survive
    keys = repo.delete_all(env="dev", entity_id="ent", bot_id="bot")
    assert sorted(keys) == ["tools/bot/a", "tools/bot/b"]
    # Never another bot's key: the store scopes every key to one bot, which is
    # what makes the returned keys safe for a caller to delete.
    assert "tools/other/a" not in keys
    assert repo.list(env="dev", entity_id="ent", bot_id="bot") == []


def test_delete_all_leaves_other_bots_alone(repo):
    _install(repo, name="a")
    _install(repo, name="a", bot_id="other")
    repo.delete_all(env="dev", entity_id="ent", bot_id="bot")
    assert len(repo.list(env="dev", entity_id="ent", bot_id="other")) == 1


def test_delete_all_on_a_bot_with_no_tools_returns_empty(repo):
    assert repo.delete_all(env="dev", entity_id="ent", bot_id="bot") == []


# --- scoping ----------------------------------------------------------------

def test_get_is_scoped_by_env_and_entity(repo):
    """``get`` resolves through the hashed surrogate while ``list`` filters on
    the raw columns, so the two share no code. If ``_tool_key`` ever dropped
    ``env``, a dev-pinned executable would be served to a prod bot."""
    _install(repo)
    assert repo.get(env="prod", entity_id="ent", bot_id="bot", name="mycli") is None
    assert repo.get(env="dev", entity_id="other", bot_id="bot", name="mycli") is None
    assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli") is not None


def test_delete_is_scoped_by_env_and_entity(repo):
    _install(repo)
    assert repo.delete(env="prod", entity_id="ent", bot_id="bot", name="mycli") is False
    assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli") is not None


# --- the upsert retry -------------------------------------------------------

def test_upsert_retries_once_when_an_insert_loses_a_race(repo, monkeypatch):
    """The read-then-insert is not atomic: two first writes for the same key
    can both see ``None`` and the UNIQUE constraint fails one. The loser is
    entitled to the update it was always going to make."""
    from sqlalchemy.exc import IntegrityError

    real = repo._upsert_once
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        return real(**kwargs)

    monkeypatch.setattr(repo, "_upsert_once", flaky)
    record = _install(repo)
    assert calls["n"] == 2
    assert record.name == "mycli"


def test_upsert_does_not_retry_forever_on_a_persistent_failure(repo, monkeypatch):
    """A NOT NULL violation raises the same class as a race. It must surface
    after exactly two attempts, not loop."""
    from sqlalchemy.exc import IntegrityError

    calls = {"n": 0}

    def always_fails(**kwargs):
        calls["n"] += 1
        raise IntegrityError("INSERT", {}, Exception("column is not null"))

    monkeypatch.setattr(repo, "_upsert_once", always_fails)
    with pytest.raises(IntegrityError):
        _install(repo)
    assert calls["n"] == 2


# --- tenant isolation -------------------------------------------------------

def test_tools_are_invisible_across_tenants(repo):
    """``_tool_key`` hashes only (env, entity_id, bot_id, name) — the tenant is
    carried as a separate column, so cross-tenant safety rests entirely on the
    guard's injected WHERE. A tool row names an executable that runs in a
    container, which is what makes this the hazard the DDL calls out.
    """
    with avernet_tenant_scope("tenant-a"):
        _install(repo, name="mycli", digest="sha256:a")
    with avernet_tenant_scope("tenant-b"):
        assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli") is None
        assert repo.list(env="dev", entity_id="ent", bot_id="bot") == []
    with avernet_tenant_scope("tenant-a"):
        assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli")


def test_one_tenant_cannot_delete_anothers_tool(repo):
    with avernet_tenant_scope("tenant-a"):
        _install(repo, name="mycli")
    with avernet_tenant_scope("tenant-b"):
        assert (
            repo.delete(env="dev", entity_id="ent", bot_id="bot", name="mycli")
            is False
        )
        assert repo.delete_all(env="dev", entity_id="ent", bot_id="bot") == []
    with avernet_tenant_scope("tenant-a"):
        assert repo.get(env="dev", entity_id="ent", bot_id="bot", name="mycli")
