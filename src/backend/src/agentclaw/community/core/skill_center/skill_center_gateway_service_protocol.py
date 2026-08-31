"""Service API for validated public Skill Center catalogue reads."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterExactDownloadRequest,
    SkillCenterPublicSkillSearchRequest,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterSkill,
    SkillCenterSkillPage,
    SkillCenterTag,
    SkillCenterVersion,
    SkillCenterVersionListRequest,
)


@runtime_checkable
class SkillCenterGatewayServiceProtocol(Protocol):
    """Public catalogue subset consumed by HTTP delivery adapters."""

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage: ...

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]: ...

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None: ...

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]: ...

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload: ...
