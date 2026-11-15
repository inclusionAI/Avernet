"""Catalog join is equivalent to the existing scoped first page, minus COUNT."""

import pytest
from sqlalchemy import event

from tests.community.repository.bot.test_stage_catalog import catalog as catalog, bot, publish
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def test_join_preserves_publication_fields_and_uses_one_fewer_query(catalog):
    repo, db = catalog
    live = bot(repo, "live")
    publish(db, live, bindings={"online": 71})
    bot(repo, "personal", kind="personal")
    pairs = [("live", "owner"), ("personal", "owner")]
    with db.orm_session() as session:
        engine = session.get_bind()
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        _, expected = repo.list_bots_by_owner_bot_pairs(pairs, page=1, page_size=len(pairs))
        before = len(statements)
        statements.clear()
        actual = repo.list_catalog_bots_by_owner_bot_pairs(pairs)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert actual == expected
    assert len(statements) == before - 1
    assert not any("count(" in sql.lower() for sql in statements)
    assert next(row for row in actual if row["bot_id"] == "live")["online_available"] is True


def test_catalog_join_keeps_scope_deletion_duplicates_and_newest_limit(catalog):
    repo, db = catalog
    with avernet_tenant_scope("tenant-a"):
        bot(repo, "shared", owner="a", kind="personal")
        repo.insert({"bot_id": "shared", "entity_id": "other-entity", "entity_type": "staff", "owner_id": "a",
                     "creator_id": "a", "bot_name": "shared", "bot_type": "personal", "status": "ACTIVE"})
        deleted = bot(repo, "deleted", owner="a")
        other_env = bot(repo, "other-env", owner="a")
        with db.orm_session() as session:
            deleted_model = session.get(repo.Model, deleted["id"])
            deleted_model.is_delete = 1
            session.get(repo.Model, other_env["id"]).env = "another-env"
    with avernet_tenant_scope("tenant-b"):
        bot(repo, "shared", owner="a", kind="personal")
    with avernet_tenant_scope("tenant-a"):
        pairs = [("shared", "a"), ("deleted", "a"), ("other-env", "a")]
        expected = repo.list_bots_by_owner_bot_pairs(pairs, page_size=len(pairs))[1]
        assert repo.list_catalog_bots_by_owner_bot_pairs(pairs) == expected
        one = repo.list_catalog_bots_by_owner_bot_pairs([("shared", "a")])
        assert len(one) == 1
        assert one[0]["id"] == expected[0]["id"]
        assert repo.list_catalog_bots_by_owner_bot_pairs([("shared", "wrong-owner")]) == []
    assert repo.list_catalog_bots_by_owner_bot_pairs([]) == []
    with pytest.raises(ValueError, match="100"):
        repo.list_catalog_bots_by_owner_bot_pairs([("bot", "owner")] * 101)
