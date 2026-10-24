"""Regression probes for current-selection scope and bounded query work."""

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.bot_management.services.bot_space_service import (
    BotSpaceService,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.plugin_api.models import BotModel


def move_to_new_space(db, scope):
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=2,
                space_code="new-team",
                space_type="TEAM",
                name="New team",
                env="dev",
                created_by="owner",
                updated_by="owner",
            )
        )
        session.add(
            SpaceMemberModel(
                space_id=2,
                user_id="owner",
                role="MEMBER",
                env="dev",
                created_by="owner",
            )
        )
    quota = MagicMock()
    quota.guard_add.side_effect = lambda **_: nullcontext()
    result = BotSpaceService(
        BotRepository(db), SpaceAccessService(SpaceRepository(db)), quota
    ).change_space(
        bot_id=scope.bot_id, owner_id=scope.owner_id, space_id=2, env=scope.env
    )
    assert result.changed and result.bot["space_id"] == 2


def set_enabled(service, scope, enabled=True, revision=0, kb_id="kb"):
    return service.set_enabled(
        scope=scope,
        actor_id="owner",
        kb_id=kb_id,
        enabled=enabled,
        expected_revision=revision,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_native_space_move_can_replace_hidden_selection(knowledge, enabled):
    db, repo, service, scope = knowledge
    assert set_enabled(service, scope)["revision"] == 1
    move_to_new_space(db, scope)
    assert service.list_bindings(scope=scope, actor_id="owner")["items"] == []
    assert repo.list_enabled_current(scope) == ()
    calls = []

    def catalog(**kwargs):
        calls.append(kwargs)
        assert kwargs["space_id"] == 2
        return ("kb",)

    service.catalog.list_authorized = catalog
    result = set_enabled(service, scope, enabled=enabled)
    assert result["enabled"] is enabled and result["revision"] == 2
    assert len(calls) == int(enabled)
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        set_enabled(service, scope, enabled=False, revision=1)
    with db.orm_session() as session:
        row = session.query(Binding).one()
        assert row.space_id == 2 and row.revision == 2
    assert service.list_bindings(scope=scope, actor_id="owner")["items"] == [result]


def test_old_space_selections_do_not_consume_new_space_limit(knowledge):
    db, repo, service, scope = knowledge
    with db.orm_session() as session:
        session.add_all(
            [
                Binding(
                    env="dev",
                    bot_pk=1,
                    space_id=1,
                    kb_id=f"old-{i}",
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
                for i in range(100)
            ]
        )
    move_to_new_space(db, scope)
    assert set_enabled(service, scope)["enabled"]
    assert repo.list_enabled_current(scope) == ("kb",)


def test_space_move_requires_new_catalog_authorization(knowledge):
    db, repo, service, scope = knowledge
    set_enabled(service, scope)
    move_to_new_space(db, scope)
    service.catalog.authorized.clear()
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_NOT_GRANTED"):
        set_enabled(service, scope)
    assert repo.list_enabled_current(scope) == ()


def test_disabling_unknown_selections_never_accumulates_rows(knowledge):
    db, _, service, scope = knowledge

    def unavailable(**_):
        pytest.fail("disabling is local")

    service.catalog.list_authorized = unavailable
    for i in range(101):
        result = set_enabled(service, scope, enabled=False, kb_id=f"unknown-{i}")
        assert result["revision"] == 0 and not result["enabled"]
    assert service.list_bindings(scope=scope, actor_id="owner")["items"] == []
    with db.orm_session() as session:
        assert session.query(Binding).count() == 0


@pytest.mark.parametrize("default_bot", [False, True])
def test_latest_draft_queries_use_matching_indexes(knowledge, default_bot):
    db, _, service, scope = knowledge
    bot_id = "default" if default_bot else scope.bot_id
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 1).update(
            {"bot_id": bot_id, "bot_type": "service"}
        )
        session.add(
            BotPublishModel(
                source_bot_pk=1,
                source_bot_id=bot_id,
                publish_bot_id=bot_id,
                owner_id="owner",
                name="Bot",
                status="draft",
                version=1,
                env="dev",
                permission_owner="owner",
            )
        )
    engine = db.factory.kw["bind"]
    queries = []

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        if statement.startswith("SELECT") and "FROM ac_bot_publish" in statement:
            queries.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        set_enabled(service, BotKnowledgeScope(bot_id, "owner", "dev"))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(queries) == 2
    index = (
        "idx_publish_current_default" if default_bot else "idx_publish_current_source"
    )
    with engine.connect() as conn:
        for statement, parameters in queries:
            plan = str(
                conn.exec_driver_sql(
                    "EXPLAIN QUERY PLAN " + statement, parameters
                ).all()
            )
            assert index in plan and "TEMP B-TREE" not in plan


def test_selection_queries_use_enabled_and_cursor_indexes(knowledge):
    db, repo, service, scope = knowledge
    set_enabled(service, scope)
    engine = db.factory.kw["bind"]
    queries = []

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        if (
            statement.startswith("SELECT")
            and "FROM ac_bot_knowledge_binding" in statement
        ):
            queries.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        service.list_bindings(scope=scope, actor_id="owner")
        repo.list_enabled_current(scope)
        service.catalog.authorized.add("second")
        set_enabled(service, scope, kb_id="second")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    indexed = [
        (sql, args) for sql, args in queries if "ORDER BY" in sql or "count(" in sql
    ]
    assert len(indexed) == 3
    with engine.connect() as conn:
        for statement, parameters in indexed:
            plan = str(
                conn.exec_driver_sql(
                    "EXPLAIN QUERY PLAN " + statement, parameters
                ).all()
            )
            index = (
                "idx_bk_page"
                if "ORDER BY ac_bot_knowledge_binding.id" in statement
                else "idx_bk_enabled"
            )
            assert index in plan and "TEMP B-TREE" not in plan



def test_current_draft_mysql_index_respects_utf8mb4_key_limit():
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateIndex
    from agentclaw.community.core.service_bot.repository.models import BotPublishModel

    index = next(i for i in BotPublishModel.__table__.indexes if i.name == "idx_publish_current_default")
    ddl = str(CreateIndex(index).compile(dialect=mysql.dialect()))
    assert "publish_bot_id(512)" in ddl
