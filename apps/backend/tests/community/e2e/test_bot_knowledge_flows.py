"""Public HTTP consumer -> current selection persistence, no grant mirror."""

import pytest
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.knowledge_catalog import KnowledgeCatalogPlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from tests.community._flows.bot_knowledge.api_lifecycle import BOT_KNOWLEDGE_FLOWS
from tests.community.framework.flow_runner import run_flow


@pytest.mark.parametrize("case", BOT_KNOWLEDGE_FLOWS, ids=lambda case: case.name)
def test_current_knowledge_http_flow(case, app_with_testing_modules, world):
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        space = SpaceModel(
            space_code="knowledge-flow",
            space_type="TEAM",
            name="Flow",
            env="dev",
            created_by="owner",
            updated_by="owner",
        )
        session.add(space)
        session.flush()
        session.add(
            SpaceMemberModel(
                space_id=space.id,
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
                creator_id="owner",
                entity_id="owner",
                entity_type="staff",
                space_id=space.id,
                env="dev",
                bot_type="personal",
            )
        )
    world.get(KnowledgeCatalogPlugin).set_response("list_authorized", ("kb",))
    run_flow(case, app_with_testing_modules, world)
    with db.orm_session() as session:
        row = session.query(BotKnowledgeBindingModel).one()
        assert not row.enabled and row.revision == 2
