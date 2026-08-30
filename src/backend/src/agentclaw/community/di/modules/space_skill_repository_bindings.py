"""DI bindings for the Space Skill authoring/read persistence seams."""

from injector import Binder, singleton

from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
    SpaceSkillRepository as UnifiedSpaceSkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_draft import (
    SpaceSkillDraftRepository as UnifiedSpaceSkillDraftRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_read import (
    SpaceSkillReadRepository as UnifiedSpaceSkillReadRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_version_read import (
    SpaceSkillVersionReadRepository as UnifiedSpaceSkillVersionReadRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_publication import (
    SpaceSkillPublicationRepository as UnifiedSpaceSkillPublicationRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    DraftEditLeaseRepository,
    SpaceSkillDraftRepository,
    SpaceSkillReadRepository,
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.protocols.space_skill_version import (
    SpaceSkillVersionReadRepository,
)
from agentclaw.community.core.repository.protocols.space_skill_publication import (
    SpaceSkillPublicationRepositoryProtocol,
)


def bind_space_skill_repositories(binder: Binder) -> None:
    """Bind the additive Space Skill persistence contracts as singletons."""
    binder.bind(
        SpaceSkillRepository,
        to=UnifiedSpaceSkillRepository,
        scope=singleton,
    )
    binder.bind(
        SpaceSkillDraftRepository,
        to=UnifiedSpaceSkillDraftRepository,
        scope=singleton,
    )
    binder.bind(
        SpaceSkillReadRepository,
        to=UnifiedSpaceSkillReadRepository,
        scope=singleton,
    )
    binder.bind(
        SpaceSkillVersionReadRepository,
        to=UnifiedSpaceSkillVersionReadRepository,
        scope=singleton,
    )
    binder.bind(
        SpaceSkillPublicationRepositoryProtocol,
        to=UnifiedSpaceSkillPublicationRepository,
        scope=singleton,
    )
    binder.bind(
        DraftEditLeaseRepository,
        to=UnifiedSpaceSkillRepository,
        scope=singleton,
    )
