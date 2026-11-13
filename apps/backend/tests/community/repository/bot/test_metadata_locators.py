"""Metadata batching retains addressing rules without per-Bot SQL reads."""

from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import mysql

from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.bcn_metadata import resolve_metadata_pairs
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError, BotLookupAmbiguousError
from agentclaw.community.core.repository.implementations.bot.metadata_locators import metadata_locator_candidates
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.repository.bot.test_stage_catalog import catalog as catalog, bot, publish


def legacy_resolve(repo, pairs):
    """The pre-batching behavior is the compatibility oracle."""
    result = []
    for bot_id, locator in dict.fromkeys(pairs):
        try:
            row = BotAddressResolver(repo).resolve(bot_id=bot_id, legacy_owner_id=locator).bot
        except BotAddressNotFoundError:
            if bot_id == "default":
                continue
            row = repo.get_unique_by_id(bot_id)
            if row is None or bcn_bot_reference(row) != f"{bot_id}:{locator}":
                continue
        result.append((str(row["bot_id"]), str(row["owner_id"])))
    return list(dict.fromkeys(result))


@contextmanager
def select_statements(db):
    with db.orm_session() as session:
        engine = session.get_bind()
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def transferred(repo, db, name):
    row = bot(repo, name, owner="new-owner")
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == row["id"]).update({
            BotModel.entity_id: "stable-entity",
            BotModel.ext: '{"bcn_provider_bot_ref":"' + name + ':old-owner"}',
        })
    return row


@pytest.mark.parametrize("locator", ["new-owner", "stable-entity", "old-owner", "unrelated"])
def test_handoff_and_retained_reference_match_legacy(catalog, locator):
    repo, db = catalog
    transferred(repo, db, "service")
    pairs = [("service", locator), ("service", locator), ("missing", "owner")]
    assert resolve_metadata_pairs(repo, pairs) == legacy_resolve(repo, pairs)


def test_default_is_exact_and_duplicate_aliases_deduplicate(catalog):
    repo, db = catalog
    transferred(repo, db, "default")
    bot(repo, "default", owner="other-owner")
    pairs = [("default", value) for value in (
        "new-owner", "stable-entity", "old-owner", "other-owner", "missing",
    )]
    assert resolve_metadata_pairs(repo, pairs) == legacy_resolve(repo, pairs) == [
        ("default", "new-owner"), ("default", "other-owner"),
    ]


@pytest.mark.parametrize("locator", ["owner", "unknown"])
def test_ambiguity_is_rejected_like_legacy(catalog, locator):
    repo, db = catalog
    for index in range(4):
        row = bot(repo, "duplicate", owner=f"entity-{index}")
        with db.orm_session() as session:
            session.query(BotModel).filter(BotModel.id == row["id"]).update({BotModel.owner_id: "owner"})
    pairs = [("duplicate", locator)]
    assert len(repo.list_metadata_locator_candidates(pairs)[pairs[0]]) == 2
    for resolve in (legacy_resolve, resolve_metadata_pairs):
        with pytest.raises(BotLookupAmbiguousError):
            resolve(repo, pairs)


def test_direct_match_beats_bare_id_ambiguity_and_prioritizes_late_rows(catalog):
    repo, _ = catalog
    for index in range(5):
        bot(repo, "repeated", owner=f"owner-{index}")
    pairs = [("repeated", "owner-4"), ("repeated", "owner-0")]
    assert resolve_metadata_pairs(repo, pairs) == legacy_resolve(repo, pairs)


def test_unrelated_bad_json_does_not_break_a_direct_match(catalog):
    repo, db = catalog
    unrelated = bot(repo, "repeated", owner="unrelated")
    bot(repo, "repeated", owner="target")
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == unrelated["id"]).update({BotModel.ext: "{"})
    pairs = [("repeated", "target")]
    assert resolve_metadata_pairs(repo, pairs) == legacy_resolve(repo, pairs) == [("repeated", "target")]


