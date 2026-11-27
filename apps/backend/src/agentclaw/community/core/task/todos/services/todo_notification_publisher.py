"""Adapter from Todo notification facts to the existing work-order inbox."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from injector import inject

from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.task.todos.notification_models import (
    TodoNotificationEvent,
    TodoNotificationPublishResult,
    TodoNotificationPublishStatus,
    TodoNotificationType,
)
from agentclaw.community.core.task.todos.notification_publisher_protocol import (
    TodoNotificationPublisherProtocol,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderEventType,
)
from agentclaw.community.utils.env_utils import get_current_env


_EVENT_TYPES: dict[TodoNotificationType, WorkOrderEventType] = {
    TodoNotificationType.HUMAN_ACTION_REQUIRED: (
        WorkOrderEventType.TODO_HUMAN_ACTION_REQUIRED
    ),
    TodoNotificationType.BOT_INFORMATION_REQUIRED: (
        WorkOrderEventType.TODO_BOT_INFORMATION_REQUIRED
    ),
    TodoNotificationType.EXECUTION_INTERRUPTED: (
        WorkOrderEventType.TODO_EXECUTION_INTERRUPTED
    ),
    TodoNotificationType.VERIFICATION_REQUIRED: (
        WorkOrderEventType.TODO_VERIFICATION_REQUIRED
    ),
    TodoNotificationType.TODO_COMPLETED: WorkOrderEventType.TODO_COMPLETED,
}


class TodoNotificationPublisher(TodoNotificationPublisherProtocol):
    """Use the shared inbox without importing the Forum publisher contract."""

    @inject
    def __init__(self, work_orders: WorkOrderRepositoryProtocol) -> None:
        self._work_orders = work_orders

    def publish(
        self, event: TodoNotificationEvent
    ) -> TodoNotificationPublishResult:
        recipient = event.recipient_id.strip() if event.recipient_id else ""
        if not recipient:
            return TodoNotificationPublishResult(
                TodoNotificationPublishStatus.SKIPPED, "recipient_missing"
            )
        try:
            self._work_orders.merge_notice(
                recipient_user_id=recipient,
                event_type=_EVENT_TYPES[event.notification_type].value,
                group_id=hashlib.sha256(
                    event.delivery_key.encode("utf-8")
                ).hexdigest(),
                biz_type=WorkOrderBizType.TODO.value,
                biz_id=event.todo_id[:128],
                title=event.title,
                content_factory=_content_factory(event),
                env=get_current_env(),
            )
        except Exception:
            return TodoNotificationPublishResult(
                TodoNotificationPublishStatus.FAILED,
                "work_order_notification_unavailable",
            )
        return TodoNotificationPublishResult(
            TodoNotificationPublishStatus.PUBLISHED, None
        )


def _content_factory(
    event: TodoNotificationEvent,
) -> Callable[[dict[str, object] | None], dict[str, object]]:
    def build(_current: dict[str, object] | None) -> dict[str, object]:
        return {
            "text": event.summary,
            "todo_id": event.todo_id,
            "space_id": event.space_id,
            "notification_type": event.notification_type.value,
            "stage_code": event.stage_code,
            "stage_rollout_id": event.stage_rollout_id,
            "rollout_revision": event.rollout_revision,
            "action_version": event.action_version,
            "target": {
                "todoId": event.todo_id,
                "focus": "interaction",
            },
        }

    return build
