"""Application service for listing Skills owned by a Space."""

from __future__ import annotations

from injector import inject

from agentclaw.community.api.space_skill_query_service import (
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
        return total, [
            self._to_summary(record, actor_id=actor_id) for record in records
        ]

    @staticmethod
    def _to_summary(
        record: SpaceSkillQueryRecord, *, actor_id: str
    ) -> SpaceSkillSummaryRecord:
        role = record["current_user_skill_role"]
        is_team = record["space_type"] == "TEAM"
        holder = record["lease_holder_user_id"]
        if record["draft_status"] is None:
            lease_summary = None
        elif not is_team:
            lease_summary = {
                "required": False,
                "state": "NOT_REQUIRED",
                "holder_user_id": None,
            }
        elif holder is None:
            lease_summary = {
                "required": True,
                "state": "AVAILABLE",
                "holder_user_id": None,
            }
        else:
            lease_summary = {
                "required": True,
                "state": "HELD_BY_SELF" if holder == actor_id else "HELD_BY_OTHER",
                "holder_user_id": holder,
            }
        return {
            **record,
            "can_edit": role in {"OWNER", "MANAGER"},
            "can_grant": is_team and role == "OWNER",
            "can_apply_edit": is_team and role is None,
            "lease_summary": lease_summary,
        }
