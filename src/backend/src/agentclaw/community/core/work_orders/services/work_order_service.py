"""Transport-agnostic work-order orchestration and authorization."""

from __future__ import annotations

import json

from injector import inject

from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.spaces import SpaceRepositoryProtocol
from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.spaces.services.space_access_service import (
    SpaceAccessService,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderBotEditorRequestNotAllowedError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidRemarkError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNotificationNotFoundError,
    WorkOrderNotFoundError,
    WorkOrderNoReviewerError,
    WorkOrderInvalidEventError,
)
from agentclaw.community.core.work_orders.models import (
    EVENT_CATEGORIES,
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderStatus,
    WorkOrderDecision,
    NotificationCategory,
    WorkOrderEventCreatedResult,
)
from agentclaw.community.utils.env_utils import get_current_env


class WorkOrderService:
    @inject
    def __init__(
        self,
        repository: WorkOrderRepositoryProtocol,
        spaces: SpaceRepositoryProtocol,
        access: SpaceAccessService,
        notifications: WorkOrderNotificationService,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
        collaborators: CollaboratorServiceProtocol,
        member_management: MemberManagementCapabilityService,
    ) -> None:
        self._repository = repository
        self._spaces = spaces
        self._access = access
        self._notifications = notifications
        self._bots = bot_repository
        self._collaborator_repository = collaborator_repository
        self._collaborators = collaborators
        self._member_management = member_management

    @staticmethod
    def _required_text(value: str, *, limit: int, error: type[Exception]) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > limit:
            raise error(f"value must contain 1-{limit} characters")
        return normalized

    def create_work_order_event(
        self,
        *,
        event_category: NotificationCategory,
        biz_type: str,
        biz_id: str,
        event_type: str,
        applicant_user_id: str | None,
        approver_user_ids: list[str],
        recipient_user_ids: list[str],
        title: str,
        content: str | None,
        apply_reason: str | None,
        biz_data: dict[str, object] | None,
        actor_id: str,
    ) -> WorkOrderEventCreatedResult:
        biz_type = self._required_text(
            biz_type, limit=64, error=WorkOrderInvalidEventError
        )
        biz_id = self._required_text(
            biz_id, limit=128, error=WorkOrderInvalidEventError
        )
        event_type = self._required_text(
            event_type, limit=64, error=WorkOrderInvalidEventError
        )
        title = self._required_text(
            title, limit=256, error=WorkOrderInvalidEventError
        )
        actor_id = self._required_text(
            actor_id, limit=256, error=WorkOrderAccessDeniedError
        )
        applicant = (applicant_user_id or actor_id).strip()
        if not applicant or applicant != actor_id:
            raise WorkOrderAccessDeniedError("applicant must be the current user")
        approvers = list(
            dict.fromkeys(user.strip() for user in approver_user_ids if user.strip())
        )
        recipients = list(
            dict.fromkeys(user.strip() for user in recipient_user_ids if user.strip())
        )
        try:
            registered_category = EVENT_CATEGORIES[WorkOrderEventType(event_type)]
        except (KeyError, ValueError) as exc:
            raise WorkOrderInvalidEventError("event_type is not registered") from exc
        if registered_category is not event_category:
            raise WorkOrderInvalidEventError(
                "event_type category does not match event_category"
            )
        if event_category is NotificationCategory.APPROVAL:
            if not approvers or recipients:
                raise WorkOrderInvalidEventError(
                    "approval events require approvers and no recipients"
                )
        elif event_category is NotificationCategory.NOTICE:
            if not recipients or approvers or applicant_user_id is not None:
                raise WorkOrderInvalidEventError(
                    "notice events require recipients and no applicant or approvers"
                )
            applicant = ""
        else:
            raise WorkOrderInvalidEventError("unsupported event category")
        reason = (apply_reason or "").strip() or None
        if reason is not None and len(reason) > 512:
            raise WorkOrderInvalidEventError("apply_reason must contain no more than 512 characters")
        serialized_data = (
            json.dumps(biz_data, ensure_ascii=False) if biz_data is not None else None
        )
        result = self._repository.create_work_order_event(
            event_category=event_category, biz_type=biz_type, biz_id=biz_id,
            event_type=event_type, applicant_user_id=applicant or None,
            approver_user_ids=approvers,
            recipient_user_ids=recipients,
            title=title,
            content=content,
            apply_reason=reason,
            biz_data=serialized_data,
            env=get_current_env(),
        )
        return result

    def create_work_order(
        self,
        *,
        biz_type: str,
        biz_id: str,
        applicant_user_id: str,
        apply_reason: str | None,
        biz_data: str | None,
        approver_user_ids: list[str],
        notification_recipient_user_ids: list[str] | None = None,
    ):
        biz_type = self._required_text(
            biz_type, limit=64, error=WorkOrderInvalidReasonError
        )
        biz_id = self._required_text(
            biz_id, limit=128, error=WorkOrderInvalidReasonError
        )
        applicant_user_id = self._required_text(
            applicant_user_id, limit=256, error=WorkOrderInvalidReasonError
        )
        approvers = list(
            dict.fromkeys(user.strip() for user in approver_user_ids if user.strip())
        )
        recipients = list(
            dict.fromkeys(
                user.strip()
                for user in (notification_recipient_user_ids or [])
                if user.strip()
            )
        )
        if not approvers and not recipients:
            raise WorkOrderNoReviewerError(
                "at least one approver or notification recipient is required"
            )
        return self._repository.create_work_order(
            biz_type=biz_type,
            biz_id=biz_id,
            applicant_user_id=applicant_user_id,
            apply_reason=apply_reason,
            biz_data=biz_data,
            approver_user_ids=approvers,
            notification_recipient_user_ids=recipients,
            env=get_current_env(),
        )

    def process_approval(
        self,
        *,
        work_order_id: int,
        actor_id: str,
        decision: WorkOrderDecision,
        review_remark: str | None,
    ):
        normalized = (review_remark or "").strip() or None
        if normalized is not None and len(normalized) > 512:
            raise WorkOrderInvalidRemarkError(
                "value must contain no more than 512 characters"
            )
        if decision is WorkOrderDecision.REJECTED and normalized is None:
            raise WorkOrderInvalidRemarkError("review remark is required")
        detail = self.get_detail(work_order_id=work_order_id, actor_id=actor_id)
        if detail.work_order.biz_type == WorkOrderBizType.SPACE_JOIN.value:
            return self._review_space_join(
                detail=detail,
                actor_id=actor_id,
                review_remark=normalized,
                target_status=WorkOrderStatus(decision.value),
            )
        if detail.work_order.biz_type == WorkOrderBizType.BOT_COLLABORATOR.value:
            return self._review_bot_editor_request(
                detail=detail,
                actor_id=actor_id,
                review_remark=normalized,
                target_status=WorkOrderStatus(decision.value),
            )
        return self._repository.process_approval(
            work_order_id=work_order_id,
            reviewer_user_id=actor_id,
            decision=decision,
            review_remark=normalized,
            env=get_current_env(),
        )

    def create_bot_editor_request(
        self,
        *,
        bot_id: str,
        owner_id: str,
        applicant_user_id: str,
        reason: str,
    ):
        reason = self._required_text(
            reason, limit=512, error=WorkOrderInvalidReasonError
        )
        bot = self._bots.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise WorkOrderNotFoundError("bot not found")
        if applicant_user_id == str(bot.get("owner_id") or ""):
            raise WorkOrderApplicantAlreadyEditorError("Bot owner already has access")
        if not self._member_management.can_manage_collaborators(bot, bot_id):
            raise WorkOrderBotEditorRequestNotAllowedError(
                "Bot does not support editor management"
            )
        raw_space_id = bot.get("space_id")
        try:
            space = self._access.require_space_reference(space_ref=str(raw_space_id))
        except (SpaceNotFoundError, ValueError) as exc:
            raise WorkOrderBotEditorRequestNotAllowedError(
                "Bot is not assigned to an available Team Space"
            ) from exc
        if space.space_type is not SpaceType.TEAM:
            raise WorkOrderBotEditorRequestNotAllowedError(
                "Only Team Space Bots accept editor requests"
            )
        try:
            self._access.require_space_member(
                space_id=space.id, user_id=applicant_user_id
            )
        except SpaceAccessDeniedError as exc:
            raise WorkOrderAccessDeniedError(
                "applicant must be a Team Space member"
            ) from exc
        if (
            self._collaborator_repository.get_by_bot_and_user(
                int(bot["id"]), applicant_user_id, get_current_env()
            )
            is not None
        ):
            raise WorkOrderApplicantAlreadyEditorError(
                "applicant already has Bot editor access"
            )
        return self._repository.create_bot_editor_request(
            bot_pk=int(bot["id"]),
            bot_id=bot_id,
            bot_name=str(bot.get("bot_name") or bot_id),
            owner_id=str(bot["owner_id"]),
            space_id=space.id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_user_id,
            apply_reason=reason,
            env=get_current_env(),
        )

    def create_space_join_request(
        self, *, space_id: int, applicant_user_id: str, reason: str | None
    ):
        reason = (reason or "").strip() or None
        if reason is not None and len(reason) > 512:
            raise WorkOrderInvalidReasonError(
                "value must contain no more than 512 characters"
            )
        space = self._access.require_space(space_id=space_id)
        if space.space_type is not SpaceType.TEAM:
            raise WorkOrderJoinNotAllowedError(
                "personal spaces do not accept join requests"
            )
        if (
            self._spaces.get_member(
                space_id=space_id,
                user_id=applicant_user_id,
                env=get_current_env(),
            )
            is not None
        ):
            raise WorkOrderApplicantAlreadyMemberError(
                "applicant is already a space member"
            )
        return self._repository.create_space_join_request(
            space_id=space_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_user_id,
            apply_reason=reason,
            env=get_current_env(),
        )

    def list_items(
        self,
        *,
        actor_id: str,
        query_type: WorkOrderQueryType,
        item_type: WorkOrderItemType,
        biz_type: str | None = None,
        biz_id: str | None = None,
        page_no: int,
        page_size: int,
    ):
        return self._repository.list_items(
            actor_id=actor_id,
            env=get_current_env(),
            query_type=query_type,
            item_type=item_type,
            biz_type=biz_type,
            biz_id=biz_id,
            offset=(page_no - 1) * page_size,
            limit=page_size,
        )

    def get_detail(self, *, work_order_id: int, actor_id: str):
        detail = self._repository.get_detail(
            work_order_id=work_order_id,
            actor_id=actor_id,
            env=get_current_env(),
        )
        if detail is None:
            raise WorkOrderNotFoundError("work order not found")
        return detail

    def approve(self, *, work_order_id: int, actor_id: str, review_remark: str | None):
        normalized_remark = (review_remark or "").strip() or None
        if normalized_remark is not None and len(normalized_remark) > 512:
            raise WorkOrderInvalidRemarkError(
                "value must contain no more than 512 characters"
            )
        return self._review_space_join(
            detail=self.get_detail(work_order_id=work_order_id, actor_id=actor_id),
            actor_id=actor_id,
            review_remark=normalized_remark,
            target_status=WorkOrderStatus.APPROVED,
        )

    def reject(self, *, work_order_id: int, actor_id: str, review_remark: str | None):
        normalized_remark = self._required_text(
            review_remark or "", limit=512, error=WorkOrderInvalidRemarkError
        )
        return self._review_space_join(
            detail=self.get_detail(work_order_id=work_order_id, actor_id=actor_id),
            actor_id=actor_id,
            review_remark=normalized_remark,
            target_status=WorkOrderStatus.REJECTED,
        )

    def _review_space_join(
        self,
        *,
        detail: WorkOrderDetail,
        actor_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
    ):
        if detail.work_order.biz_type != WorkOrderBizType.SPACE_JOIN.value:
            raise WorkOrderNotFoundError("space join work order not found")
        try:
            self._access.require_space_owner(space_id=detail.space_id, user_id=actor_id)
        except SpaceAccessDeniedError as exc:
            raise WorkOrderAccessDeniedError("space owner role required") from exc
        notification = self._notifications.build_space_join_review_result(
            detail=detail,
            target_status=target_status,
            review_remark=review_remark,
        )
        return self._repository.review_space_join(
            work_order_id=detail.work_order.id,
            reviewer_user_id=actor_id,
            review_remark=review_remark,
            target_status=target_status,
            notification=notification,
            env=get_current_env(),
        )

    def _review_bot_editor_request(
        self,
        *,
        detail: WorkOrderDetail,
        actor_id: str,
        review_remark: str | None,
        target_status: WorkOrderStatus,
    ):
        data = _business_data(detail.work_order.biz_data)
        bot_id = str(data.get("bot_id") or "")
        owner_id = str(data.get("owner_id") or "")
        bot_name = str(data.get("bot_name") or bot_id)
        if not bot_id or not owner_id or actor_id != owner_id:
            raise WorkOrderAccessDeniedError("Bot owner role required")
        notification = self._notifications.build_bot_editor_review_result(
            detail=detail,
            bot_name=bot_name,
            target_status=target_status,
            review_remark=review_remark,
        )
        result = self._repository.review_bot_editor_request(
            work_order_id=detail.work_order.id,
            reviewer_user_id=actor_id,
            review_remark=review_remark,
            target_status=target_status,
            notification=notification,
            env=get_current_env(),
        )
        if target_status is WorkOrderStatus.APPROVED:
            self._collaborators.on_collaboration_changed(
                bot_id, owner_id, get_current_env()
            )
        return result


