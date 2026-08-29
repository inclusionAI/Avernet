"""Application service for listing Skills owned by a Space."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.skill_center.space_skill_query_service_protocol import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillQueryRecord,
    SpaceSkillSummaryRecord,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.utils.env_utils import get_current_env


class SpaceSkillQueryService(SpaceSkillQueryServiceProtocol):
    """Authorize a Space member and delegate its Skill query to persistence."""

    @inject
    def __init__(
        self,
        access_service: SpaceAccessService,
        repository: SpaceSkillRepository,
    ) -> None:
        self._access_service = access_service
        self._repository = repository

    def list_space_skills(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSkillSummaryRecord]]:
        self._access_service.require_space_member(
            space_id=space_id,
            user_id=actor_id,
        )
        normalized_keyword = keyword.strip() if keyword else None
        if not normalized_keyword:
            normalized_keyword = None
        total, records = self._repository.list_space_skills(
            space_id=space_id,
            actor_id=actor_id,
            env=get_current_env(),
            keyword=normalized_keyword,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )
        return total, [self._to_summary(record) for record in records]

    @staticmethod
    def _to_summary(record: SpaceSkillQueryRecord) -> SpaceSkillSummaryRecord:
        role = record["current_user_skill_role"]
        is_team = record["space_type"] == "TEAM"
        return {
            **record,
            "can_edit": role in {"OWNER", "MANAGER"},
            "can_grant": is_team and role == "OWNER",
            "can_apply_edit": is_team and role is None,
        }
