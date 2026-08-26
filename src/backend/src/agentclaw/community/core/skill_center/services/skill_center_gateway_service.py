"""Consumer seam for the independent Skill Center Gateway Plugin API."""

from __future__ import annotations

from injector import inject

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterPublicSkillSearchRequest,
    SkillCenterPublishStatus,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishSubmission,
    SkillCenterPublishSubmitRequest,
    SkillCenterSkill,
    SkillCenterSkillPage,
    SkillCenterTag,
    SkillCenterTeam,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamLookupRequest,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterTeamSkillListRequest,
    SkillCenterVersionListRequest,
    SkillCenterVersion,
)


class SkillCenterGatewayService:
    """Typed application consumer with no publication-domain state or policy."""

    @inject
    def __init__(self, gateway: SkillCenterGateway) -> None:
        self._gateway = gateway

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeam:
        return self._gateway.create_team(request)

    def get_team_by_ref(
        self, request: SkillCenterTeamLookupRequest
    ) -> SkillCenterTeam | None:
        return self._gateway.get_team_by_ref(request)

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage:
        return self._gateway.search_public_skills(request)

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None:
        return self._gateway.get_public_skill(request)

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]:
        return self._gateway.list_public_tags()

    def list_team_skills(
        self, request: SkillCenterTeamSkillListRequest
    ) -> SkillCenterSkillPage:
        return self._gateway.list_team_skills(request)

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterSkill | None:
        return self._gateway.get_team_skill(request)

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission:
        return self._gateway.submit_publish(request)

    def get_publish_status(
        self, request: SkillCenterPublishStatusRequest
    ) -> SkillCenterPublishStatus:
        return self._gateway.get_publish_status(request)

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]:
        return self._gateway.list_versions(request)

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload:
        return self._gateway.get_exact_download(request)
