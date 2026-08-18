"""Upper-layer consumer for the team-scoped Skill Center Gateway.

This service owns no Space mapping, Publication Attempt, Version, retry, or
materialization policy. Its callers provide already-resolved request data; it
keeps the core's dependency on the Q5 Plugin API explicit and testable.
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.plugin_api.skill_center_client import SkillCenterGateway


class SkillCenterGatewayService:
    """Application-facing consumer of the Q5 transport boundary."""

    @inject
    def __init__(self, gateway: SkillCenterGateway) -> None:
        self._gateway = gateway

    def create_team(self, *, name: str, request_id: str) -> dict:
        return self._gateway.create_team(name=name, request_id=request_id)

    def close_team(self, team_id: str) -> dict:
        return self._gateway.close_team(team_id)

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
