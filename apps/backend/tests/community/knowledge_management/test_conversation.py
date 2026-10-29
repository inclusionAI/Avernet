"""Conversation reads share effective Bot knowledge, not Editor or caller KB ACLs."""

from unittest.mock import Mock

import pytest
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.core.knowledge_management.services.conversation import (
    ConversationKnowledgeService,
)
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError

from .conftest import fact


@pytest.fixture
def conversation(world):
    BotKnowledgeBindingModel.__table__.create(world.engine)
    with world.db.orm_session() as session:
        session.add(
            SpaceMemberModel(
                space_id=1,
                user_id="other-owner",
                role="MEMBER",
                status="ACTIVE",
                env="pre",
                created_by="owner",
            )
        )
        session.add(
            BotKnowledgeBindingModel(
                bot_pk=1,
                space_id=1,
                env="pre",
                kb_id="kb",
                enabled=True,
                revision=7,
                updated_by="owner",
            )
        )
    world.catalog.page_authorized = Mock(
        return_value={"kb_ids": ["kb"], "next_cursor": None}
    )
    world.catalog.details = Mock(return_value=[fact()])
    collaborators = Mock()
    collaborators.check_collaborator_permission.return_value = {"has_permission": False}
    friendships = Mock()
    friendships.is_friend.return_value = True
    service = ConversationKnowledgeService(
        world.source, world.catalog, collaborators, friendships
    )
    return service, collaborators, friendships


def read(conversation, actor="friend", **kwargs):
    service, _, _ = conversation
    return service.list_for_bot(
        scope=BotKnowledgeScope("bot", "owner", "pre"),
        actor_id=actor,
        request_headers={},
        **kwargs,
    )


def test_friend_without_space_membership_or_editor_or_kb_acl_can_read(
    world, conversation
):
    assert read(conversation) == {
        "items": [{"kb_id": "kb", "name": "Knowledge"}],
        "next_cursor": None,
    }
    world.catalog.details.assert_called_once_with(
        space_id=1, kb_ids=["kb"], actor_id="owner"
    )
    conversation[2].is_friend.assert_called_once_with(
        human_id="friend",
        bot_id="bot",
        owner_id="owner",
        request_headers={},
    )
    # The existing management path must still refuse the same caller.
    with pytest.raises(
        KnowledgeError, match="SPACE_MEMBER_REQUIRED|BOT_EDITOR_REQUIRED"
    ):
        world.service.available_for_bot(
            scope=BotKnowledgeScope("bot", "owner", "pre"), actor_id="friend"
        )


@pytest.mark.parametrize("access", ["owner", "public", "collaborator"])
def test_native_conversation_access_does_not_require_friendship(
    world, conversation, access
):
    from agentclaw.community.plugin_api.models import BotModel

    actor = "owner" if access == "owner" else "friend"
    if access == "public":
        with world.db.orm_session() as session:
            session.query(BotModel).filter(BotModel.id == 1).update({"public": "1"})
    if access == "collaborator":
        conversation[1].check_collaborator_permission.return_value = {
            "has_permission": True
        }
    assert len(read(conversation, actor=actor)["items"]) == 1
    conversation[2].is_friend.assert_not_called()


def test_private_stranger_cannot_probe_grants_or_enablement(world, conversation):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    conversation[2].is_friend.return_value = False
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CHAT_ACCESS_DENIED"):
        read(conversation)
    world.catalog.page_authorized.assert_not_called()
    world.catalog.details.assert_not_called()
    assert not any(
        "ac_bot_knowledge_binding" in sql and "SELECT" in sql for sql in world.sql
    )


@pytest.mark.parametrize("dependency", ["collaborator", "friendship"])
def test_permission_dependency_failure_never_grants_access(
    world, conversation, dependency
):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    method = (
        conversation[1].check_collaborator_permission
        if dependency == "collaborator"
        else conversation[2].is_friend
    )
    method.side_effect = RuntimeError("unavailable")
    with pytest.raises(
        KnowledgeManagementError, match="KNOWLEDGE_CHAT_ACCESS_UNAVAILABLE"
    ):
        read(conversation)
    world.catalog.page_authorized.assert_not_called()


