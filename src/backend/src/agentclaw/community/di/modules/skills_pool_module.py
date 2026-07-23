"""Skills Pool 控制面状态、灰度门禁与认领服务装配。"""

from injector import Binder, Module, singleton

from agentclaw.community.core.skills_pool.claim_service import (
    SkillsPoolMigrationClaimService,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    SkillsPoolRolloutGate,
)
from agentclaw.community.plugins.skills_pool_layout_repository import (
    SkillsPoolLayoutRepository,
)


class SkillsPoolModule(Module):
    """绑定与部署 profile 无关的统一 ORM 仓储和领域服务。"""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            SkillsPoolLayoutRepositoryProtocol,
            to=SkillsPoolLayoutRepository,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolRolloutGate,
            to=SkillsPoolRolloutGate,
            scope=singleton,
        )
        binder.bind(
            SkillsPoolMigrationClaimService,
            to=SkillsPoolMigrationClaimService,
            scope=singleton,
        )
