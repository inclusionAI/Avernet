"""Deterministic offline Fake for the independent SkillCenterGateway."""

from __future__ import annotations

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
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
    SkillCenterPublishState,
    SkillCenterPublishSubmission,
    SkillCenterPublishSubmissionState,
    SkillCenterPublishSubmitRequest,
    SkillCenterSkill,
    SkillCenterSkillPage,
    SkillCenterTeam,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamLookupRequest,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterTeamSkillListRequest,
    SkillCenterVersion,
    SkillCenterVersionListRequest,
    SkillCenterVersionPage,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.FAKE, rationale="in-memory SC model")
class LocalSkillCenterGateway(MockSeam, SkillCenterGateway):
    """Small stateful Fake: one submission is immediately queryable."""

    def __init__(self) -> None:
        self._teams_by_ref: dict[tuple[str, str], SkillCenterTeam] = {}
        self._skills: dict[tuple[str, str], SkillCenterSkill] = {}
        self._versions: dict[tuple[str, str], list[SkillCenterVersion]] = {}

    @staticmethod
    def _missing(message: str) -> None:
        raise SkillCenterGatewayError(SkillCenterGatewayErrorCode.BUSINESS, message)

    def _has_team(self, team_id: str) -> bool:
        return any(team.team_id == team_id for team in self._teams_by_ref.values())

    def _has_version(self, team_id: str, skill_code: str, version: str) -> bool:
        return any(
            item.version_number == version
            for item in self._versions.get((team_id, skill_code), ())
        )

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeam:
        key = (request.ref_source, request.ref_source_id)
        existing = self._teams_by_ref.get(key)
        if existing is not None:
            return existing
        team = SkillCenterTeam(
            team_id=f"local-team-{len(self._teams_by_ref) + 1}",
            team_code=request.team_code,
            team_name=request.team_name,
            ref_source=request.ref_source,
            ref_source_id=request.ref_source_id,
        )
        self._teams_by_ref[key] = team
        return team

    def get_team_by_ref(
        self, request: SkillCenterTeamLookupRequest
    ) -> SkillCenterTeam | None:
        return self._teams_by_ref.get((request.ref_source, request.ref_source_id))

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage:
        return SkillCenterSkillPage((), 0, request.page_num, request.page_size)

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None:
        return None

    def list_team_skills(
        self, request: SkillCenterTeamSkillListRequest
    ) -> SkillCenterSkillPage:
        items = tuple(
            skill
            for (team_id, _), skill in self._skills.items()
            if team_id == request.team_id
            and (
                request.keyword is None
                or request.keyword.casefold() in skill.skill_name.casefold()
            )
        )
        start = (request.page_num - 1) * request.page_size
        page = items[start : start + request.page_size]
        return SkillCenterSkillPage(page, len(items), request.page_num, request.page_size)

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterSkill | None:
        return self._skills.get((request.team_id, request.skill_code))

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission:
        if not self._has_team(request.team_id):
            self._missing(f"team {request.team_id} does not exist")
        key = (request.team_id, request.skill_code)
        self._skills[key] = SkillCenterSkill(
            skill_code=request.skill_code,
            skill_name=request.skill_name,
            description=request.description or "",
            latest_version_number=request.version_number,
            team_id=request.team_id,
        )
        versions = self._versions.setdefault(key, [])
        if all(v.version_number != request.version_number for v in versions):
            versions.append(SkillCenterVersion(version_number=request.version_number))
        return SkillCenterPublishSubmission(
            skill_code=request.skill_code,
            version_number=request.version_number,
            status=SkillCenterPublishSubmissionState.ACCEPTED,
        )

    def get_publish_status(
        self, request: SkillCenterPublishStatusRequest
    ) -> SkillCenterPublishStatus:
        if not self._has_version(
            request.team_id, request.skill_code, request.version_number
        ):
            self._missing(
                f"skill {request.skill_code} version {request.version_number} "
                f"does not exist in team {request.team_id}"
            )
        return SkillCenterPublishStatus(
            skill_code=request.skill_code,
            version_number=request.version_number,
            status=SkillCenterPublishState.PUBLISHED,
        )

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> SkillCenterVersionPage:
        versions = tuple(self._versions.get((request.team_id, request.skill_code), ()))
        start = (request.page_num - 1) * request.page_size
        page = versions[start : start + request.page_size]
        return SkillCenterVersionPage(
            page, len(versions), request.page_num, request.page_size
        )

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload:
        if not self._has_version(
            request.team_id, request.skill_code, request.version_number
        ):
            self._missing(
                f"skill {request.skill_code} version {request.version_number} "
                f"does not exist in team {request.team_id}"
            )
        return SkillCenterExactDownload(
            skill_code=request.skill_code,
            version_number=request.version_number,
            download_url=(
                f"file:///local-skill-center/{request.team_id}/"
                f"{request.skill_code}/{request.version_number}.zip"
            ),
        )
