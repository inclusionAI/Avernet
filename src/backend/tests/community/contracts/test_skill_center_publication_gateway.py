"""Service-API conformance for the narrow Team Publication Gateway seam."""

from agentclaw.community.api.skill_center_publication_gateway import (
    SkillCenterPublicationGatewayProtocol,
)
from agentclaw.community.core.skill_center.services.skill_center_gateway_service import (
    SkillCenterGatewayService,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterPublishStatusRequest,
    SkillCenterPublishSubmitRequest,
    SkillCenterReadScope,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterVersionListRequest,
)


def test_publication_gateway_protocol_reuses_validated_team_publish_consumer(
    world,
) -> None:
    protocol = world.get(SkillCenterPublicationGatewayProtocol)
    concrete = world.get(SkillCenterGatewayService)
    team = concrete.create_team(
        SkillCenterTeamCreateRequest("team-pub", "Publication", "TC", "space-pub")
    )

    submitted = protocol.submit_publish(
        SkillCenterPublishSubmitRequest(
            team_id=team.team_id,
            skill_code="publication-skill",
            skill_name="Publication Skill",
            version_number="1.0.0",
            package_url="https://example.invalid/publication.zip",
        )
    )
    detail = protocol.get_team_skill(
        SkillCenterTeamSkillDetailRequest(team.team_id, "publication-skill")
    )
    status = protocol.get_publish_status(
        SkillCenterPublishStatusRequest("publication-skill")
    )
    versions = protocol.list_versions(
        SkillCenterVersionListRequest(
            "publication-skill", SkillCenterReadScope.TEAM, team.team_id
        )
    )

    assert isinstance(protocol, SkillCenterGatewayService)
    assert submitted.version_number == "1.0.0"
    assert detail is not None and detail.team_id == team.team_id
    assert detail.skill_id is not None and detail.skill_id.isdigit()
    assert status.version_number == "1.0.0"
    assert [version.version_number for version in versions] == ["1.0.0"]
    assert versions[0].version_id is not None and versions[0].version_id.isdigit()
