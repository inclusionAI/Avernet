"""Narrow Service API for the Team-scoped Publication consumer."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterPublishStatus,
    SkillCenterPublishStatusRequest,
    SkillCenterPublishSubmission,
    SkillCenterPublishSubmitRequest,
    SkillCenterTeamSkill,
    SkillCenterTeamSkillDetailRequest,
    SkillCenterVersion,
    SkillCenterVersionListRequest,
)


@runtime_checkable
class SkillCenterPublicationGatewayProtocol(Protocol):
    """Validated Team publish/status/version subset used only by Publication."""

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterTeamSkill | None: ...

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission: ...

    def get_publish_status(
        self, request: SkillCenterPublishStatusRequest
    ) -> SkillCenterPublishStatus: ...

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]: ...


__all__ = ["SkillCenterPublicationGatewayProtocol"]
