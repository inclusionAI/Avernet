"""Composition-root coverage for the Publication vertical slice."""

import asyncio

from agentclaw.community.api.skill_center_gateway_service import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.api.skill_center_publication_gateway import (
    SkillCenterPublicationGatewayProtocol,
)
from agentclaw.community.api.space_skill_publication_service import (
    SpaceSkillPublicationServiceProtocol,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_publication import (
    SpaceSkillPublicationRepository,
)
from agentclaw.community.core.repository.protocols.space_skill_publication import (
    SpaceSkillPublicationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.skill_center_gateway_service import (
    SkillCenterGatewayService,
)
from agentclaw.community.core.skill_center.services.space_skill_publication_service import (
    SpaceSkillPublicationService,
)
from agentclaw.community.core.skill_center.services.space_skill_publication_task import (
    SPACE_SKILL_PUBLICATION_TASK,
    SpaceSkillPublicationTaskHandler,
    SpaceSkillPublicationTaskLifecycle,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry


def test_publication_uses_separate_gateway_protocol_and_real_vertical_wiring(
    test_injector,
) -> None:
    gateway = test_injector.get(SkillCenterPublicationGatewayProtocol)
    repository = test_injector.get(SpaceSkillPublicationRepositoryProtocol)
    service = test_injector.get(SpaceSkillPublicationServiceProtocol)
    handler = test_injector.get(SpaceSkillPublicationTaskHandler)
    lifecycle = test_injector.get(SpaceSkillPublicationTaskLifecycle)
    registry = test_injector.get(HandlerRegistry)

    assert isinstance(gateway, SkillCenterGatewayService)
    assert isinstance(repository, SpaceSkillPublicationRepository)
    assert isinstance(service, SpaceSkillPublicationService)
    assert handler.task_type == SPACE_SKILL_PUBLICATION_TASK
    assert isinstance(lifecycle, SpaceSkillPublicationTaskLifecycle)
    assert not hasattr(SkillCenterGatewayServiceProtocol, "submit_publish")
    assert hasattr(SkillCenterPublicationGatewayProtocol, "submit_publish")

    asyncio.run(lifecycle.bootstrap())
    assert registry.get(SPACE_SKILL_PUBLICATION_TASK) is handler
    assert registry.wakes_on_enqueue(SPACE_SKILL_PUBLICATION_TASK) is True