class WorkOrderNotificationService:
    @inject
    def __init__(self, repository: WorkOrderRepositoryProtocol) -> None:
        self._repository = repository

    @staticmethod
    def build_space_join_review_result(
        *,
        detail: WorkOrderDetail,
        target_status: WorkOrderStatus,
        review_remark: str | None,
    ) -> WorkOrderNotificationDraft:
        if target_status is WorkOrderStatus.APPROVED:
            title = WorkOrderMessageTitle.SPACE_JOIN_APPROVED.value
            content = WorkOrderMessageContent.SPACE_JOIN_APPROVED.value.format(
                space_name=detail.space_name
            )
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderMessageTitle.SPACE_JOIN_REJECTED.value
            content = WorkOrderMessageContent.SPACE_JOIN_REJECTED.value.format(
                space_name=detail.space_name,
                review_remark=review_remark,
            )
        else:
            raise ValueError(f"unsupported review status: {target_status}")

        event_type = WorkOrderEventType.SPACE_JOIN_REVIEWED
        return WorkOrderNotificationDraft(
            recipient_user_id=detail.work_order.applicant_user_id,
            notification_category=EVENT_CATEGORIES[event_type],
            event_type=event_type,
            biz_type=detail.work_order.biz_type,
            biz_id=detail.work_order.biz_id,
            title=title,
            content=content,
        )

    @staticmethod
    def build_bot_editor_review_result(
        *,
        detail: WorkOrderDetail,
        bot_name: str,
        target_status: WorkOrderStatus,
        review_remark: str | None,
    ) -> WorkOrderNotificationDraft:
        if target_status is WorkOrderStatus.APPROVED:
            title = WorkOrderMessageTitle.BOT_COLLABORATOR_APPROVED.value
            content = WorkOrderMessageContent.BOT_COLLABORATOR_APPROVED.value.format(
                bot_name=bot_name
            )
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderMessageTitle.BOT_COLLABORATOR_REJECTED.value
            content = WorkOrderMessageContent.BOT_COLLABORATOR_REJECTED.value.format(
                bot_name=bot_name, review_remark=review_remark
            )
        else:
            raise ValueError(f"unsupported review status: {target_status}")
        return WorkOrderNotificationDraft(
            recipient_user_id=detail.work_order.applicant_user_id,
            notification_category=NotificationCategory.NOTICE,
            event_type=WorkOrderEventType.BOT_COLLABORATOR_REVIEWED,
            biz_type=WorkOrderBizType.BOT_COLLABORATOR,
            biz_id=detail.work_order.biz_id,
            title=title,
            content=content,
        )

    def get_detail(self, *, notification_id: int, actor_id: str):
        result = self._repository.get_notification(
            notification_id=notification_id,
            recipient_user_id=actor_id,
            env=get_current_env(),
            mark_read=True,
        )
        if result is None:
            raise WorkOrderNotificationNotFoundError("notification not found")
        return result

    def unread_count(self, *, actor_id: str) -> int:
        return self._repository.count_unread(
            recipient_user_id=actor_id, env=get_current_env()
        )

    def badge_summary(self, *, actor_id: str):
        return self._repository.get_notification_badge_summary(
            recipient_user_id=actor_id, env=get_current_env()
        )

    def mark_read(self, *, notification_id: int, actor_id: str):
        record = self._repository.mark_notification_read(
            notification_id=notification_id,
            recipient_user_id=actor_id,
            env=get_current_env(),
        )
        if record is None:
            raise WorkOrderNotificationNotFoundError("notification not found")
        return record

    def mark_all_read(self, *, actor_id: str) -> int:
        return self._repository.mark_all_notifications_read(
            recipient_user_id=actor_id, env=get_current_env()
        )


def _business_data(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
