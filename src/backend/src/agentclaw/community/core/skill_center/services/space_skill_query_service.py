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
from agentclaw.community.core.skill_center.services.space_skill_grant_service import (
    space_skill_actor_permissions,
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
        space, member = self._access_service.require_space_member(
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
            self._to_summary(
                record,
                actor_id=actor_id,
                space_type=space.space_type,
                space_role=member.role,
            )
            for record in records
        ]

    @staticmethod
    def _to_summary(
        record: SpaceSkillQueryRecord,
        *,
        actor_id: str,
        space_type,
        space_role,
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
                "holder_display_name": None,
            }
        elif holder is None:
            lease_summary = {
                "required": True,
                "state": "FREE",
                "holder_user_id": None,
                "holder_display_name": None,
            }
        else:
            lease_summary = {
                "required": True,
                "state": "HELD_BY_ME" if holder == actor_id else "HELD_BY_OTHER",
                "holder_user_id": holder,
                "holder_display_name": record["lease_holder_display_name"],
            }
        return {
            "id": record["id"],
            "skill_uuid": record["skill_uuid"],
            "name": record["name"],
            "description": record["description"],
            "status": record["status"],
            "draft_status": record["draft_status"],
            "space_type": record["space_type"],
            "actor": {
                "skill_role": role,
                "permissions": space_skill_actor_permissions(
                    space_type=space_type,
                    space_role=space_role,
                    skill_role=role,
                ),
            },
            "lease_summary": lease_summary,
            "gmt_created": record["gmt_created"],
            "gmt_modified": record["gmt_modified"],
        }
