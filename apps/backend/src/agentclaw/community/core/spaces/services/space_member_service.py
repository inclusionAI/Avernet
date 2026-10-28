"""Space member management service."""

from __future__ import annotations

from injector import inject

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
from agentclaw.community.core.work_orders.protocols import WorkOrderEventServiceProtocol
from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.spaces.errors import (
    PersonalSpaceInvariantError,
    SpaceCreatorInvariantError,
    SpaceMemberAlreadyExistsError,
    SpaceMemberInvalidError,
    SpaceMemberNotFoundError,
)
from agentclaw.community.core.spaces.models import (
    SpaceMemberRecord,
    SpaceRecord,  # noqa: F401 — annotation-only reference (SAST F821)
    SpaceMemberSummaryRecord,
    SpaceRole,
    SpaceType,
)
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderEventType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    notification_title_for,
)
from agentclaw.community.kernel.bot_manager_sync import SpaceChange
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup
from agentclaw.community.core.spaces.space_service_protocol import SpaceMemberServiceProtocol


logger = get_logger()


class SpaceMemberService(SpaceMemberServiceProtocol):
    @staticmethod
    def _user_id(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise SpaceMemberInvalidError("member user id is empty")
        return normalized

    @inject
    def __init__(
        self,
        repository: SpaceRepositoryProtocol,
        access: SpaceAccessService,
        staff_dept: StaffDeptPlugin,
        work_orders: WorkOrderEventServiceProtocol,
        manager_sync: BotManagerSyncProtocol,
    ) -> None:
        self._repository = repository
        self._access = access
        self._staff_dept = staff_dept
        self._work_orders = work_orders
        self._manager_sync = manager_sync

    def _submit_manager_sync_hint(
        self, *, space: SpaceRecord, env: str, user_ids: tuple[str, ...]
    ) -> None:
        """Best-effort BCS manager-set refresh hint after a committed write.

        Only small refs derived from the Space this operation already
        resolved are carried — never a Bot enumeration. The submit is
        strictly local: it never propagates, so a failed hint cannot change
        the committed business result; its return value is opaque.
        """
        try:
            self._manager_sync.request_space_sync(
                SpaceChange(
                    avernet_tenant=get_current_avernet_tenant(),
                    env=env,
                    space_refs=(
                        str(space.id),
                        f"team:{space.id}",
                        space.space_code,
                    ),
                    user_ids=user_ids,
                )
            )
        except Exception:
            logger.error(
                "manager sync notification rejected",
                extra={"space_id": space.id, "env": env},
            )

    def _resolve_member_user_name(self, *, user_id: str) -> str:
        try:
            profile = self._staff_dept.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(user_id)
            )
        except StaffProfileLookupError:
            logger.warning(
                "failed to resolve space member nickname; falling back to user id",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return user_id

        nickname = (profile.nick_name or "").strip()
        return nickname[:128] or user_id

    def list_members(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page_no: int,
        page_size: int,
    ) -> tuple[int, list[SpaceMemberSummaryRecord]]:
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return self._repository.list_members(
            space_id=space_id,
            env=get_current_env(),
            keyword=keyword.strip() if keyword and keyword.strip() else None,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )

    def add_member(
        self,
        *,
        space_id: int,
        actor_id: str,
        user_id: str,
        role: SpaceRole,
    ) -> SpaceMemberRecord:
        space, _ = self._access.require_space_owner(space_id=space_id, user_id=actor_id)
        if space.space_type is SpaceType.PERSONAL:
            raise PersonalSpaceInvariantError("personal space cannot add members")
        normalized = self._user_id(user_id)
        if (
            self._repository.get_member(
                space_id=space_id, user_id=normalized, env=get_current_env()
            )
            is not None
        ):
            raise SpaceMemberAlreadyExistsError("space member already exists")
        resolved_user_name = self._resolve_member_user_name(user_id=normalized)
        record = self._repository.add_member(
            space_id=space_id,
            user_id=normalized,
            user_name=resolved_user_name,
            role=role,
            creator_id=actor_id,
            env=get_current_env(),
        )
        # Membership grew — submit the in-memory hint before returning; a
        # failed submit never changes the committed result.
        self._submit_manager_sync_hint(
            space=space, env=get_current_env(), user_ids=(normalized,)
        )
        return record

    def delete_member(self, *, space_id: int, actor_id: str, user_id: str) -> bool:
        space, _ = self._access.require_space_owner(space_id=space_id, user_id=actor_id)
        normalized = self._user_id(user_id)
        if normalized == space.created_by:
            raise SpaceCreatorInvariantError("space creator cannot be removed")
        deleted = self._repository.delete_member(
            space_id=space_id, user_id=normalized, env=get_current_env()
        )
        if not deleted:
            raise SpaceMemberNotFoundError("space member not found")
        # Membership shrank — submit the in-memory hint BEFORE the existing
        # post-operation notification, so an exception there cannot lose the
        # BCS hint (and a failed hint still allows the notification below).
        self._submit_manager_sync_hint(
            space=space, env=get_current_env(), user_ids=(normalized,)
        )
        self._work_orders.create_work_order_event(
            event_category=NotificationCategory.NOTICE,
            biz_type="SPACE",
            biz_id=str(space_id),
            event_type=WorkOrderEventType.SPACE_MEMBER_REMOVED.value,
            applicant_user_id=None,
            approver_user_ids=[],
            recipient_user_ids=[normalized],
            title=notification_title_for(
                WorkOrderEventType.SPACE_MEMBER_REMOVED.value,
                WorkOrderMessageTitle.SPACE_MEMBER_REMOVED.value,
            ),
            content={
                "text": WorkOrderMessageContent.SPACE_MEMBER_REMOVED.format(
                    space_name=space.name
                )
            },
            apply_reason=None,
            biz_data=None,
            actor_id=actor_id,
        )
        return True

    def update_role(
        self, *, space_id: int, actor_id: str, user_id: str, role: SpaceRole
    ) -> SpaceMemberSummaryRecord:
        space, _ = self._access.require_space_owner(space_id=space_id, user_id=actor_id)
        if space.space_type is SpaceType.PERSONAL:
            raise PersonalSpaceInvariantError("personal space role is immutable")
        normalized = self._user_id(user_id)
        if normalized == space.created_by and role not in (
            SpaceRole.ADMIN,
            SpaceRole.OWNER,
            SpaceRole.ADMINISTRATOR,
        ):
            raise SpaceCreatorInvariantError("space creator cannot be demoted")
        updated = self._repository.update_member_role(
            space_id=space_id,
            user_id=normalized,
            role=role,
            env=get_current_env(),
        )
        if updated is None:
            raise SpaceMemberNotFoundError("space member not found")
        return SpaceMemberSummaryRecord(
            member=updated, is_creator=updated.user_id == space.created_by
        )
