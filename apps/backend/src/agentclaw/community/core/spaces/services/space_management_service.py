"""Script maintenance policy, separate from page/OpenAPI membership governance."""

from collections.abc import Callable

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.bot_collaborator.manager_sync.notifications import (
    notify_space_manager_sync,
)
from agentclaw.community.core.spaces.space_management_service_protocol import (
    SpaceManagementServiceProtocol,
)
from agentclaw.community.core.repository.protocols.spaces import (
    SpaceManagementRepositoryProtocol,
    SpaceRepositoryProtocol,
)
from agentclaw.community.core.spaces.management_models import (
    InitializeTeam,
    ManagedSpace,
    ManagementError,
    MemberBatch,
    MemberPageQuery,
    RenameTeam,
    SetMemberRoles,
    SpacePageQuery,
)
from agentclaw.community.kernel.bot_manager_sync import SpaceChange
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterTeamCreateRequest,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup

logger = get_logger()


class SpaceManagementService(SpaceManagementServiceProtocol):
    def __init__(
        self,
        repository: SpaceManagementRepositoryProtocol,
        spaces: SpaceRepositoryProtocol,
        skill_center: SkillCenterClient,
        staff: StaffDeptPlugin,
        env_provider: Callable[[], str],
        manager_sync: BotManagerSyncProtocol,
    ) -> None:
        self._repository, self._spaces = repository, spaces
        self._skill_center, self._staff, self._env = skill_center, staff, env_provider
        self._manager_sync = manager_sync

    def _submit_manager_sync_hint(
        self,
        *,
        space_id: int,
        env: str,
        space_refs: tuple[str, ...],
        user_ids: tuple[str, ...],
    ) -> None:
        """Best-effort BCS manager-set refresh hint after a committed write.

        Only small refs are carried — never a Bot enumeration. The submit is
        strictly local: it never propagates, so a failed hint cannot change
        the committed business result; its return value is opaque.
        """
        notify_space_manager_sync(
            self._manager_sync,
            change=SpaceChange(
                avernet_tenant=get_current_avernet_tenant(),
                env=env,
                space_refs=space_refs,
                user_ids=user_ids,
            ),
            space_id=space_id,
        )

    @staticmethod
    def _team_refs(space_id: int) -> tuple[str, ...]:
        """Refs the reader can use as-is, so they survive a deleted Space row."""
        return (str(space_id), f"team:{space_id}")

    def _name(self, user_id: str) -> str | None:
        try:
            profile = self._staff.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(user_id)
            )
        except StaffProfileLookupError:
            logger.warning("staff lookup unavailable during space maintenance")
            return None
        return (profile.nick_name or "").strip()[:128] or None

    def create_team(self, command: InitializeTeam) -> ManagedSpace:
        if command.space_type != "TEAM":
            raise ManagementError("SPACE_TYPE_INVALID")
        env = self._env()
        if (
            self._spaces.get_team_space_by_name(
                creator_id=command.admin_user_id,
                name=command.space_name,
                env=env,
            )
            is not None
        ):
            raise ManagementError("SPACE_NAME_ALREADY_EXISTS")
        members = {
            uid: self._name(uid)
            for uid in dict.fromkeys(command.member_user_ids)
            if uid != command.admin_user_id
        }
        with self._spaces.create_team_transaction(
            name=command.space_name,
            creator_id=command.admin_user_id,
            creator_user_name=self._name(command.admin_user_id),
            env=env,
            initial_members=members,
        ) as record:
            result = self._skill_center.create_team(
                SkillCenterTeamCreateRequest(
                    team_code=record.space_code,
                    team_name=record.name,
                    ref_source_id=str(record.id),
                )
            )
            record.sc_team_id = result.team_id
        return ManagedSpace(space=record, member_count=len(members) + 1, admin_count=1)

    def list_teams(self, query: SpacePageQuery) -> dict:
        return self._repository.list_teams(query=query, env=self._env())

    def get_team(self, space_id: int) -> ManagedSpace:
        return self._repository.get_team(space_id=space_id, env=self._env())

    def rename_team(self, space_id: int, command: RenameTeam, actor_id: str) -> dict:
        return self._repository.rename_team(
            space_id=space_id,
            name=command.space_name,
            actor_id=actor_id,
            env=self._env(),
        )

    def delete_empty_team(self, space_id: int) -> dict:
        return self._delete_team(space_id=space_id, with_members=False)

    def delete_team_with_members(self, space_id: int) -> dict:
        return self._delete_team(space_id=space_id, with_members=True)

    def _delete_team(self, *, space_id: int, with_members: bool) -> dict:
        env = self._env()
        # Capture the small refs BEFORE the delete: afterwards the Space row
        # is gone (its code resolves to nothing), while Bots keep pointing at
        # the numeric id the sync reader can still use as-is. A row that no
        # longer resolves must not block the delete — numeric refs remain.
        space = self._spaces.get_space(space_id=space_id, env=env)
        refs = self._team_refs(space_id)
        if space is not None:
            refs = refs + (space.space_code,)
        result = self._repository.delete_team(
            space_id=space_id, with_members=with_members, env=env
        )
        # Whole-Space delete: empty ``user_ids`` means every Bot under these
        # refs with an explicit collaborator, per the fan-out contract.
        self._submit_manager_sync_hint(
            space_id=space_id, env=env, space_refs=refs, user_ids=()
        )
        return result

    def list_members(self, space_id: int, query: MemberPageQuery) -> dict:
        return self._repository.list_members(
            space_id=space_id, query=query, env=self._env()
        )

    def add_members(self, space_id: int, command: MemberBatch, actor_id: str) -> dict:
        env = self._env()
        self._repository.get_team(space_id=space_id, env=env)
        members = {
            uid: self._name(uid) for uid in dict.fromkeys(command.member_user_ids)
        }
        result = self._repository.add_members(
            space_id=space_id, members=members, actor_id=actor_id, env=env
        )
        # Only the users whose membership actually changed: added members and
        # rejoined (reactivated) ones — members that existed untouched do not
        # widen anyone's rights. An all-existing batch stays silent.
        affected = (
            tuple(result["added_user_ids"])
            + tuple(result["reactivated_user_ids"])
        )
        if affected:
            self._submit_manager_sync_hint(
                space_id=space_id,
                env=env,
                space_refs=self._team_refs(space_id),
                user_ids=affected,
            )
        return result

    def remove_members(self, space_id: int, command: MemberBatch) -> dict:
        env = self._env()
        result = self._repository.remove_members(
            space_id=space_id,
            user_ids=list(dict.fromkeys(command.member_user_ids)),
            env=env,
        )
        if result["removed_user_ids"]:
            self._submit_manager_sync_hint(
                space_id=space_id,
                env=env,
                space_refs=self._team_refs(space_id),
                user_ids=tuple(result["removed_user_ids"]),
            )
        return result

    def set_member_roles(self, space_id: int, command: SetMemberRoles) -> dict:
        return self._repository.set_member_roles(
            space_id=space_id,
            user_ids=list(dict.fromkeys(command.member_user_ids)),
            role=command.role,
            env=self._env(),
        )
