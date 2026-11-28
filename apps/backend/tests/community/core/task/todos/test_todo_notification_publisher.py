from __future__ import annotations

from datetime import datetime, timezone

from agentclaw.community.core.task.todos.notification_models import (
    TodoNotificationEvent,
    TodoNotificationPublishStatus,
    TodoNotificationType,
    build_todo_notification_delivery_key,
)
from agentclaw.community.core.task.todos.services.todo_notification_publisher import (
    TodoNotificationPublisher,
)


class RecordingWorkOrders:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.failure = failure

    def merge_notice(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return 1


def _event(
    recipient_id: str | None = "owner_1",
    action_version: str = "stage_1:pending_action:1",
) -> TodoNotificationEvent:
    return TodoNotificationEvent(
        delivery_key=build_todo_notification_delivery_key(
            todo_id="todo_1",
            notification_type=TodoNotificationType.HUMAN_ACTION_REQUIRED,
            rollout_revision=7,
            stage_rollout_id=1001,
            action_version=action_version,
        ),
        notification_type=TodoNotificationType.HUMAN_ACTION_REQUIRED,
        todo_id="todo_1",
        space_id="7",
        recipient_id=recipient_id,
        title="待办需要人工处理",
        summary="请进入待办处理当前阶段",
        stage_code="stage_1",
        stage_rollout_id=1001,
        rollout_revision=7,
        action_version=action_version,
        occurred_at=datetime.now(timezone.utc),
    )


def test_publisher_uses_delivered_key_as_inbox_group() -> None:
    work_orders = RecordingWorkOrders()
    publisher = TodoNotificationPublisher(work_orders)  # type: ignore[arg-type]
    event = _event()

    result = publisher.publish(event)

    assert result.status is TodoNotificationPublishStatus.PUBLISHED
    assert len(work_orders.calls) == 1
    kwargs = work_orders.calls[0]
    assert kwargs["recipient_user_id"] == "owner_1"
    assert kwargs["event_type"] == "TODO_HUMAN_ACTION_REQUIRED"
    assert kwargs["biz_type"] == "TODO"
    assert kwargs["biz_id"] == "todo_1"
    assert kwargs["title"] == "待办需要人工处理"
    content_factory = kwargs["content_factory"]
    content = content_factory(None)
    assert content["text"] == "请进入待办处理当前阶段"
    assert content["target"] == {"todoId": "todo_1", "focus": "interaction"}


def test_missing_recipient_is_skipped_and_does_not_touch_inbox() -> None:
    work_orders = RecordingWorkOrders()
    publisher = TodoNotificationPublisher(work_orders)  # type: ignore[arg-type]

    result = publisher.publish(_event(recipient_id=None))

    assert result is not None
    assert result.status is TodoNotificationPublishStatus.SKIPPED
    assert result.reason == "recipient_missing"
    assert work_orders.calls == []


def test_inbox_failure_is_returned_without_raising() -> None:
    work_orders = RecordingWorkOrders(RuntimeError("inbox unavailable"))
    publisher = TodoNotificationPublisher(work_orders)  # type: ignore[arg-type]

    result = publisher.publish(_event())

    assert result.status is TodoNotificationPublishStatus.FAILED
    assert result.reason == "work_order_notification_unavailable"


def test_delivery_key_is_stable_for_the_same_action_fact() -> None:
    args = {
        "todo_id": "todo_1",
        "notification_type": TodoNotificationType.VERIFICATION_REQUIRED,
        "rollout_revision": 7,
        "stage_rollout_id": 1001,
        "action_version": "stage_1:pending_action:1",
    }
    assert build_todo_notification_delivery_key(**args) == (
        build_todo_notification_delivery_key(
            **{**args, "action_version": "stage_1:pending_action:1"}
        )
    )
    assert build_todo_notification_delivery_key(
        **{**args, "action_version": "stage_1:pending_action:2"}
    ) != build_todo_notification_delivery_key(**args)
