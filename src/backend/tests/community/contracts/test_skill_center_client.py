"""Rule 25 conformance — SkillCenterClient.

Consumer under test: ``SkillPublishService`` wires the client via DI
and uses it from multiple methods. The local
``LocalSkillCenterClient`` returns a mock envelope with the original
``skillCode`` echoed back and ``status="PUBLISHED"``.

Driving ``SkillPublishService.publish`` requires substantial bot +
skill repository fixtures; the contract is verified through the
DI-bound instance the service receives.

Plugin-hit assertions: publish returns the local mock fingerprint, and team
creation is recorded by ``MockSeam.calls_to`` with the exact request DTO.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.services.skill_center_gateway_service import (
    SkillCenterGatewayService,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterGateway,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterTeamCreateRequest,
)


def test_local_skill_center_client_upload_returns_mock_envelope(world) -> None:
    client = world.get(SkillCenterClient)
    result = client.upload_and_publish(
        {"skillCode": "demo_skill", "skillName": "Demo", "versionNumber": "1.0.0"}
    )
    assert result["success"] is True
    assert result["data"]["skillCode"] == "demo_skill"
    assert result["data"]["status"] == "PUBLISHED"


def test_local_skill_center_client_query_status_envelope(world) -> None:
    client = world.get(SkillCenterClient)
    result = client.query_publish_status("demo_skill")
    assert result["success"] is True


def test_local_skill_center_client_create_team_records_plugin_hit(world) -> None:
    client = world.get(SkillCenterClient)
    request = SkillCenterTeamCreateRequest(
            team_code="spc-0123456789abcdef0123",
            team_name="Demo Team",
            ref_source_id="7",
            ref_source_platform="teamclaw",
    )

    result = client.create_team(request)

    assert result.team_id == 1
    assert result.ref_source_id == request.ref_source_id
    calls = client.calls_to("create_team")
    assert len(calls) == 1
    assert calls[0].args == (request,)
    assert calls[0].kwargs == {}


def test_local_skill_center_gateway_records_explicit_team_per_request(world) -> None:
    """Q5 calls are scoped by the caller, never by a process-wide default team."""
    consumer = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)

    request = SkillCenterTeamCreateRequest("risk-team", "Risk reviewers", "space-1", ref_source_platform="teamclaw")
    created = consumer.create_team(request)
    team_id = created.team_id
    consumer.submit_publish(
        {"skillCode": "skill-uuid", "versionNumber": "2.0.0"},
        team_id=team_id,
    )
    consumer.get_publish_status("skill-uuid", team_id=team_id)
    consumer.get_skill_detail("skill-uuid", team_id=team_id)
    assert consumer.list_versions("skill-uuid", team_id=team_id)[0]["versionNumber"] == "1.0.0"
    consumer.get_download_url("skill-uuid", "2.0.0", team_id=team_id)
    assert consumer.search_market_skills(keyword="risk")["success"] is True
    assert consumer.get_market_tags() == []
    assert consumer.get_team_by_ref_source(ref_source_platform="teamclaw", ref_source_id="space-1").team_id == team_id
    assert consumer.list_team_skills(team_id=team_id).total == 1

    assert [call.method for call in gateway.calls] == [
        "create_team",
        "upload_and_publish",
        "query_publish_status",
        "get_skill_detail",
        "list_versions",
        "get_download_url",
        "search_market_skills",
        "get_market_tags",
        "get_team_by_ref_source",
        "list_team_skills",
    ]
    for operation in ("upload_and_publish", "query_publish_status", "get_skill_detail", "list_versions", "get_download_url"):
        assert gateway.calls_to(operation)[0].kwargs["team_id"] == team_id


def test_local_skill_center_gateway_rejects_omitted_team_id(world) -> None:
    consumer = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)

    with pytest.raises(TypeError):
        consumer.get_skill_detail("skill-uuid")

    assert gateway.calls == []


@pytest.mark.parametrize(
    ("code", "operation"),
    [
        (SkillCenterGatewayErrorCode.BUSINESS, "create_team"),
        (SkillCenterGatewayErrorCode.TIMEOUT, "upload_and_publish"),
        (SkillCenterGatewayErrorCode.UNKNOWN_RESPONSE, "query_publish_status"),
        (SkillCenterGatewayErrorCode.PROTOCOL, "get_download_url"),
    ],
)
def test_local_skill_center_gateway_normalizes_failures(world, code, operation) -> None:
    consumer = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.fail_next(operation, code, "stable failure")
    calls = {
        "create_team": lambda: consumer.create_team(SkillCenterTeamCreateRequest("risk", "Risk", "space-1", ref_source_platform="teamclaw")),
        "upload_and_publish": lambda: consumer.submit_publish(
            {"skillCode": "skill-uuid", "versionNumber": "1.0.0"}, team_id="team-1"
        ),
        "query_publish_status": lambda: consumer.get_publish_status("skill-uuid", team_id="team-1"),
        "get_download_url": lambda: consumer.get_download_url("skill-uuid", "1.0.0", team_id="team-1"),
    }

    with pytest.raises(SkillCenterGatewayError) as raised:
        calls[operation]()

    assert raised.value.code is code
    assert [call.method for call in gateway.calls_to(operation)] == [operation]


def test_local_skill_center_gateway_never_retries_publish_post_after_timeout(world) -> None:
    consumer = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.fail_next("upload_and_publish", SkillCenterGatewayErrorCode.TIMEOUT, "timeout")

    with pytest.raises(SkillCenterGatewayError) as raised:
        consumer.submit_publish(
            {"skillCode": "skill-uuid", "versionNumber": "1.0.0"}, team_id="team-1"
        )

    assert raised.value.code is SkillCenterGatewayErrorCode.TIMEOUT
    assert len(gateway.calls_to("upload_and_publish")) == 1
