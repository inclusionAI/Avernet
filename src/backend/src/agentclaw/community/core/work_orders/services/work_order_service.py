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
    WorkOrderAlreadyProcessedError,
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
from agentclaw.community.core.work_orders.callbacks import (
    WorkOrderCallbackCredential,
    WorkOrderDecisionCallbackDispatcher,
    validate_friend_approval_event,
)
from agentclaw.community.core.work_orders.models import (
    EVENT_CATEGORIES,
    FRIEND_APPROVAL_EVENT_TYPES,
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDetail,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderMessageContent,
    WorkOrderMessageTitle,
    WorkOrderTitleKey,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderStatus,
    WorkOrderApproverStatus,
    WorkOrderDecision,
    WorkOrderEventCreatedResult,
)
from agentclaw.community.core.work_orders.protocols import (
    SkillCollaboratorApprovalHandlerProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    StaffProfileLookupError,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.work_no import normalize_work_no_for_lookup
from agentclaw.community.core.work_orders.work_order_service_protocol import WorkOrderNotificationServiceProtocol
from agentclaw.community.core.work_orders.work_order_service_protocol import WorkOrderServiceProtocol


logger = get_logger()


class WorkOrderService(WorkOrderServiceProtocol):
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
        staff_dept: StaffDeptPlugin,
        skill_collaborator_approval_handler: SkillCollaboratorApprovalHandlerProtocol,
        decision_callbacks: WorkOrderDecisionCallbackDispatcher,
    ) -> None:
        self._repository = repository
        self._spaces = spaces
        self._access = access
        self._notifications = notifications
        self._bots = bot_repository
        self._collaborator_repository = collaborator_repository
        self._collaborators = collaborators
        self._member_management = member_management
        self._staff_dept = staff_dept
        self._skill_collaborator_approval_handler = skill_collaborator_approval_handler
        self._decision_callbacks = decision_callbacks

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
        content: dict[str, object] | None,
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
        title = self._required_text(title, limit=256, error=WorkOrderInvalidEventError)
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
        if (
            biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value
            or event_type == WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value
        ):
            raise WorkOrderInvalidEventError(
                "Skill editor requests must use the Skill endpoint"
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
            raise WorkOrderInvalidEventError(
                "apply_reason must contain no more than 512 characters"
            )
        validate_friend_approval_event(
            biz_type=biz_type,
            event_type=event_type,
            biz_data=biz_data,
        )
        serialized_content = (
            json.dumps(content, ensure_ascii=False) if content is not None else None
        )
        serialized_data = (
            json.dumps(biz_data, ensure_ascii=False) if biz_data is not None else None
        )
        if event_type == WorkOrderEventType.SPACE_JOIN_APPLIED.value:
            title = WorkOrderTitleKey.SPACE_JOIN_PENDING.value
        result = self._repository.create_work_order_event(
            event_category=event_category,
            biz_type=biz_type,
            biz_id=biz_id,
            event_type=event_type,
            applicant_user_id=applicant or None,
            approver_user_ids=approvers,
            recipient_user_ids=recipients,
            title=title,
            content=serialized_content,
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
        callback_credential: WorkOrderCallbackCredential,
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
        if detail.work_order.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value:
            return self._skill_collaborator_approval_handler.process(
                detail=detail,
                actor_id=actor_id,
                review_remark=normalized,
                target_status=WorkOrderStatus(decision.value),
            )
        context = self._repository.get_approval_context(
            work_order_id=work_order_id,
            reviewer_user_id=actor_id,
            env=get_current_env(),
        )
        source_event_type = context.source_event_type
        if context.work_order.biz_type == WorkOrderBizType.BOT_FRIEND.value:
            try:
                source_event = WorkOrderEventType(source_event_type)
            except (TypeError, ValueError) as exc:
                raise WorkOrderInvalidEventError(
                    "friend work order is missing its source approval event"
                ) from exc
            if source_event not in FRIEND_APPROVAL_EVENT_TYPES:
                raise WorkOrderInvalidEventError(
                    "BOT_FRIEND work order has an unsupported source event"
                )
        if self._decision_callbacks.requires_callback(source_event_type):
            if (
                context.work_order.status is not WorkOrderStatus.PENDING
                or context.approver.status is not WorkOrderApproverStatus.PENDING
            ):
                raise WorkOrderAlreadyProcessedError("work order already processed")
            self._decision_callbacks.dispatch(
                context=context,
                decision=decision,
                review_remark=normalized,
                credential=callback_credential,
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
        applicant_name = self._get_applicant_name(
            applicant_user_id=applicant_user_id
        )
        return self._repository.create_space_join_request(
            space_id=space_id,
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            apply_reason=reason,
            env=get_current_env(),
        )

    def _get_applicant_name(self, *, applicant_user_id: str) -> str:
        try:
            profile = self._staff_dept.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(applicant_user_id)
            )
        except StaffProfileLookupError:
            logger.warning(
                "failed to resolve applicant nickname; falling back to user id",
                extra={"user_id": applicant_user_id},
                exc_info=True,
            )
            return applicant_user_id

        nickname = (profile.nick_name or "").strip()
        return nickname[:128] or applicant_user_id

    def _get_reviewer_name(self, *, reviewer_user_id: str | None) -> str | None:
        if not reviewer_user_id:
            return None
        try:
            profile = self._staff_dept.get_profile_by_work_no(
                work_no=normalize_work_no_for_lookup(reviewer_user_id)
            )
        except StaffProfileLookupError:
            logger.warning(
                "failed to resolve reviewer nickname",
                extra={"user_id": reviewer_user_id},
                exc_info=True,
            )
            return None

        nickname = (profile.nick_name or "").strip()
        return nickname[:128] or None

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
        return detail.model_copy(
            update={
                "reviewer_user_name": self._get_reviewer_name(
                    reviewer_user_id=detail.work_order.reviewer_user_id
                )
            }
        )

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
        applicant_user_name = (
            self._get_applicant_name(
                applicant_user_id=detail.work_order.applicant_user_id
            )
            if target_status is WorkOrderStatus.APPROVED
            else None
        )
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
            applicant_user_name=applicant_user_name,
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


class WorkOrderNotificationService(WorkOrderNotificationServiceProtocol):
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
            title = WorkOrderTitleKey.SPACE_JOIN_APPROVED.value
            content = WorkOrderMessageContent.SPACE_JOIN_APPROVED.value.format(
                space_name=detail.space_name
            )
        elif target_status is WorkOrderStatus.REJECTED:
            if review_remark is None:
                raise WorkOrderInvalidRemarkError("review remark is required")
            title = WorkOrderTitleKey.SPACE_JOIN_REJECTED.value
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