def test_only_current_grants_intersect_enabled_rows_and_details_are_one_batch(
    world, conversation
):
    with world.db.orm_session() as session:
        session.add_all(
            [
                BotKnowledgeBindingModel(
                    bot_pk=1,
                    space_id=1,
                    env="pre",
                    kb_id=kb_id,
                    enabled=enabled,
                    revision=1,
                    updated_by="owner",
                )
                for kb_id, enabled in [
                    ("revoked", True),
                    ("disabled", False),
                    ("second", True),
                ]
            ]
        )
    world.catalog.page_authorized.return_value = {
        "kb_ids": ["disabled", "kb", "missing", "second"],
        "next_cursor": 19,
    }
    world.catalog.details.return_value = [fact(), fact("second")]
    assert read(conversation, cursor=7, limit=4) == {
        "items": [
            {"kb_id": "kb", "name": "Knowledge"},
            {"kb_id": "second", "name": "Knowledge"},
        ],
        "next_cursor": 19,
    }
    world.catalog.page_authorized.assert_called_once_with(
        scope=BotKnowledgeScope("bot", "owner", "pre"),
        space_id=1,
        cursor=7,
        limit=4,
    )
    world.catalog.details.assert_called_once_with(
        space_id=1, kb_ids=["kb", "second"], actor_id="owner"
    )


def test_fully_filtered_page_keeps_next_cursor_without_metadata_calls(
    world, conversation
):
    world.catalog.page_authorized.return_value = {
        "kb_ids": ["not-enabled"],
        "next_cursor": 23,
    }
    assert read(conversation) == {"items": [], "next_cursor": 23}
    world.catalog.details.assert_not_called()
    assert world.catalog.page_authorized.call_count == 1


@pytest.mark.parametrize("status", ["creating", "failed"])
def test_not_ready_libraries_are_not_selectable(world, conversation, status):
    world.catalog.details.return_value = [fact(kgbase_status=status)]
    assert read(conversation) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    "rows", [[], [fact("foreign")], [fact(space_id="2")], [fact(name=None)]]
)
def test_incomplete_or_foreign_metadata_is_not_shared(world, conversation, rows):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    world.catalog.details.return_value = rows
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CATALOG_INVALID"):
        read(conversation)


def test_selection_is_scoped_to_bot_owner_env_space_and_tenant(world, conversation):
    from agentclaw.community.plugin_api.models import BotModel

    with world.db.orm_session() as session:
        session.query(BotKnowledgeBindingModel).delete()
        session.query(BotModel).filter(BotModel.id == 2).update(
            {"bot_type": "personal"}
        )
        session.add_all(
            [
                BotKnowledgeBindingModel(
                    bot_pk=bot_pk,
                    space_id=space,
                    env=env,
                    kb_id="kb",
                    avernet_tenant=tenant,
                    enabled=True,
                    revision=1,
                    updated_by="owner",
                )
                for bot_pk, space, env, tenant in [
                    (2, 1, "pre", "teamclaw"),
                    (1, 1, "prod", "teamclaw"),
                    (1, 2, "pre", "teamclaw"),
                ]
            ]
        )
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    with avernet_tenant_scope("external"), world.db.orm_session() as session:
        session.add(
            BotKnowledgeBindingModel(
                bot_pk=1,
                space_id=1,
                env="pre",
                kb_id="kb",
                enabled=True,
                revision=1,
                updated_by="owner",
            )
        )
    assert read(conversation) == {"items": [], "next_cursor": None}
    # The duplicate business bot_id under another Owner is a distinct Bot.
    assert conversation[0].list_for_bot(
        scope=BotKnowledgeScope("bot", "other-owner", "pre"),
        actor_id="other-owner",
        request_headers={},
    )["items"] == [{"kb_id": "kb", "name": "Knowledge"}]


def test_foreign_tenant_and_environment_fail_before_catalog(world, conversation):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    with avernet_tenant_scope("external"):
        with pytest.raises(
            KnowledgeManagementError, match="KNOWLEDGE_CHAT_ACCESS_DENIED"
        ):
            read(conversation)
    with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CHAT_ACCESS_DENIED"):
        conversation[0].list_for_bot(
            scope=BotKnowledgeScope("bot", "owner", "prod"),
            actor_id="owner",
            request_headers={},
        )
    world.catalog.page_authorized.assert_not_called()


@pytest.mark.parametrize(
    "scope",
    [
        BotKnowledgeScope("deleted", "owner", "pre"),
        BotKnowledgeScope("bot", "absent", "pre"),
    ],
)
def test_deleted_or_missing_bot_cannot_share_knowledge(world, conversation, scope):
    with pytest.raises(KnowledgeError, match="BOT_NOT_FOUND"):
        conversation[0].list_for_bot(scope=scope, actor_id="owner", request_headers={})
    world.catalog.page_authorized.assert_not_called()