@pytest.mark.parametrize("size", [1, 60, 100])
@pytest.mark.parametrize("historical", [False, True])
def test_full_metadata_pipeline_has_constant_query_count(catalog, size, historical):
    repo, db = catalog
    for index in range(size):
        source = transferred(repo, db, f"bot-{index}")
        publish(db, source, bindings={"online": 71}, version=3)
    locator = "old-owner" if historical else "stable-entity"
    pairs = [(f"bot-{index}", locator) for index in range(size)]
    with select_statements(db) as statements:
        resolved = resolve_metadata_pairs(repo, pairs)
        total, rows = repo.list_bots_by_owner_bot_pairs(resolved, page_size=100)
    assert len(statements) == 4, "one locator SELECT + COUNT + page + stage projection"
    assert total == len(rows) == size
    assert all(row["online_available"] and row["live_version"] == 3 for row in rows)
    assert all(row["owner_id"] == "new-owner" for row in rows)
    with select_statements(db) as old_statements:
        previous = legacy_resolve(repo, pairs)
        old_result = repo.list_bots_by_owner_bot_pairs(previous, page_size=100)
    assert old_result == (total, rows)
    assert len(old_statements) == size * (2 if historical else 1) + 3


def test_candidates_scope_tenant_env_and_deletion_before_ranking(catalog):
    repo, db = catalog
    with avernet_tenant_scope("other-tenant"):
        for index in range(3):
            bot(repo, "shared", owner=f"foreign-{index}")
    with avernet_tenant_scope("target-tenant"):
        deleted = bot(repo, "shared", owner="deleted")
        wrong_env = bot(repo, "shared", owner="wrong-env")
        with db.orm_session() as session:
            session.query(BotModel).filter(BotModel.id == deleted["id"]).update({BotModel.is_delete: 1})
            session.query(BotModel).filter(BotModel.id == wrong_env["id"]).update({BotModel.env: "other"})
        transferred(repo, db, "shared")
        pairs = [("shared", "old-owner"), ("shared", "foreign")]
        assert resolve_metadata_pairs(repo, pairs) == legacy_resolve(repo, pairs) == [("shared", "new-owner")]


def test_pagination_order_and_totals_are_unchanged(catalog):
    repo, db = catalog
    for name in ("a", "b", "c"):
        transferred(repo, db, name)
    pairs = [(name, "old-owner") for name in ("a", "missing", "b", "a", "c")]
    old, new = legacy_resolve(repo, pairs), resolve_metadata_pairs(repo, pairs)
    for page in (1, 2, 3):
        assert repo.list_bots_by_owner_bot_pairs(old, page=page, page_size=2) == \
            repo.list_bots_by_owner_bot_pairs(new, page=page, page_size=2)


def test_empty_batch_does_not_read_and_large_batch_is_rejected(catalog):
    repo, db = catalog
    with select_statements(db) as statements:
        assert resolve_metadata_pairs(repo, []) == []
    assert statements == []
    with pytest.raises(ValueError, match="at most 100"):
        repo.list_metadata_locator_candidates([(f"bot-{index}", "owner") for index in range(101)])


def test_mysql_query_is_bound_and_scoped_before_window_ranking(catalog, monkeypatch):
    repo, db = catalog
    from sqlalchemy.orm import Query

    statements = []
    original = Query.all

    def record(query):
        statements.append(query.statement.compile(dialect=mysql.dialect()))
        return original(query)

    monkeypatch.setattr(Query, "all", record)
    with db.orm_session() as session:
        metadata_locator_candidates(session, [("sql-bound-bot", "sql-bound-owner")])
    sql = str(statements[0])
    assert "row_number() OVER" in sql and "PARTITION BY" in sql
    assert "avernet_tenant" in sql and "is_delete" in sql and "env" in sql
    assert "sql-bound-bot" not in sql and "sql-bound-owner" not in sql
    assert "sql-bound-bot" in statements[0].params.values()
