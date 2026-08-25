"""Rule 25 consumer conformance for the independent SkillCenterGateway."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.services.skill_center_gateway_service import (
    SkillCenterGatewayService,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterPublicSkillSearchRequest,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishState,
    SkillCenterPublishSubmitRequest,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamLookupRequest,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterTeamSkillListRequest,
    SkillCenterVersionListRequest,
)


def _all_gateway_requests() -> tuple[tuple[str, object], ...]:
    return (
        (
            "create_team",
            SkillCenterTeamCreateRequest("team", "Team", "TC", "space-1"),
        ),
        ("get_team_by_ref", SkillCenterTeamLookupRequest("TC", "space-1")),
        ("search_public_skills", SkillCenterPublicSkillSearchRequest()),
        ("get_public_skill", SkillCenterPublicSkillDetailRequest("skill")),
        ("list_team_skills", SkillCenterTeamSkillListRequest("team-1")),
        (
            "get_team_skill",
            SkillCenterTeamSkillDetailRequest("team-1", "skill"),
        ),
        (
            "submit_publish",
            SkillCenterPublishSubmitRequest(
                "team-1", "skill", "Skill", "1", "memory://package"
            ),
        ),
        (
            "get_publish_status",
            SkillCenterPublishStatusRequest("team-1", "skill", "1"),
        ),
        ("list_versions", SkillCenterVersionListRequest("team-1", "skill")),
        (
            "get_exact_download",
            SkillCenterExactDownloadRequest("team-1", "skill", "1"),
        ),
    )


def test_gateway_consumer_round_trips_team_publish_and_exact_version(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    team = service.create_team(
        SkillCenterTeamCreateRequest(
            team_code="space-risk",
            team_name="Risk Team",
            ref_source="TEAMCLAW",
            ref_source_id="space-7",
        )
    )

    assert service.get_team_by_ref(
        SkillCenterTeamLookupRequest(
            ref_source="TEAMCLAW", ref_source_id="space-7"
        )
    ) == team

    submitted = service.submit_publish(
        SkillCenterPublishSubmitRequest(
            team_id=team.team_id,
            skill_code="skill-uuid",
            skill_name="Risk Review",
            version_number="2",
            package_url="https://example.invalid/temporary-package.zip",
        )
    )
    assert submitted.skill_code == "skill-uuid"
    assert submitted.version_number == "2"

    detail = service.get_team_skill(
        SkillCenterTeamSkillDetailRequest(
            team_id=team.team_id, skill_code="skill-uuid"
        )
    )
    assert detail is not None
    assert detail.skill_name == "Risk Review"
    assert service.list_team_skills(
        SkillCenterTeamSkillListRequest(team_id=team.team_id)
    ).items == (detail,)

    status = service.get_publish_status(
        SkillCenterPublishStatusRequest(
            team_id=team.team_id,
            skill_code="skill-uuid",
            version_number="2",
        )
    )
    assert status.status == "PUBLISHED"
    assert status.status is SkillCenterPublishState.PUBLISHED
    assert status.completed is True
    assert status.succeeded is True
    assert service.list_versions(
        SkillCenterVersionListRequest(
            team_id=team.team_id, skill_code="skill-uuid"
        )
    ).items[0].version_number == "2"
    download = service.get_exact_download(
        SkillCenterExactDownloadRequest(
            team_id=team.team_id,
            skill_code="skill-uuid",
            version_number="2",
        )
    )
    assert download.version_number == "2"

    assert [call.method for call in gateway.calls] == [
        "create_team",
        "get_team_by_ref",
        "submit_publish",
        "get_team_skill",
        "list_team_skills",
        "get_publish_status",
        "list_versions",
        "get_exact_download",
    ]


def test_gateway_consumer_routes_public_market_without_a_team(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)

    page = service.search_public_skills(
        SkillCenterPublicSkillSearchRequest(keyword="risk")
    )
    detail = service.get_public_skill(
        SkillCenterPublicSkillDetailRequest(skill_code="missing")
    )

    assert page.items == ()
    assert detail is None
    assert [call.method for call in gateway.calls] == [
        "search_public_skills",
        "get_public_skill",
    ]


def test_gateway_consumer_propagates_stable_error_without_retry(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "submit_publish",
        lambda request: (_ for _ in ()).throw(
            SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.TIMEOUT,
                "publish outcome is unknown",
            )
        ),
    )
    request = SkillCenterPublishSubmitRequest(
        team_id="team-1",
        skill_code="skill-uuid",
        skill_name="Risk Review",
        version_number="1",
        package_url="https://example.invalid/temporary-package.zip",
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.submit_publish(request)

    assert raised.value.code is SkillCenterGatewayErrorCode.TIMEOUT
    assert len(gateway.calls_to("submit_publish")) == 1


@pytest.mark.parametrize("operation", ["get_publish_status", "get_exact_download"])
def test_local_gateway_rejects_an_unknown_exact_version(world, operation: str) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    request = {
        "get_publish_status": SkillCenterPublishStatusRequest(
            "unknown-team", "skill", "9"
        ),
        "get_exact_download": SkillCenterExactDownloadRequest(
            "unknown-team", "skill", "9"
        ),
    }[operation]

    with pytest.raises(SkillCenterGatewayError) as raised:
        getattr(service, operation)(request)

    assert raised.value.code is SkillCenterGatewayErrorCode.BUSINESS
    assert len(gateway.calls_to(operation)) == 1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SkillCenterTeamSkillListRequest(team_id=""),
        lambda: SkillCenterTeamSkillDetailRequest(team_id="", skill_code="skill"),
        lambda: SkillCenterPublishSubmitRequest(
            team_id="",
            skill_code="skill",
            skill_name="Skill",
            version_number="1",
            package_url="https://example.invalid/package.zip",
        ),
        lambda: SkillCenterPublishStatusRequest(
            team_id="", skill_code="skill", version_number="1"
        ),
        lambda: SkillCenterVersionListRequest(team_id="", skill_code="skill"),
        lambda: SkillCenterExactDownloadRequest(
            team_id="", skill_code="skill", version_number="1"
        ),
    ],
)
def test_team_scoped_requests_reject_empty_team_id(factory) -> None:
    with pytest.raises(ValueError, match="team_id is required"):
        factory()


@pytest.mark.parametrize(("operation", "gateway_request"), _all_gateway_requests())
def test_community_gateway_fails_closed_as_unavailable(
    community_world, operation: str, gateway_request: object
) -> None:
    gateway = community_world.get(SkillCenterGateway)

    with pytest.raises(SkillCenterGatewayError) as raised:
        getattr(gateway, operation)(gateway_request)

    assert raised.value.code is SkillCenterGatewayErrorCode.UNAVAILABLE
