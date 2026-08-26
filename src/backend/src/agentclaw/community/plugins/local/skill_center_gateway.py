"""Deterministic offline Fake for the independent SkillCenterGateway."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterExactDownloadRequest,
    SkillCenterAccessLevel,
    SkillCenterBelongTo,
    SkillCenterCheckFinding,
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
    SkillCenterPublishSubmission,
    SkillCenterPublishSubmissionState,
    SkillCenterPublishSubmitRequest,
    SkillCenterSkill,
    SkillCenterSkillPage,
    SkillCenterSortOrder,
    SkillCenterStandardCheckResult,
    SkillCenterTag,
    SkillCenterTeam,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamLookupRequest,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterTeamSkillListRequest,
    SkillCenterTeamSkill,
    SkillCenterTeamSkillPage,
    SkillCenterVersion,
    SkillCenterVersionListRequest,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.FAKE, rationale="in-memory SC model")
class LocalSkillCenterGateway(MockSeam, SkillCenterGateway):
    """Small stateful Fake: one submission is immediately queryable."""

    def __init__(self) -> None:
        self._teams_by_ref: dict[tuple[str, str], SkillCenterTeam] = {}
        self._skills: dict[tuple[str, str], SkillCenterTeamSkill] = {}
        self._versions: dict[tuple[str, str], list[SkillCenterVersion]] = {}
        self._artifact_dir = tempfile.TemporaryDirectory(
            prefix="avernet-local-skill-center-"
        )
        self._artifacts: dict[tuple[str, str, str], tuple[str, str]] = {}

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

    def _resolve_read_team_id(
        self, scope: SkillCenterReadScope, team_id: str | None, skill_code: str
    ) -> str:
        if scope is SkillCenterReadScope.TEAM:
            assert team_id is not None
            return team_id
        public_matches = [
            candidate_team_id
            for (candidate_team_id, candidate_code), skill in self._skills.items()
            if candidate_code == skill_code
            and skill.access_level is SkillCenterAccessLevel.PUBLIC
        ]
        if len(public_matches) != 1:
            self._missing(f"public skill {skill_code} does not exist")
        return public_matches[0]

    def _pathlib_write_artifact(
        self, request: SkillCenterPublishSubmitRequest
    ) -> None:
        manifest = (
            "---\n"
            f"name: {json.dumps(request.skill_name, ensure_ascii=False)}\n"
            f"description: {json.dumps(request.description or '', ensure_ascii=False)}\n"
            "---\n"
        ).encode()
        buffer = io.BytesIO()
        info = zipfile.ZipInfo("SKILL.md", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(info, manifest)
        payload = buffer.getvalue()
        artifact_id = hashlib.sha256(
            f"{request.team_id}\0{request.skill_code}\0{request.version_number}".encode()
        ).hexdigest()
        path = Path(self._artifact_dir.name, f"{artifact_id}.zip")
        path.write_bytes(payload)
        self._artifacts[
            (request.team_id, request.skill_code, request.version_number)
        ] = (path.as_uri(), hashlib.sha256(payload).hexdigest())

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
        items = [
            skill
            for skill in self._skills.values()
            if skill.access_level is SkillCenterAccessLevel.PUBLIC
            and (
                request.keyword is None
                or request.keyword.casefold() in skill.skill_name.casefold()
                or request.keyword.casefold() in (skill.description or "").casefold()
            )
            and (not request.tags or bool(set(request.tags) & set(skill.tags)))
            and (
                request.official_only is None
                or skill.is_official is request.official_only
            )
            and (
                request.recommended_only is None
                or skill.is_recommended is request.recommended_only
            )
            and (
                request.creator_name is None
                or request.creator_name.casefold()
                in (skill.creator_name or "").casefold()
            )
            and (
                request.creator_work_no is None
                or request.creator_work_no == skill.creator_work_no
            )
        ]
        if request.sort_by is SkillCenterSortOrder.OLDEST:
            pass
        elif request.sort_by is SkillCenterSortOrder.DOWNLOAD:
            items.sort(key=lambda item: item.download_count or 0, reverse=True)
        elif request.sort_by is SkillCenterSortOrder.FAVORITE:
            items.sort(key=lambda item: item.favorite_count or 0, reverse=True)
        else:
            items.reverse()
        start = (request.page_num - 1) * request.page_size
        page = items[start : start + request.page_size]
        return SkillCenterSkillPage(
            tuple(page), len(items), request.page_num, request.page_size
        )

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None:
        return next(
            (
                skill
                for (_, skill_code), skill in self._skills.items()
                if skill_code == request.skill_code
                and skill.access_level is SkillCenterAccessLevel.PUBLIC
            ),
            None,
        )

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]:
        names = sorted(
            {
                tag
                for skill in self._skills.values()
                if skill.access_level is SkillCenterAccessLevel.PUBLIC
                for tag in skill.tags
            }
        )
        return tuple(
            SkillCenterTag(tag_id=str(index), name=name)
            for index, name in enumerate(names, start=1)
        )

    def list_team_skills(
        self, request: SkillCenterTeamSkillListRequest
    ) -> SkillCenterTeamSkillPage:
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
        return SkillCenterTeamSkillPage(
            page, len(items), request.page_num, request.page_size
        )

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterTeamSkill | None:
        return self._skills.get((request.team_id, request.skill_code))

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission:
        if not self._has_team(request.team_id):
            self._missing(f"team {request.team_id} does not exist")
        key = (request.team_id, request.skill_code)
        self._skills[key] = SkillCenterTeamSkill(
            skill_code=request.skill_code,
            skill_name=request.skill_name,
            description=request.description or "",
            skill_id=f"local-skill-{len(self._skills) + 1}",
            creator_name=request.creator_name,
            creator_work_no=request.creator_work_no,
            latest_version_number=request.version_number,
            icon_url=request.icon_url,
            access_level=SkillCenterAccessLevel(request.visibility.value),
            belong_to=SkillCenterBelongTo.TEAM,
            tags=request.tags,
            is_official=False,
            is_recommended=False,
            is_test=False,
            team_id=request.team_id,
        )
        versions = self._versions.setdefault(key, [])
        if all(v.version_number != request.version_number for v in versions):
            versions.append(SkillCenterVersion(version_number=request.version_number))
        self._pathlib_write_artifact(request)
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
            is_completed=True,
            is_success=True,
            skill_name=self._skills[(request.team_id, request.skill_code)].skill_name,
            upstream_status="PUBLISHED",
            status_description="已发布",
            source="LOCAL",
            standard_check_result=SkillCenterStandardCheckResult(
                findings=(SkillCenterCheckFinding("manifest", "PASSED"),)
            ),
            security_check_report=SkillCenterSecurityCheckReport(
                risk_level="LOW",
                findings=(SkillCenterCheckFinding("package", "PASSED"),),
            ),
        )

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]:
        team_id = self._resolve_read_team_id(
            request.scope, request.team_id, request.skill_code
        )
        return tuple(self._versions.get((team_id, request.skill_code), ()))

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload:
        team_id = self._resolve_read_team_id(
            request.scope, request.team_id, request.skill_code
        )
        if not self._has_version(
            team_id, request.skill_code, request.version_number
        ):
            self._missing(
                f"skill {request.skill_code} version {request.version_number} "
                f"does not exist in team {team_id}"
            )
        download_url, sha256 = self._artifacts[
            (team_id, request.skill_code, request.version_number)
        ]
        return SkillCenterExactDownload(
            skill_code=request.skill_code,
            version_number=request.version_number,
            download_url=download_url,
            sha256=sha256,
            office_download_url=download_url,
            intranet_download_url=download_url,
        )
