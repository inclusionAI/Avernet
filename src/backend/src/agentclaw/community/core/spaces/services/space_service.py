"""Space lifecycle service."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import SpaceNameInvalidError
from agentclaw.community.core.spaces.models import (
    SpaceRecord,
    SpaceSummaryRecord,
    SpaceType,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterTeamCreateRequest,
)
from agentclaw.community.utils.env_utils import get_current_env


class SpaceService:
    @inject
    def __init__(
        self,
        repository: SpaceRepositoryProtocol,
        skill_center_client: SkillCenterClient,
    ) -> None:
        self._repository = repository
        self._skill_center_client = skill_center_client

    def initialize_personal(self, *, user_id: str) -> tuple[SpaceRecord, bool]:
        return self._repository.initialize_personal(
            user_id=user_id, env=get_current_env()
        )

    def create_team(self, *, name: str, creator_id: str) -> SpaceRecord:
        normalized = name.strip()
        if not normalized or len(normalized) > 128:
            raise SpaceNameInvalidError("space name must contain 1-128 characters")
        with self._repository.create_team_transaction(
            name=normalized, creator_id=creator_id, env=get_current_env()
        ) as record:
            result = self._skill_center_client.create_team(
                SkillCenterTeamCreateRequest(
                    team_code=record.space_code,
                    team_name=record.name,
                    ref_source_id=str(record.id),
                )
            )
            record.sc_team_id = result.team_id
        return record

    def list_spaces(
        self,
        *,
        user_id: str,
        keyword: str | None,
        space_type: SpaceType | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceSummaryRecord]]:
        return self._repository.list_spaces(
            user_id=user_id,
            env=get_current_env(),
            keyword=keyword.strip() if keyword and keyword.strip() else None,
            space_type=space_type.value if space_type is not None else None,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )
