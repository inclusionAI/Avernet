"""Service API wiring for exact Center Version publication."""

from agentclaw.community.api.skill_version_materializer import (
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SkillVersionMaterializer,
)


def test_world_wires_the_public_materializer_seam(world) -> None:
    materializer = world.get(SkillVersionMaterializerProtocol)

    assert isinstance(materializer, SkillVersionMaterializer)
