"""Consumer seam for the independent Skill Center Gateway Plugin API."""

from __future__ import annotations

from injector import inject

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterExactDownloadRequest,
    SkillCenterAccessLevel,
    SkillCenterGateway,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterPublicSkillSearchRequest,
    SkillCenterReadScope,
    SkillCenterPublishStatus,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishState,
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


class SkillCenterGatewayService:
    """Typed consumer that rejects identity drift at the SC trust boundary.

    Attempt/retry/materialization policy stays in its owning application
    service. This seam only guarantees that an adapter cannot silently return
    a different Team, Skill, page, or exact version than the caller requested.
    """

    @inject
    def __init__(self, gateway: SkillCenterGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _reject(message: str) -> None:
        raise SkillCenterGatewayError(SkillCenterGatewayErrorCode.PROTOCOL, message)

    @classmethod
    def _validate_page(
        cls,
        page: SkillCenterSkillPage | SkillCenterTeamSkillPage,
        *,
        page_num: int,
        page_size: int,
    ) -> None:
        if page.page_num != page_num or page.page_size != page_size:
            cls._reject("Skill Center returned pagination for a different request")

    def _validate_public_read(self, skill_code: str) -> None:
        skill = self.get_public_skill(SkillCenterPublicSkillDetailRequest(skill_code))
        if skill is None:
            raise SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.BUSINESS,
                f"public Skill {skill_code} does not exist",
            )

    def _validate_team_read(self, team_id: str, skill_code: str) -> None:
        skill = self.get_team_skill(SkillCenterTeamSkillDetailRequest(team_id, skill_code))
        if skill is None:
            raise SkillCenterGatewayError(
                SkillCenterGatewayErrorCode.BUSINESS,
                f"Skill {skill_code} does not exist in Team {team_id}",
            )

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeam:
        team = self._gateway.create_team(request)
        if (
            team.team_code != request.team_code
            or team.ref_source != request.ref_source
            or team.ref_source_id != request.ref_source_id
        ):
            self._reject("Skill Center returned a different Team identity")
        return team

    def get_team_by_ref(
        self, request: SkillCenterTeamLookupRequest
    ) -> SkillCenterTeam:
        team = self._gateway.get_team_by_ref(request)
        if (
            team.ref_source != request.ref_source
            or team.ref_source_id != request.ref_source_id
        ):
            self._reject("Skill Center returned a Team for a different reference")
        return team

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage:
        page = self._gateway.search_public_skills(request)
        self._validate_page(page, page_num=request.page_num, page_size=request.page_size)
        if any(
            isinstance(item, SkillCenterTeamSkill)
            or item.access_level is not SkillCenterAccessLevel.PUBLIC
            for item in page.items
        ):
            self._reject("Skill Center returned a non-public catalogue item")
        return page

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None:
        skill = self._gateway.get_public_skill(request)
        if skill is not None and (
            isinstance(skill, SkillCenterTeamSkill)
            or skill.skill_code != request.skill_code
            or skill.access_level is not SkillCenterAccessLevel.PUBLIC
        ):
            self._reject("Skill Center returned a non-public or different Skill")
        return skill

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]:
        return self._gateway.list_public_tags()

    def list_team_skills(
        self, request: SkillCenterTeamSkillListRequest
    ) -> SkillCenterTeamSkillPage:
        page = self._gateway.list_team_skills(request)
        self._validate_page(page, page_num=request.page_num, page_size=request.page_size)
        if any(
            not isinstance(item, SkillCenterTeamSkill)
            or item.team_id != request.team_id
            for item in page.items
        ):
            self._reject("Skill Center returned a Skill from a different Team")
        return page

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterTeamSkill | None:
        skill = self._gateway.get_team_skill(request)
        if skill is None:
            return None
        if not isinstance(skill, SkillCenterTeamSkill):
            self._reject("Skill Center omitted Team Skill identity")
        if (
            skill.skill_code != request.skill_code or skill.team_id != request.team_id
        ):
            self._reject("Skill Center returned a different Team Skill")
        return skill

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission:
        submission = self._gateway.submit_publish(request)
        if (
            submission.skill_code != request.skill_code
            or submission.version_number != request.version_number
        ):
            self._reject("Skill Center accepted a different Skill version")
        return submission

    def get_publish_status(
        self, request: SkillCenterPublishStatusRequest
    ) -> SkillCenterPublishStatus:
        status = self._gateway.get_publish_status(request)
        if status.skill_code != request.skill_code:
            self._reject("Skill Center returned status for a different Skill")
        expected_completed = status.status is not SkillCenterPublishState.PENDING
        expected_success = status.status is SkillCenterPublishState.PUBLISHED
        if (
            status.is_completed is not expected_completed
            or status.is_success is not expected_success
        ):
            self._reject("Skill Center returned inconsistent publish status facts")
        return status

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]:
        if request.scope is SkillCenterReadScope.PUBLIC:
            self._validate_public_read(request.skill_code)
        else:
            assert request.team_id is not None
            self._validate_team_read(request.team_id, request.skill_code)
        versions = self._gateway.list_versions(request)
        version_numbers = [item.version_number for item in versions]
        if len(version_numbers) != len(set(version_numbers)):
            self._reject("Skill Center returned duplicate version numbers")
        return versions

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload:
        if request.scope is SkillCenterReadScope.PUBLIC:
            self._validate_public_read(request.skill_code)
        else:
            assert request.team_id is not None
            self._validate_team_read(request.team_id, request.skill_code)
        download = self._gateway.get_exact_download(request)
        if (
            download.skill_code != request.skill_code
            or download.version_number != request.version_number
        ):
            self._reject("Skill Center returned a different exact download")
        return download