def test_service_bot_draft_selection_is_not_exposed(world, conversation):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    with pytest.raises(
        KnowledgeManagementError, match="KNOWLEDGE_CONVERSATION_UNSUPPORTED"
    ):
        conversation[0].list_for_bot(
            scope=BotKnowledgeScope("bot", "other-owner", "pre"),
            actor_id="other-owner",
            request_headers={},
        )
    world.catalog.page_authorized.assert_not_called()


@pytest.mark.parametrize(
    "values", [{"limit": 21}, {"limit": 0}, {"cursor": -1}, {"cursor": True}]
)
def test_invalid_pagination_never_reads_catalog(world, conversation, values):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    with pytest.raises(KnowledgeManagementError, match="INVALID_KNOWLEDGE_INPUT"):
        read(conversation, **values)
    world.catalog.page_authorized.assert_not_called()


@pytest.fixture
def conversation_client(client, conversation):
    from agentclaw.community.api.conversation_knowledge_service import (
        ConversationKnowledgeServiceProtocol,
    )

    client.app.state.injector.binder.bind(
        ConversationKnowledgeServiceProtocol, to=conversation[0]
    )
    return client


def test_http_uses_authenticated_actor_not_query_and_exposes_only_reference_fields(
    world, conversation, conversation_client, monkeypatch
):
    monkeypatch.setenv("SERVER_ENV", "prepub")
    response = conversation_client.get(
        "/api/knowledge/bots/bot/conversation-knowledge-bases?owner_id=owner&actor_id=owner&cursor=0&limit=20",
        headers={"x-request-id": "test-request", "x-untrusted": "do-not-forward"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [{"kb_id": "kb", "name": "Knowledge"}],
        "next_cursor": None,
    }
    assert conversation[2].is_friend.call_args.kwargs["human_id"] == "actor"
    assert conversation[2].is_friend.call_args.kwargs["request_headers"] == {
        "x-request-id": "test-request"
    }


def test_http_stranger_gets_403_while_old_editor_endpoint_is_unchanged(
    world, conversation, conversation_client, monkeypatch
):
    from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel

    monkeypatch.setenv("SERVER_ENV", "prepub")
    BotCollaboratorModel.__table__.create(world.engine)
    conversation[2].is_friend.return_value = False
    response = conversation_client.get(
        "/api/knowledge/bots/bot/conversation-knowledge-bases?owner_id=owner"
    )
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "KNOWLEDGE_CHAT_ACCESS_DENIED"}}
    response = conversation_client.get(
        "/api/knowledge/bots/bot/available-knowledge-bases?owner_id=owner"
    )
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "BOT_EDITOR_REQUIRED"}}


def test_http_config_disabled_fails_before_business_calls(
    world, conversation, conversation_client
):
    from agentclaw.community.core.knowledge_management.config import (
        KnowledgeManagementConfig,
    )

    conversation_client.app.state.injector.binder.bind(
        KnowledgeManagementConfig, to=KnowledgeManagementConfig()
    )
    response = conversation_client.get(
        "/api/knowledge/bots/bot/conversation-knowledge-bases?owner_id=owner"
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "KNOWLEDGE_INTEGRATION_UNAVAILABLE"}}
    world.catalog.page_authorized.assert_not_called()


def test_revocation_removes_reference_even_though_enabled_selection_is_retained(
    world, conversation
):
    assert len(read(conversation)["items"]) == 1
    world.catalog.page_authorized.return_value = {"kb_ids": [], "next_cursor": None}
    world.sql.clear()
    assert read(conversation) == {"items": [], "next_cursor": None}
    # No admission, selection or ACL write is performed by either read.
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in world.sql)
    with world.db.orm_session() as session:
        row = session.query(BotKnowledgeBindingModel).one()
        assert row.enabled is True and row.revision == 7


