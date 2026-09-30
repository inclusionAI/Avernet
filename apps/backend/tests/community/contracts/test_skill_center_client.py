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

from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterMarketSearchRequest,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamQueryRequest,
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
    )

    result = client.create_team(request)

    assert result.team_id == f"mock-{request.team_code}"
    calls = client.calls_to("create_team")
    assert len(calls) == 1
    assert calls[0].args == (request,)
    assert calls[0].kwargs == {}


def test_local_skill_center_client_queries_team_by_external_identity(world) -> None:
    client = world.get(SkillCenterClient)
    request = SkillCenterTeamQueryRequest(source="OCB", ref_source_id="7")

    result = client.get_team_by_ref_source(request)

    assert result is not None
    assert result.team_id == "mock-OCB-7"
    calls = client.calls_to("get_team_by_ref_source")
    assert calls[-1].args == (request,)
    assert calls[-1].kwargs == {}


def test_local_skill_center_market_search_records_typed_request(world) -> None:
    client = world.get(SkillCenterClient)
    request = SkillCenterMarketSearchRequest(
        keyword="assistant",
        page_num=2,
        page_size=10,
        access_level="PUBLIC",
    )

    result = client.search_market_skills(request)

    assert result.total == 0
    assert result.items == ()
    calls = client.calls_to("search_market_skills")
    assert calls[-1].args == (request,)
