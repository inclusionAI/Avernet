"""Manifest bulk Set writes require the same persisted Bot fence as Skill edits."""

import pytest

from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneLockUnavailableError,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from .capability_desired_state_support import _Database
from .test_apply_skill_sets_uow import SCOPE


@pytest.mark.parametrize("missing", ["deleted", "wrong-env", "absent"])
def test_bulk_apply_refuses_unavailable_bot_fence_without_creating_sets(missing):
    db = _Database()
    with db.transactional_orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot", entity_id="owner").one()
        if missing == "deleted":
            bot.is_delete = 1
        elif missing == "wrong-env":
            bot.env = "other"
        else:
            session.delete(bot)
    with pytest.raises(SkillSetControlPlaneLockUnavailableError):
        CapabilityDesiredStateRepository(db).replace_apply_skill_sets(
            scope=SCOPE,
            sets=(ResolvedSkillSetWrite("expert"),),
            remove_names=(),
            default_change=DefaultExclusionChange(),
            platform_default_codes=frozenset(),
            mcp_catalog_entries={},
        )
    with db.orm_session() as session:
        assert session.query(SkillSet).count() == 0
