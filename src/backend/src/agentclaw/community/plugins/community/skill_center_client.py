"""Community ``SkillCenterClient`` — Skill Center marketplace is unsupported.

A real, deployable impl (not a ``MockSeam`` test double). The Skill Center
marketplace is an internal product the community build does not run (unused in
production); the real skills pipeline is the local skills source (see
``CommunitySkillRepoSync``). Every method raises ``SkillCenterUnsupportedError``
so a caller that actually tries to use the marketplace fails loudly rather than
silently receiving a fake result.
"""

from __future__ import annotations

from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterGateway,
    SkillCenterTeamCreateRequest,
    SkillCenterTeamCreateResult,
    SkillCenterTeamSkillPage,
    SkillCenterGatewayError,
    SkillCenterGatewayErrorCode,
    SkillCenterTeamCreateError,
)


class SkillCenterUnsupportedError(RuntimeError):
    """Raised when a Skill Center marketplace operation is invoked in the
    community build, which has no Skill Center."""


_MSG = "Skill Center is not available in the community build"


class CommunitySkillCenterClient(SkillCenterClient):
    """Skill Center marketplace bypass for the community profile (raises)."""

    def create_team(
        self, request: SkillCenterTeamCreateRequest
    ) -> SkillCenterTeamCreateResult:
        raise SkillCenterTeamCreateError(_MSG)

    def upload_and_publish(self, payload: dict) -> dict:
        raise SkillCenterUnsupportedError(_MSG)

    def query_publish_status(self, skill_code: str) -> dict:
        raise SkillCenterUnsupportedError(_MSG)

    def list_versions(self, skill_code: str) -> list[dict]:
        raise SkillCenterUnsupportedError(_MSG)

    def search_market_skills(
        self,
        keyword: str = "",
        tag: str = "",
        page: int = 1,
        page_size: int = 20,
        team_id: str | None = None,
    ) -> dict:
        raise SkillCenterUnsupportedError(_MSG)

    def get_market_tags(self) -> list[dict]:
        raise SkillCenterUnsupportedError(_MSG)

    def get_download_url(self, skill_code: str, version_number: str) -> dict:
        raise SkillCenterUnsupportedError(_MSG)

    def get_file_structure(self, skill_code: str, version: str = "") -> dict:
        raise SkillCenterUnsupportedError(_MSG)

    def get_file_content(
        self, skill_code: str, file_path: str, version: str = ""
    ) -> dict:
        raise SkillCenterUnsupportedError(_MSG)

    def delete_skill(self, skill_code: str) -> dict:
        raise SkillCenterUnsupportedError(_MSG)


class CommunitySkillCenterGateway(SkillCenterGateway):
    """Public-build Q5 seam; the Corp adapter owns SC HTTP/config/auth wiring."""

    @staticmethod
    def _unsupported() -> None:
        raise SkillCenterGatewayError(SkillCenterGatewayErrorCode.UNAVAILABLE, _MSG)

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeamCreateResult:
        self._unsupported()

    def get_team_by_ref_source(self, *, ref_source_platform: str, ref_source_id: str) -> SkillCenterTeamCreateResult:
        self._unsupported()

    def list_team_skills(self, *, team_id: int, keyword: str = "", page_num: int = 1, page_size: int = 20) -> SkillCenterTeamSkillPage:
        self._unsupported()

    def upload_and_publish(self, payload: dict, *, team_id: str) -> dict:
        self._unsupported()

    def query_publish_status(self, skill_code: str, *, team_id: str) -> dict:
        self._unsupported()

    def get_skill_detail(self, skill_code: str, *, team_id: str) -> dict:
        self._unsupported()

    def list_versions(self, skill_code: str, *, team_id: str) -> list[dict]:
        self._unsupported()

    def get_download_url(self, skill_code: str, version_number: str, *, team_id: str) -> dict:
        self._unsupported()

    def search_market_skills(
        self, keyword: str = "", tag: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        self._unsupported()

    def get_market_tags(self) -> list[dict]:
        self._unsupported()
