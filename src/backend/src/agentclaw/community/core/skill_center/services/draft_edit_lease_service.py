"""Policy service for permanent, fencing-token protected Draft Edit Leases."""

from __future__ import annotations

from collections.abc import Callable

from agentclaw.community.core.repository.protocols.skill_center import (
    DraftEditLeaseRepository,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    DraftEditLeaseRecord,
    DraftEditLeaseViewRecord,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseForbiddenError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.skill_center.protocols import (
    SpaceSkillEditorAccessProtocol,
)
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol


class DraftEditLeaseService:
    """Keep Lease policy transport-free and reuse the canonical Grant seam."""

    def __init__(
        self,
        access: SpaceAccessServiceProtocol,
        grants: SpaceSkillEditorAccessProtocol,
        repository: DraftEditLeaseRepository,
        env_provider: Callable[[], str],
    ) -> None:
        self._access = access
        self._grants = grants
        self._repository = repository
        self._env_provider = env_provider

    def get_lease(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftEditLeaseViewRecord:
        if not self._required(space_id=space_id, actor_id=actor_id):
            self._require_personal_draft(space_id=space_id, skill_id=skill_id)
            return self._not_required()
        self._require_editor(space_id=space_id, skill_id=skill_id, actor_id=actor_id)
        record = self._repository.get_lease(
            space_id=space_id,
            skill_id=skill_id,
            env=self._env_provider(),
        )
        return self._present(record, actor_id=actor_id)

    def acquire(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftEditLeaseViewRecord:
        if not self._required(space_id=space_id, actor_id=actor_id):
            self._require_personal_draft(space_id=space_id, skill_id=skill_id)
            return self._not_required()
        self._require_editor(space_id=space_id, skill_id=skill_id, actor_id=actor_id)
        record = self._repository.acquire(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )
        return self._present(record, actor_id=actor_id)

    def release(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        fencing_token: int,
    ) -> DraftEditLeaseViewRecord:
        if not self._required(space_id=space_id, actor_id=actor_id):
            self._require_personal_draft(space_id=space_id, skill_id=skill_id)
            return self._not_required()
        self._require_editor(space_id=space_id, skill_id=skill_id, actor_id=actor_id)
        record = self._repository.release(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            fencing_token=fencing_token,
            env=self._env_provider(),
        )
        return self._present(record, actor_id=actor_id)

    def takeover(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftEditLeaseViewRecord:
        if not self._required(space_id=space_id, actor_id=actor_id):
            self._require_personal_draft(space_id=space_id, skill_id=skill_id)
            return self._not_required()
        self._require_editor(space_id=space_id, skill_id=skill_id, actor_id=actor_id)
        record = self._repository.takeover(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )
        return self._present(record, actor_id=actor_id)

    def _required(self, *, space_id: int, actor_id: str) -> bool:
        try:
            space, _ = self._access.require_space_member(
                space_id=space_id, user_id=actor_id
            )
        except SpaceAccessDeniedError as exc:
            raise DraftEditLeaseForbiddenError() from exc
        return space.space_type == SpaceType.TEAM

    def _require_editor(self, *, space_id: int, skill_id: int, actor_id: str) -> None:
        try:
            self._grants.require_editor(
                space_id=space_id, skill_id=skill_id, actor_id=actor_id
            )
        except SpaceSkillGrantForbiddenError as exc:
            raise DraftEditLeaseForbiddenError() from exc

    def _require_personal_draft(self, *, space_id: int, skill_id: int) -> None:
        """Validate the addressed Draft without creating a Personal Lease."""
        self._repository.get_lease(
            space_id=space_id,
            skill_id=skill_id,
            env=self._env_provider(),
        )

    @staticmethod
    def _not_required() -> DraftEditLeaseViewRecord:
        return {
            "required": False,
            "state": "NOT_REQUIRED",
            "holder_user_id": None,
            "fencing_token": None,
        }

    @staticmethod
    def _present(
        record: DraftEditLeaseRecord | None, *, actor_id: str
    ) -> DraftEditLeaseViewRecord:
        if record is None or record["holder_user_id"] is None:
            return {
                "required": True,
                "state": "AVAILABLE",
                "holder_user_id": None,
                "fencing_token": None,
            }
        held_by_self = record["holder_user_id"] == actor_id
        return {
            "required": True,
            "state": "HELD_BY_SELF" if held_by_self else "HELD_BY_OTHER",
            "holder_user_id": record["holder_user_id"],
            "fencing_token": record["fencing_token"] if held_by_self else None,
        }