def test_actual_catalog_transport_uses_one_grant_request_and_one_owner_metadata_batch(
    world, conversation
):
    import json

    import httpx

    from .conftest import make_catalog

    requests = []

    def receive(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.path.endswith(
                "/spaces/1/bots/bot/authorized-knowledge-bases"
            )
            assert dict(request.url.params) == {
                "user_id": "owner",
                "cursor": "0",
                "limit": "20",
            }
            return httpx.Response(
                200,
                json={
                    "bot_id": "bot",
                    "user_id": "owner",
                    "space_id": "1",
                    "kb_ids": ["kb"],
                    "next_cursor": None,
                },
            )
        payload = json.loads(request.content)
        assert payload == {"actor_staff_no": "owner", "kb_ids": ["kb"]}
        return httpx.Response(200, json={"space_id": "1", "items": [fact()]})

    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        conversation[0].catalog = make_catalog(
            "https://knowledge.example", enabled=True, client=http
        )
        assert read(conversation) == {
            "items": [{"kb_id": "kb", "name": "Knowledge"}],
            "next_cursor": None,
        }
    assert len(requests) == 2


def test_new_service_is_resolvable_through_public_composition_root(world, conversation):
    from agentclaw.community.api.conversation_knowledge_service import (
        ConversationKnowledgeServiceProtocol,
    )
    from agentclaw.community.api.human_bot_friendship_service import (
        HumanBotFriendshipServiceProtocol,
    )
    from agentclaw.community.core.bot_collaborator.protocols import (
        CollaboratorServiceProtocol,
    )
    from agentclaw.community.core.knowledge_management.ports import KnowledgeCatalog
    from agentclaw.community.core.knowledge_management.repository.source import (
        KnowledgeSourceRepository,
    )
    from agentclaw.community.di.modules.knowledge_management_module import (
        KnowledgeManagementModule,
    )
    from agentclaw.community.di.profile import DeployProfile
    from injector import Injector, InstanceProvider

    injector = Injector(
        [KnowledgeManagementModule(DeployProfile.TEST)], auto_bind=False
    )
    injector.binder.bind(KnowledgeSourceRepository, to=world.source)
    injector.binder.bind(KnowledgeCatalog, to=InstanceProvider(world.catalog))
    injector.binder.bind(CollaboratorServiceProtocol, to=conversation[1])
    injector.binder.bind(HumanBotFriendshipServiceProtocol, to=conversation[2])
    service = injector.get(ConversationKnowledgeServiceProtocol)
    assert isinstance(service, ConversationKnowledgeServiceProtocol)
    assert service.list_for_bot(
        scope=BotKnowledgeScope("bot", "owner", "pre"),
        actor_id="friend",
        request_headers={},
    )["items"] == [{"kb_id": "kb", "name": "Knowledge"}]


def test_native_sqlite_conversation_permission_is_reused_without_editor_escalation(
    world, conversation, monkeypatch
):
    from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
        DisabledBotManagerSync,
    )
    from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
        CollaboratorService,
    )
    from agentclaw.community.core.repository.implementations.bot.bot import (
        BotRepository,
    )
    from agentclaw.community.core.repository.implementations.bot.collaborator import (
        CollaboratorRepository,
    )
    from agentclaw.community.core.repository.implementations.spaces.persistence_effects import (
        NoKnowledgeSpacePersistenceEffects,
    )
    from agentclaw.community.core.repository.implementations.spaces.space import (
        SpaceRepository,
    )
    from agentclaw.community.core.spaces.services.space_access_service import (
        SpaceAccessService,
    )

    monkeypatch.setenv("SERVER_ENV", "prepub")
    BotCollaboratorModel.__table__.create(world.engine)
    conversation[0].collaborators = CollaboratorService(
        CollaboratorRepository(world.db),
        BotRepository(world.db),
        Mock(),
        Mock(),
        SpaceAccessService(
            SpaceRepository(world.db, NoKnowledgeSpacePersistenceEffects())
        ),
        DisabledBotManagerSync(),
    )
    # Native Team membership provides ordinary chat access but not explicit Editor.
    assert len(read(conversation, actor="actor")["items"]) == 1
    conversation[2].is_friend.assert_not_called()
    with pytest.raises(KnowledgeError, match="BOT_EDITOR_REQUIRED"):
        world.service.available_for_bot(
            scope=BotKnowledgeScope("bot", "owner", "pre"), actor_id="actor"
        )
    # An external friend without Space/Editor rows follows authoritative friendship.
    assert len(read(conversation, actor="friend")["items"]) == 1
    assert conversation[2].is_friend.call_args.kwargs["human_id"] == "friend"
    with world.db.orm_session() as session:
        assert session.query(BotCollaboratorModel).count() == 0
