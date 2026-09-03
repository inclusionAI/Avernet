"""Unit tests for BotCliToolRepository (W9).

Exercised against in-memory SQLite — the same single ORM body that runs on prod
OceanBase, so the UNIQUE guard, the upsert-not-duplicate behavior and the
deterministic ordering are tested against a real database rather than a mock.
"""
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
def repo():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return BotCliToolRepository(InMemorySqliteDB(engine))


def _install(repo, *, name="mycli", digest="sha256:aa", subpath=None, **over):
    fields = dict(
        env="dev",
        entity_id="ent",
        bot_id="bot",
        name=name,
        source="https://example.com/mycli",
        digest=digest,
        subpath=subpath,
        md5="9f" * 16,
        size_bytes=1024,
        version="1.4.2",
        oss_key=f"tools/bot/{name}",
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


def test_upsert_touches_gmt_modified_even_when_nothing_changed(repo):
    """SQLAlchemy emits no UPDATE when every assigned value equals the stored
    one, so ``onupdate`` never fires and a re-install would show the previous
    write's timestamp. The repository force-stamps it."""
    first = _install(repo)
    time.sleep(1.1)
    again = _install(repo)
    assert again.gmt_modified > first.gmt_modified


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
    keys = repo.delete_all(env="dev", entity_id="ent", bot_id="bot")
    assert sorted(keys) == ["tools/bot/a", "tools/bot/b"]
    assert repo.list(env="dev", entity_id="ent", bot_id="bot") == []


def test_delete_all_leaves_other_bots_alone(repo):
    _install(repo, name="a")
    _install(repo, name="a", bot_id="other")
    repo.delete_all(env="dev", entity_id="ent", bot_id="bot")
    assert len(repo.list(env="dev", entity_id="ent", bot_id="other")) == 1


def test_delete_all_on_a_bot_with_no_tools_returns_empty(repo):
    assert repo.delete_all(env="dev", entity_id="ent", bot_id="bot") == []
