"""Composition-root bindings for exact published Skill version resolution."""

from injector import Binder, Module, singleton

from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)
from agentclaw.community.core.skill_center.version_resolution_contract import (
    SkillVersionResolverProtocol,
)


class SkillVersionModule(Module):
    """Wire the Version Resolution repository and Service API implementation."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            SkillVersionRepositoryProtocol,
            to=SkillVersionRepository,
            scope=singleton,
        )
        binder.bind(
            SkillVersionResolverProtocol,
            to=SkillVersionResolver,
            scope=singleton,
        )
