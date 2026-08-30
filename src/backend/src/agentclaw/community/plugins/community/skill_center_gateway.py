"""Community adapter for SkillCenterGateway: fail closed as unavailable."""

from __future__ import annotations

from typing import NoReturn

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterExactDownloadRequest,
    SkillCenterGateway,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
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
    SkillCenterTeamSkill,
    SkillCenterTeamSkillPage,
    SkillCenterVersionListRequest,
    SkillCenterVersion,
)


class CommunitySkillCenterGateway(SkillCenterGateway):
    """The public distribution has no configured Skill Center service."""

    @staticmethod
    def _unavailable() -> NoReturn:
        raise SkillCenterGatewayError(
            SkillCenterGatewayErrorCode.UNAVAILABLE,
            "Skill Center is not available in the community build",
        )

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeam:
        self._unavailable()

    def get_team_by_ref(
        self, request: SkillCenterTeamLookupRequest
    ) -> SkillCenterTeam:
        self._unavailable()

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage:
        self._unavailable()

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None:
        self._unavailable()

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]:
        self._unavailable()

    def list_team_skills(
        self, request: SkillCenterTeamSkillListRequest
    ) -> SkillCenterTeamSkillPage:
        self._unavailable()

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterTeamSkill | None:
        self._unavailable()

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission:
        self._unavailable()

    def get_publish_status(
        self, request: SkillCenterPublishStatusRequest
    ) -> SkillCenterPublishStatus:
        self._unavailable()

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]:
        self._unavailable()

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload:
        self._unavailable()
