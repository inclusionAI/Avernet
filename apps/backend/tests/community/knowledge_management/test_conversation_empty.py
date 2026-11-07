"""No enabled selections is an empty conversation view, not a provider error."""

from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from .conftest import make_catalog
from .test_conversation import (
    conversation as conversation,
    conversation_client as conversation_client,
    read,
)
from .test_legacy_personal_bots import personal as personal


@pytest.mark.parametrize("state", ["never-enabled", "all-disabled"])
@pytest.mark.parametrize("cursor", [0, 23])
@pytest.mark.parametrize("actor", ["owner", "friend"])
def test_no_enabled_selections_never_contacts_uninitialized_provider(
    world, conversation, state, cursor, actor
):
    with world.db.orm_session() as session:
        if state == "never-enabled":
            session.query(Binding).delete()
        else:
            session.query(Binding).update({"enabled": False})
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(404, json={"detail": {"code": "RESOURCE_NOT_FOUND"}})

    world.sql.clear()
    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        conversation[0].catalog = make_catalog(
            "https://knowledge.example", enabled=True, client=http
        )
        assert read(conversation, actor=actor, cursor=cursor) == {
            "items": [],
            "next_cursor": None,
        }
    assert requests == []
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in world.sql)
    selects = [sql for sql in world.sql if "ac_bot_knowledge_binding" in sql]
    assert len(selects) == 1
    assert "LIMIT" in selects[0]


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "KNOWLEDGE_REMOTE_ACCESS_DENIED"),
        (404, "KNOWLEDGE_REMOTE_NOT_FOUND"),
        (503, "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_existing_enabled_selection_preserves_provider_errors(
    world, conversation, status, code
):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(status, json={"detail": {"code": "PROVIDER_ERROR"}})

    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        conversation[0].catalog = make_catalog(
            "https://knowledge.example", enabled=True, client=http
        )
        with pytest.raises(KnowledgeManagementError, match=code):
            read(conversation)
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/authorized-knowledge-bases")


def test_uninitialized_legacy_personal_bot_returns_empty_through_http(
    personal, conversation, conversation_client, monkeypatch
):
    from agentclaw.community.plugin_api.models import BotModel

    monkeypatch.setenv("SERVER_ENV", "prepub")
    with personal.db.orm_session() as session:
        session.query(Binding).delete()
    personal.catalog.page_authorized.side_effect = KnowledgeManagementError(
        "KNOWLEDGE_REMOTE_NOT_FOUND"
    )
    personal.sql.clear()
    response = conversation_client.get(
        "/api/knowledge/bots/bot/conversation-knowledge-bases?owner_id=owner"
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}
    personal.catalog.page_authorized.assert_not_called()
    personal.catalog.details.assert_not_called()
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in personal.sql)
    with personal.db.orm_session() as session:
        assert session.get(BotModel, 1).space_id is None
        assert session.query(Binding).count() == 0


def test_empty_bot_still_requires_conversation_permission(world, conversation):
    with world.db.orm_session() as session:
        session.query(Binding).delete()
    conversation[2].is_friend.return_value = False
    world.sql.clear()
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CHAT_ACCESS_DENIED"):
        read(conversation)
    world.catalog.page_authorized.assert_not_called()
    assert not any("ac_bot_knowledge_binding" in sql for sql in world.sql)


@pytest.mark.parametrize(
    ("bot_pk", "space_id", "env", "tenant"),
    [
        (2, 1, "pre", "teamclaw"),
        (1, 2, "pre", "teamclaw"),
        (1, 1, "prod", "teamclaw"),
        (1, 1, "pre", "external"),
    ],
)
def test_enabled_existence_is_scoped_to_exact_bot_space_env_and_tenant(
    world, conversation, bot_pk, space_id, env, tenant
):
    with world.db.orm_session() as session:
        session.query(Binding).delete()
    with avernet_tenant_scope(tenant), world.db.orm_session() as session:
        session.add(
            Binding(
                bot_pk=bot_pk,
                space_id=space_id,
                env=env,
                kb_id="kb",
                enabled=True,
                revision=1,
                updated_by="owner",
            )
        )
    world.catalog.page_authorized.side_effect = KnowledgeManagementError(
        "KNOWLEDGE_REMOTE_NOT_FOUND"
    )
    assert read(conversation) == {"items": [], "next_cursor": None}
    world.catalog.page_authorized.assert_not_called()


def test_enabled_existence_read_failure_is_not_an_empty_result(world, conversation):
    world.source.has_enabled_selection = Mock(side_effect=RuntimeError("read failed"))
    with pytest.raises(RuntimeError, match="read failed"):
        read(conversation)
    world.catalog.page_authorized.assert_not_called()


def test_empty_existence_lookup_uses_scoped_index_with_many_unrelated_rows(
    world, conversation
):
    with world.db.orm_session() as session:
        session.add_all(
            [
                Binding(
                    bot_pk=2,
                    space_id=1,
                    env="pre",
                    kb_id=f"unrelated-{index}",
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
                for index in range(2000)
            ]
        )
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "ac_bot_knowledge_binding" in statement:
            statements.append((statement, parameters))

    event.listen(world.engine, "before_cursor_execute", record)
    try:
        assert (
            world.source.has_enabled_selection(bot={"id": 99, "space_id": 1}) is False
        )
    finally:
        event.remove(world.engine, "before_cursor_execute", record)
    assert len(statements) == 1
    statement, parameters = statements[0]
    with world.engine.connect() as connection:
        plan = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN " + statement, parameters
        ).all()
    description = " ".join(str(row[-1]) for row in plan)
    assert "SEARCH" in description and "idx_bk_enabled" in description
