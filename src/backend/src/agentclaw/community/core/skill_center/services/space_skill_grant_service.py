"""Space Skill Grant policy behind the public Service API."""

from __future__ import annotations

from collections.abc import Callable

from injector import inject

from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SpaceSkillGrantItem,
    SpaceSkillGrantSetRecord,
    SpaceSkillGrantViewRecord,
)
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantReasonRequiredError,
)
from agentclaw.community.core.spaces.models import SpaceRole, SpaceType
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol


def space_skill_actor_permissions(
    *, space_type, space_role, skill_role: str | None
) -> dict[str, bool]:
    """Project stable ACL/Grant qualifications independently of command state."""
    can_edit = skill_role in {"OWNER", "MANAGER"}
    is_owner = skill_role == "OWNER"
    is_admin = space_role in {
        SpaceRole.ADMIN,
        SpaceRole.OWNER,
        SpaceRole.ADMINISTRATOR,
    }
    can_request = space_type == SpaceType.TEAM and skill_role is None
    return {
        "edit_draft": can_edit,
        "publish_draft": can_edit,
        "delete_draft": can_edit,
        "create_upgrade_draft": can_edit,
        "retire_skill": can_edit,
        "manage_grants": is_owner,
        "transfer_owner": is_owner or is_admin,
        "request_edit_access": can_request,
        "takeover_lease": can_edit,
    }


class SpaceSkillGrantService:
    """Authorize Grant commands and delegate atomic facts to the repository."""

    @inject
    def __init__(
        self,
        access: SpaceAccessServiceProtocol,
        repository: SpaceSkillRepository,
        env_provider: Callable[[], str],
    ) -> None:
        self._access = access
        self._repository = repository
        self._env_provider = env_provider

    def list_grants(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> SpaceSkillGrantViewRecord:
        space, member = self._access.require_space_member(
            space_id=space_id, user_id=actor_id
        )
        record = self._repository.list_grants(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )
        return self._present(
            record,
            space_type=space.space_type,
            space_role=member.role,
        )

    def require_editor(self, *, space_id: int, skill_id: int, actor_id: str) -> str:
        """Authorize Draft editing through the canonical Grant seam."""
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        role = self._repository.get_active_role(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            env=self._env_provider(),
        )
        if role not in {"OWNER", "MANAGER"}:
            raise SpaceSkillGrantForbiddenError("owner or manager required")
        return role

    def add_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
    ) -> SpaceSkillGrantItem:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        env = self._env_provider()
        self._require_owner(space_id, skill_id, actor_id, env)
        return self._repository.add_manager(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            manager_user_id=manager_user_id,
            env=env,
        )

    def remove_manager(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        manager_user_id: str,
    ) -> SpaceSkillGrantItem:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        env = self._env_provider()
        self._require_owner(space_id, skill_id, actor_id, env)
        return self._repository.remove_manager(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            manager_user_id=manager_user_id,
            env=env,
        )

    def transfer_owner(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        new_owner_user_id: str,
        reason: str | None,
        retain_previous_owner_as_manager: bool = False,
    ) -> SpaceSkillGrantViewRecord:
        space, member = self._access.require_space_member(
            space_id=space_id, user_id=actor_id
        )
        env = self._env_provider()
        skill_role = self._repository.get_active_role(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id, env=env
        )
        is_owner = skill_role == "OWNER"
        is_admin = actor_id == space.created_by or member.role in {
            SpaceRole.ADMIN,
            SpaceRole.OWNER,
            SpaceRole.ADMINISTRATOR,
        }
        if not is_owner and not is_admin:
            raise SpaceSkillGrantForbiddenError("owner or space admin required")
        normalized_reason = reason.strip() if reason is not None else None
        if not is_owner and not normalized_reason:
            raise SpaceSkillGrantReasonRequiredError("space admin reason required")
        record = self._repository.transfer_owner(
            space_id=space_id,
            skill_id=skill_id,
            actor_id=actor_id,
            new_owner_user_id=new_owner_user_id,
            reason=normalized_reason,
            retain_previous_owner_as_manager=retain_previous_owner_as_manager,
            env=env,
        )
        return self._present(
            record,
            space_type=space.space_type,
            space_role=member.role,
        )

    def _require_owner(
        self, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> None:
        role = self._repository.get_active_role(
            space_id=space_id, skill_id=skill_id, actor_id=actor_id, env=env
        )
        if role != "OWNER":
            raise SpaceSkillGrantForbiddenError("skill owner required")

    @classmethod
    def _present(
        cls,
        record: SpaceSkillGrantSetRecord,
        *,
        space_type,
        space_role,
    ) -> SpaceSkillGrantViewRecord:
        return {
            "owner": record["owner"],
            "managers": record["managers"],
            "actor": {
                "skill_role": record["actor_role"],
                "permissions": space_skill_actor_permissions(
                    space_type=space_type,
                    space_role=space_role,
                    skill_role=record["actor_role"],
                ),
            },
        }
