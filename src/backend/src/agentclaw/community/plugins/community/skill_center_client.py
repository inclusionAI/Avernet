"""Community ``SkillCenterClient`` — Skill Center marketplace is unsupported.

A real, deployable impl (not a ``MockSeam`` test double). The Skill Center
marketplace is an internal product the community build does not run (unused in
production); the real skills pipeline is the local skills source (see
``CommunitySkillRepoSync``). Every method raises ``SkillCenterUnsupportedError``
so a caller that actually tries to use the marketplace fails loudly rather than
silently receiving a fake result.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient


class SkillCenterUnsupportedError(RuntimeError):
    """Raised when a Skill Center marketplace operation is invoked in the
    community build, which has no Skill Center."""


_MSG = "Skill Center is not available in the community build"


class CommunitySkillCenterClient(SkillCenterClient):
    """Skill Center marketplace bypass for the community profile (raises)."""

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
