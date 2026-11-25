"""Bot capability fence scopes and fails closed on an unresolved Bot."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentclaw.community.core.repository.implementations.skill_center.bot_mutation_fence import (
    lock_bot_capabilities,
)
from agentclaw.community.core.skill_center.errors import SkillSetControlPlaneLockUnavailableError
from agentclaw.community.plugin_api.models import BotModel


def test_fence_resolves_the_stable_entity_and_rejects_another_owner():
    engine = create_engine("sqlite:///:memory:")
    BotModel.__table__.create(engine)
    with Session(engine) as session:
        session.add(BotModel(bot_id="default", entity_id="entity", owner_id="owner", entity_type="staff", creator_id="owner", env="dev"))
        session.commit()
        lock_bot_capabilities(session, bot_id="default", entity_id="entity", env="dev")
        with pytest.raises(SkillSetControlPlaneLockUnavailableError):
            lock_bot_capabilities(session, bot_id="default", entity_id="owner", env="dev")
        with pytest.raises(SkillSetControlPlaneLockUnavailableError):
            lock_bot_capabilities(session, bot_id="default", entity_id="entity", env="pre")
