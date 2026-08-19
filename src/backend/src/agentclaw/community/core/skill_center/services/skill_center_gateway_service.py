"""Upper-layer consumer for the team-scoped Skill Center Gateway.

This service owns no Space mapping, Publication Attempt, Version, retry, or
materialization policy. Its callers provide already-resolved request data; it
keeps the core's dependency on the Q5 Plugin API explicit and testable.
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterGateway,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamCreateResult,
    SkillCenterTeamSkillPage,
)


class SkillCenterGatewayService:
    """Application-facing consumer of the Q5 transport boundary."""

    @inject
    def __init__(self, gateway: SkillCenterGateway) -> None:
        self._gateway = gateway

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeamCreateResult:
        return self._gateway.create_team(request)

    def get_team_by_ref_source(self, *, ref_source_platform: str, ref_source_id: str) -> SkillCenterTeamCreateResult:
        return self._gateway.get_team_by_ref_source(ref_source_platform=ref_source_platform, ref_source_id=ref_source_id)

    def list_team_skills(self, *, team_id: int, keyword: str = "", page_num: int = 1, page_size: int = 20) -> SkillCenterTeamSkillPage:
        return self._gateway.list_team_skills(team_id=team_id, keyword=keyword, page_num=page_num, page_size=page_size)

    def submit_publish(self, payload: dict, *, team_id: str) -> dict:
        """Submit exactly one publish request; caller owns retry decisions."""
        return self._gateway.upload_and_publish(payload, team_id=team_id)

    def get_publish_status(self, skill_code: str, *, team_id: str) -> dict:
        return self._gateway.query_publish_status(skill_code, team_id=team_id)

    def get_skill_detail(self, skill_code: str, *, team_id: str) -> dict:
        return self._gateway.get_skill_detail(skill_code, team_id=team_id)

    def list_versions(self, skill_code: str, *, team_id: str) -> list[dict]:
        return self._gateway.list_versions(skill_code, team_id=team_id)

    def get_download_url(self, skill_code: str, version_number: str, *, team_id: str) -> dict:
        return self._gateway.get_download_url(skill_code, version_number, team_id=team_id)

    def search_market_skills(
        self, keyword: str = "", tag: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        return self._gateway.search_market_skills(keyword, tag, page, page_size)

    def get_market_tags(self) -> list[dict]:
        return self._gateway.get_market_tags()
