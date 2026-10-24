import pytest
from agentclaw.community.kernel.knowledge import BotKnowledgeScope, KnowledgeError
from agentclaw.community.plugins.community.knowledge_catalog import (
    UnavailableKnowledgeCatalog,
)


@pytest.mark.parametrize("method", ["list_authorized", "list_in_space"])
def test_missing_catalog_fails_closed(method):
    catalog = UnavailableKnowledgeCatalog()
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_CATALOG_UNAVAILABLE"):
        if method == "list_in_space":
            catalog.list_in_space(space_id=1, actor_id="owner")
        else:
            catalog.list_authorized(
                scope=BotKnowledgeScope("bot", "owner", "dev"), space_id=1
            )


def seed_consumer(world):
    from agentclaw.community.api.bot_knowledge_service import (
        BotKnowledgeServiceProtocol,
    )
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.core.spaces.repository.models import (
        SpaceModel,
        SpaceMemberModel,
    )

    with world.get(DatabasePlugin).orm_session() as session:
        space = SpaceModel(
            space_code="catalog-contract",
            space_type="TEAM",
            name="Contract",
            env="dev",
            created_by="owner",
            updated_by="owner",
        )
        session.add(space)
        session.flush()
        space_id = space.id
        session.add(
            SpaceMemberModel(
                space_id=space_id,
                user_id="owner",
                role="MEMBER",
                env="dev",
                created_by="owner",
            )
        )
        session.add(
            BotModel(
                bot_id="bot",
                bot_name="Bot",
                owner_id="owner",
                entity_id="owner",
                entity_type="staff",
                creator_id="owner",
                space_id=space_id,
                env="dev",
                bot_type="personal",
            )
        )
    return world.get(BotKnowledgeServiceProtocol), space_id


@pytest.mark.parametrize("authorized", [True, False])
def test_consumer_uses_catalog_for_enablement(world, authorized):
    from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin

    service, space_id = seed_consumer(world)
    catalog = world.get(KnowledgeCatalogPlugin)
    catalog.set_response("list_authorized", ("kb",) if authorized else ())
    scope = BotKnowledgeScope("bot", "owner", "dev")

    def enable():
        return service.set_enabled(
            scope=scope, actor_id="owner", kb_id="kb", enabled=True, expected_revision=0
        )

    if authorized:
        assert enable()["enabled"] is True
        assert service.list_enabled_current(scope) == ("kb",)
    else:
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_NOT_GRANTED"):
            enable()
        assert service.list_enabled_current(scope) == ()
    calls = catalog.calls_to("list_authorized")
    assert len(calls) == 1
    assert calls[0].kwargs == {"scope": scope, "space_id": space_id}


def test_unconfigured_catalog_fails_closed_through_consumer(world):
    from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin

    service, _ = seed_consumer(world)
    scope = BotKnowledgeScope("bot", "owner", "dev")
    with pytest.raises(KnowledgeError, match="KNOWLEDGE_CATALOG_UNAVAILABLE"):
        service.set_enabled(
            scope=scope, actor_id="owner", kb_id="kb", enabled=True, expected_revision=0
        )
    assert service.list_enabled_current(scope) == ()
    assert len(world.get(KnowledgeCatalogPlugin).calls_to("list_authorized")) == 1
