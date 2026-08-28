"""Service API for validated public Skill Center catalogue reads."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterPublicSkillSearchRequest,
    SkillCenterSkillPage,
    SkillCenterTag,
)


@runtime_checkable
class SkillCenterGatewayServiceProtocol(Protocol):
    """Public catalogue subset consumed by HTTP delivery adapters."""

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage: ...

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]: ...
