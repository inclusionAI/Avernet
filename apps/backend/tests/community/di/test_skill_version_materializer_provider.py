"""Exact Version Materializer must consume the validated Gateway seam."""

from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.di import DeployProfile, build_injector


def test_materializer_uses_the_validated_gateway_service_seam() -> None:
    injector = build_injector(profile=DeployProfile.TEST)

    materializer = injector.get(SkillVersionMaterializerProtocol)

    assert materializer._gateway is injector.get(  # noqa: SLF001
        SkillCenterGatewayServiceProtocol
    )
