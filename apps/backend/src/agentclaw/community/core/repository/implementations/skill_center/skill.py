"""Skill and SkillSet repositories composed from focused command modules."""

from injector import inject
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository as SkillRepositoryProtocol,
    SkillSetRepository as SkillSetRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolSkillRepositoryProtocol,
)
from .skill_asset_part_1 import SkillAssetPart1
from .skill_asset_part_2 import SkillAssetPart2


class SkillRepository(
    SkillAssetPart1,
    SkillAssetPart2,
    SkillRepositoryProtocol,
    SkillsPoolSkillRepositoryProtocol,
):
    @inject
    def __init__(self, db: DatabasePlugin):
        from agentclaw.community.core.models import Skill, SkillSet, SkillSetSkill

        self._db = db
        self.Skill = Skill
        self.SkillSet = SkillSet
        self.SkillSetSkill = SkillSetSkill


from .skill_set_part_1 import SkillSetPart1
from .skill_set_part_2 import SkillSetPart2


class SkillSetRepository(SkillSetPart1, SkillSetPart2, SkillSetRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin):
        from agentclaw.community.core.models import (
            Skill,
            SkillSet,
            SkillSetMCPServer,
            SkillSetSkill,
        )

        self._db = db
        self.Skill = Skill
        self.SkillSet = SkillSet
        self.SkillSetSkill = SkillSetSkill
        self.SkillSetMCPServer = SkillSetMCPServer
