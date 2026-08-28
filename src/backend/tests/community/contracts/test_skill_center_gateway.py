"""Rule 25 consumer conformance for the independent SkillCenterGateway."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse
import zipfile

import pytest

from agentclaw.community.core.skill_center.services.skill_center_gateway_service import (
    SkillCenterGatewayService,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterAccessLevel,
    SkillCenterBelongTo,
    SkillCenterExactDownload,
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterPublicSkillSearchRequest,
    SkillCenterReadScope,
    SkillCenterSecurityCheckReport,
    SkillCenterPublishStatus,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishState,
    SkillCenterPublishSubmitRequest,
    SkillCenterPublishSubmission,
    SkillCenterPublishSubmissionState,
    SkillCenterSkill,
    SkillCenterSkillPage,
    SkillCenterSortOrder,
    SkillCenterStandardCheckResult,
    SkillCenterTeam,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamLookupRequest,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterTeamSkillListRequest,
    SkillCenterTeamSkill,
    SkillCenterTeamSkillPage,
    SkillCenterVersion,
    SkillCenterVersionListRequest,
    SkillCenterVisibility,
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
        ("list_public_tags", None),
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
            SkillCenterPublishStatusRequest("skill"),
        ),
        (
            "list_versions",
            SkillCenterVersionListRequest(
                "skill", SkillCenterReadScope.TEAM, "team-1"
            ),
        ),
        (
            "get_exact_download",
            SkillCenterExactDownloadRequest(
                "skill", "1", SkillCenterReadScope.TEAM, "team-1"
            ),
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

    assert (
        service.get_team_by_ref(
            SkillCenterTeamLookupRequest(ref_source="TEAMCLAW", ref_source_id="space-7")
        )
        == team
    )

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
        SkillCenterTeamSkillDetailRequest(team_id=team.team_id, skill_code="skill-uuid")
    )
    assert detail is not None
    assert detail.skill_name == "Risk Review"
    assert service.list_team_skills(
        SkillCenterTeamSkillListRequest(team_id=team.team_id)
    ).items == (detail,)

    status = service.get_publish_status(
        SkillCenterPublishStatusRequest(skill_code="skill-uuid")
    )
    assert status.status == "PUBLISHED"
    assert status.status is SkillCenterPublishState.PUBLISHED
    assert status.upstream_status == "PUBLISHED"
    assert status.status_description == "已发布"
    assert status.skill_name == "Risk Review"
    assert status.completed is True
    assert status.succeeded is True
    assert status.is_completed is True
    assert status.is_success is True
    assert isinstance(status.standard_check_result, SkillCenterStandardCheckResult)
    assert isinstance(status.security_check_report, SkillCenterSecurityCheckReport)
    assert status.standard_check_result.raw == {"passed": True}
    assert status.security_check_report.raw == {"risk": "LOW"}
    versions = service.list_versions(
        SkillCenterVersionListRequest(
            team_id=team.team_id,
            skill_code="skill-uuid",
            scope=SkillCenterReadScope.TEAM,
        )
    )
    assert versions[0].version_number == "2"
    assert versions[0].note is None
    download = service.get_exact_download(
        SkillCenterExactDownloadRequest(
            team_id=team.team_id,
            skill_code="skill-uuid",
            version_number="2",
            scope=SkillCenterReadScope.TEAM,
        )
    )
    assert download.version_number == "2"
    assert download.sha256
    assert download.download_url.startswith("file://")
    artifact = Path(unquote(urlparse(download.download_url).path))
    payload = artifact.read_bytes()
    assert zipfile.is_zipfile(artifact)
    assert hashlib.sha256(payload).hexdigest() == download.sha256
    assert download.office_download_url == download.download_url
    assert download.intranet_download_url == download.download_url
    assert download.mcp_services == ()

    assert [call.method for call in gateway.calls] == [
        "create_team",
        "get_team_by_ref",
        "submit_publish",
        "get_team_skill",
        "list_team_skills",
        "get_publish_status",
        "get_team_skill",
        "list_versions",
        "get_team_skill",
        "get_exact_download",
    ]


def test_local_gateway_reports_missing_team_reference_as_team_not_found(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_team_by_ref(
            SkillCenterTeamLookupRequest(
                ref_source="TEAMCLAW", ref_source_id="missing-space"
            )
        )

    assert raised.value.code is SkillCenterGatewayErrorCode.TEAM_NOT_FOUND
    assert len(gateway.calls_to("get_team_by_ref")) == 1


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


def test_gateway_public_catalog_preserves_documented_filters_and_metadata(
    world,
) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    request = SkillCenterPublicSkillSearchRequest(
        keyword="skill",
        tags=("研发效能",),
        official_only=True,
        recommended_only=False,
        sort_by=SkillCenterSortOrder.DOWNLOAD,
        creator_name="示例用户",
        creator_work_no="123456",
        belong_to=SkillCenterBelongTo.PERSONAL,
        page_num=2,
        page_size=50,
    )
    documented_skill = SkillCenterSkill(
        skill_id="123",
        skill_code="my-skill",
        skill_name="技能名称",
        description="技能描述",
        creator_id="522152",
        creator_work_no="123456",
        creator_name="示例用户",
        latest_version_number="1.0.0",
        official_version_number="1.0.0",
        updated_at="2026-04-10T14:30:00.000+08:00",
        icon_url="https://example.invalid/icon.png",
        access_level=SkillCenterAccessLevel.PUBLIC,
        belong_to=SkillCenterBelongTo.PERSONAL,
        owner_name="示例用户",
        homepage_url="https://example.invalid/skill/my-skill",
        office_download_url="https://example.invalid/office.zip",
        intranet_download_url="https://example.invalid/intranet.zip",
        sha256="abc123",
        tags=("研发效能",),
        favorite_count=10,
        download_count=100,
        is_official=True,
        is_recommended=False,
        is_test=False,
        network_types=("OFFICE",),
        antcode_url=None,
    )

    gateway.set_override(
        "search_public_skills",
        lambda actual: SkillCenterSkillPage(
            items=(documented_skill,),
            total=1,
            page_num=actual.page_num,
            page_size=actual.page_size,
        ),
    )

    result = service.search_public_skills(request)

    assert result.items == (documented_skill,)
    assert result.items[0].skill_code == "my-skill"
    assert result.items[0].creator_work_no == "123456"
    assert result.items[0].tags == ("研发效能",)
    assert result.items[0].office_download_url.endswith("office.zip")
    recorded_request = gateway.calls_to("search_public_skills")[0].args[0]
    assert recorded_request == request
    assert recorded_request.belong_to is SkillCenterBelongTo.PERSONAL


def test_local_gateway_round_trips_documented_public_skill_and_tags(world) -> None:
    service = world.get(SkillCenterGatewayService)
    team = service.create_team(
        SkillCenterTeamCreateRequest("space-doc", "Docs", "TEAMCLAW", "space-doc")
    )
    service.submit_publish(
        SkillCenterPublishSubmitRequest(
            team_id=team.team_id,
            skill_code="yuque-doc-skill",
            skill_name="语雀文档处理",
            version_number="v1.0",
            package_url="https://example.invalid/yuque.zip",
            description="读写语雀文档",
            icon_url="https://example.invalid/icon.png",
            tags=("研发效能",),
            visibility=SkillCenterVisibility.PUBLIC,
            creator_name="示例用户",
            creator_work_no="123456",
        )
    )

    page = service.search_public_skills(
        SkillCenterPublicSkillSearchRequest(
            keyword="语雀",
            tags=("研发效能",),
            sort_by=SkillCenterSortOrder.LATEST,
            creator_name="示例用户",
            creator_work_no="123456",
        )
    )
    detail = service.get_public_skill(
        SkillCenterPublicSkillDetailRequest("yuque-doc-skill")
    )
    tags = service.list_public_tags()

    assert page.items == (detail,)
    assert detail is not None
    assert type(detail) is SkillCenterSkill
    assert type(page.items[0]) is SkillCenterSkill
    assert "team_id" not in asdict(detail)
    assert "skill_status" not in asdict(detail)
    assert detail.skill_id is not None
    assert detail.skill_code == "yuque-doc-skill"
    assert detail.skill_name == "语雀文档处理"
    assert detail.icon_url == "https://example.invalid/icon.png"
    assert detail.access_level is SkillCenterAccessLevel.PUBLIC
    assert detail.belong_to is SkillCenterBelongTo.TEAM
    assert detail.tags == ("研发效能",)
    assert detail.latest_version_number == "v1.0"
    assert [tag.name for tag in tags] == ["研发效能"]

    # Public Reference materialization is intentionally not Team-scoped.
    versions = service.list_versions(
        SkillCenterVersionListRequest(
            skill_code="yuque-doc-skill", scope=SkillCenterReadScope.PUBLIC
        )
    )
    download = service.get_exact_download(
        SkillCenterExactDownloadRequest(
            skill_code="yuque-doc-skill",
            version_number="v1.0",
            scope=SkillCenterReadScope.PUBLIC,
        )
    )
    assert [version.version_number for version in versions] == ["v1.0"]
    assert download.version_number == "v1.0"
    assert download.sha256


def test_local_gateway_filters_public_catalog_by_belong_to(world) -> None:
    service = world.get(SkillCenterGatewayService)
    team = service.create_team(
        SkillCenterTeamCreateRequest("space-team", "Team", "TEAMCLAW", "space-team")
    )
    service.submit_publish(
        SkillCenterPublishSubmitRequest(
            team_id=team.team_id,
            skill_code="team-owned-skill",
            skill_name="Team Skill",
            version_number="1",
            package_url="https://example.invalid/team-skill.zip",
            visibility=SkillCenterVisibility.PUBLIC,
        )
    )

    team_page = service.search_public_skills(
        SkillCenterPublicSkillSearchRequest(belong_to=SkillCenterBelongTo.TEAM)
    )
    personal_page = service.search_public_skills(
        SkillCenterPublicSkillSearchRequest(belong_to=SkillCenterBelongTo.PERSONAL)
    )

    assert [skill.skill_code for skill in team_page.items] == ["team-owned-skill"]
    assert personal_page.items == ()


def test_local_gateway_rejects_cross_team_skill_code_reuse(world) -> None:
    service = world.get(SkillCenterGatewayService)
    owner_team = service.create_team(
        SkillCenterTeamCreateRequest("space-owner", "Owner", "TEAMCLAW", "space-owner")
    )
    another_team = service.create_team(
        SkillCenterTeamCreateRequest(
            "space-another", "Another", "TEAMCLAW", "space-another"
        )
    )
    service.submit_publish(
        SkillCenterPublishSubmitRequest(
            team_id=owner_team.team_id,
            skill_code="globally-unique-skill",
            skill_name="Owned Skill",
            version_number="1",
            package_url="https://example.invalid/owned.zip",
        )
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.submit_publish(
            SkillCenterPublishSubmitRequest(
                team_id=another_team.team_id,
                skill_code="globally-unique-skill",
                skill_name="Conflicting Skill",
                version_number="1",
                package_url="https://example.invalid/conflicting.zip",
            )
        )

    assert raised.value.code is SkillCenterGatewayErrorCode.BUSINESS
    assert service.get_team_skill(
        SkillCenterTeamSkillDetailRequest(
            team_id=owner_team.team_id, skill_code="globally-unique-skill"
        )
    ) is not None
    assert (
        service.get_team_skill(
            SkillCenterTeamSkillDetailRequest(
                team_id=another_team.team_id, skill_code="globally-unique-skill"
            )
        )
        is None
    )


def test_gateway_service_rejects_mismatched_publish_identity(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "submit_publish",
        lambda _request: SkillCenterPublishSubmission(
            skill_code="another-skill",
            version_number="9",
            status=SkillCenterPublishSubmissionState.ACCEPTED,
        ),
    )
    request = SkillCenterPublishSubmitRequest(
        team_id="team-1",
        skill_code="expected-skill",
        skill_name="Expected",
        version_number="1",
        package_url="https://example.invalid/package.zip",
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.submit_publish(request)

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL
    assert len(gateway.calls_to("submit_publish")) == 1


def test_gateway_service_rejects_cross_team_catalog_item(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "list_team_skills",
        lambda request: SkillCenterTeamSkillPage(
            items=(
                SkillCenterTeamSkill(
                    skill_code="skill",
                    skill_name="Skill",
                    access_level=SkillCenterAccessLevel.PRIVATE,
                    team_id="another-team",
                ),
            ),
            total=1,
            page_num=request.page_num,
            page_size=request.page_size,
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.list_team_skills(SkillCenterTeamSkillListRequest("expected-team"))

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL


def test_gateway_service_rejects_missing_team_context(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "get_team_skill",
        lambda request: SkillCenterSkill(
            skill_code=request.skill_code,
            skill_name="Skill",
            access_level=SkillCenterAccessLevel.PRIVATE,
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_team_skill(
            SkillCenterTeamSkillDetailRequest("expected-team", "skill")
        )

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL


def test_team_exact_download_rejects_cross_team_preflight(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "get_team_skill",
        lambda request: SkillCenterTeamSkill(
            skill_code=request.skill_code,
            skill_name="Skill",
            access_level=SkillCenterAccessLevel.PRIVATE,
            team_id="another-team",
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_exact_download(
            SkillCenterExactDownloadRequest(
                skill_code="skill",
                version_number="1",
                scope=SkillCenterReadScope.TEAM,
                team_id="expected-team",
            )
        )

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL
    assert gateway.calls_to("get_exact_download") == []


def test_gateway_service_rejects_private_skill_from_public_detail(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "get_public_skill",
        lambda request: SkillCenterSkill(
            skill_code=request.skill_code,
            skill_name="Private Skill",
            access_level=SkillCenterAccessLevel.PRIVATE,
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_public_skill(SkillCenterPublicSkillDetailRequest("private-skill"))

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL


def test_gateway_service_rejects_team_identity_from_public_detail(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "get_public_skill",
        lambda request: SkillCenterTeamSkill(
            skill_code=request.skill_code,
            skill_name="Public Skill",
            access_level=SkillCenterAccessLevel.PUBLIC,
            team_id="private-team-context",
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_public_skill(SkillCenterPublicSkillDetailRequest("public-skill"))

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL


def test_gateway_service_rejects_private_skill_from_public_search(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "search_public_skills",
        lambda request: SkillCenterSkillPage(
            items=(
                SkillCenterSkill(
                    skill_code="private-skill",
                    skill_name="Private Skill",
                    access_level=SkillCenterAccessLevel.PRIVATE,
                ),
            ),
            total=1,
            page_num=request.page_num,
            page_size=request.page_size,
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.search_public_skills(SkillCenterPublicSkillSearchRequest())

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL


def test_gateway_service_rejects_team_identity_from_public_search(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "search_public_skills",
        lambda request: SkillCenterSkillPage(
            items=(
                SkillCenterTeamSkill(
                    skill_code="public-skill",
                    skill_name="Public Skill",
                    access_level=SkillCenterAccessLevel.PUBLIC,
                    team_id="private-team-context",
                ),
            ),
            total=1,
            page_num=request.page_num,
            page_size=request.page_size,
        ),
    )

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.search_public_skills(SkillCenterPublicSkillSearchRequest())

    assert raised.value.code is SkillCenterGatewayErrorCode.PROTOCOL


def test_gateway_consumer_propagates_stable_error_without_retry(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "submit_publish",
        lambda request: (_ for _ in ()).throw(
            SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.TIMEOUT,
                "publish outcome is unknown",
                upstream_code="SC_TIMEOUT",
                trace_id="trace-publish-timeout",
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
    assert raised.value.upstream_code == "SC_TIMEOUT"
    assert raised.value.trace_id == "trace-publish-timeout"
    assert len(gateway.calls_to("submit_publish")) == 1


def test_local_gateway_rejects_an_unknown_publish_status_skill(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_publish_status(SkillCenterPublishStatusRequest("unknown-skill"))

    assert raised.value.code is SkillCenterGatewayErrorCode.BUSINESS
    assert len(gateway.calls_to("get_publish_status")) == 1


def test_local_gateway_rejects_an_unknown_exact_version(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)

    with pytest.raises(SkillCenterGatewayError) as raised:
        service.get_exact_download(
            SkillCenterExactDownloadRequest(
                "skill", "9", SkillCenterReadScope.TEAM, "unknown-team"
            )
        )

    assert raised.value.code is SkillCenterGatewayErrorCode.BUSINESS
    assert gateway.calls_to("get_exact_download") == []


def test_gateway_status_query_returns_the_current_sc_version(world) -> None:
    service = world.get(SkillCenterGatewayService)
    gateway = world.get(SkillCenterGateway)
    gateway.set_override(
        "get_publish_status",
        lambda request: SkillCenterPublishStatus(
            skill_code=request.skill_code,
            version_number="server-current-version",
            status=SkillCenterPublishState.PENDING,
            is_completed=False,
            is_success=False,
        ),
    )

    status = service.get_publish_status(SkillCenterPublishStatusRequest("skill"))

    assert status.version_number == "server-current-version"
    assert len(gateway.calls_to("get_publish_status")) == 1


def test_publish_reports_preserve_unknown_sc_fields_without_aliasing() -> None:
    standard_payload = {
        "passed": True,
        "checks": [{"ruleCode": "manifest", "result": "PASS"}],
    }
    security_payload = {
        "risk": "LOW",
        "details": {"scanner": "sc", "score": 0},
    }

    standard = SkillCenterStandardCheckResult(raw=standard_payload)
    security = SkillCenterSecurityCheckReport(raw=security_payload)
    standard_payload["checks"][0]["result"] = "MUTATED"
    security_payload["details"]["score"] = 99

    expected_standard = {
        "passed": True,
        "checks": [{"ruleCode": "manifest", "result": "PASS"}],
    }
    expected_security = {
        "risk": "LOW",
        "details": {"scanner": "sc", "score": 0},
    }
    assert standard.to_raw_dict() == expected_standard
    assert security.to_raw_dict() == expected_security
    assert json.loads(json.dumps(standard.to_raw_dict())) == expected_standard
    assert json.loads(json.dumps(security.to_raw_dict())) == expected_security
    with pytest.raises(TypeError):
        standard.raw["passed"] = False  # type: ignore[index]


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
        lambda: SkillCenterVersionListRequest(
            skill_code="skill", scope=SkillCenterReadScope.TEAM, team_id=""
        ),
        lambda: SkillCenterExactDownloadRequest(
            team_id="",
            skill_code="skill",
            version_number="1",
            scope=SkillCenterReadScope.TEAM,
        ),
    ],
)
def test_team_scoped_requests_reject_empty_team_id(factory) -> None:
    with pytest.raises(ValueError, match="team_id is required"):
        factory()


def test_publish_status_request_rejects_empty_skill_code() -> None:
    with pytest.raises(ValueError, match="skill_code is required"):
        SkillCenterPublishStatusRequest(skill_code="")


def test_publish_status_response_requires_sc_version() -> None:
    with pytest.raises(ValueError, match="version_number is required"):
        SkillCenterPublishStatus(
            skill_code="skill",
            version_number="",
            status=SkillCenterPublishState.PENDING,
            is_completed=False,
            is_success=False,
        )


def test_team_response_rejects_empty_team_id() -> None:
    with pytest.raises(ValueError, match="team_id is required"):
        SkillCenterTeam(
            team_id="",
            team_code="space-1",
            team_name="Space 1",
            ref_source="TEAMCLAW",
            ref_source_id="space-1",
        )


def test_version_response_rejects_empty_version_number() -> None:
    with pytest.raises(ValueError, match="version_number is required"):
        SkillCenterVersion(version_number="")


def test_publish_request_rejects_more_than_one_sc_tag() -> None:
    with pytest.raises(ValueError, match="at most one tag"):
        SkillCenterPublishSubmitRequest(
            team_id="team-1",
            skill_code="skill",
            skill_name="Skill",
            version_number="1",
            package_url="https://example.invalid/package.zip",
            tags=("研发效能", "代码辅助"),
        )


def test_public_version_read_rejects_a_team_id() -> None:
    with pytest.raises(ValueError, match="team_id must be omitted"):
        SkillCenterVersionListRequest(
            skill_code="skill",
            scope=SkillCenterReadScope.PUBLIC,
            team_id="team-1",
        )


def test_exact_download_rejects_a_non_sha256_digest() -> None:
    with pytest.raises(ValueError, match="64-character hexadecimal"):
        SkillCenterExactDownload(
            skill_code="skill",
            version_number="1",
            download_url="https://example.invalid/skill.zip",
            sha256="not-a-sha256",
        )


@pytest.mark.parametrize(("operation", "gateway_request"), _all_gateway_requests())
def test_community_gateway_fails_closed_as_unavailable(
    community_world, operation: str, gateway_request: object
) -> None:
    gateway = community_world.get(SkillCenterGateway)

    with pytest.raises(SkillCenterGatewayError) as raised:
        if gateway_request is None:
            getattr(gateway, operation)()
        else:
            getattr(gateway, operation)(gateway_request)

    assert raised.value.code is SkillCenterGatewayErrorCode.UNAVAILABLE
