from agentclaw.community.core.work_orders.models import (
    EVENT_CATEGORIES,
    NotificationCategory,
    WorkOrderEventType,
    notification_summary_for,
    notification_title_for,
)


def test_every_known_event_has_complete_display_contract():
    assert set(EVENT_CATEGORIES) == set(WorkOrderEventType)
    for event_type in WorkOrderEventType:
        assert event_type.notification_category in (
            NotificationCategory.APPROVAL,
            NotificationCategory.NOTICE,
        )
        assert event_type.title
        assert event_type.summary
        assert notification_title_for(event_type.value) == event_type.title
        assert notification_summary_for(event_type.value) == event_type.summary


def test_unknown_event_keeps_supplied_title_and_uses_generic_summary():
    assert notification_title_for("EXTERNAL_EVENT", "外部标题") == "外部标题"
    assert notification_title_for("EXTERNAL_EVENT") == "新的系统通知"
    assert notification_summary_for("EXTERNAL_EVENT") == "你有一条新的通知"


def test_known_event_overrides_historical_title():
    assert (
        notification_title_for(
            WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value, "旧标题"
        )
        == "Skill 共同编辑申请待审批"
    )


def test_public_order_events_use_common_bot_display_copy():
    assert (
        WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_CREATED.title
        == "Bot 公开工单审批已创建"
    )
    assert (
        WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_CREATED.title
        == "Bot 公开工单审批已创建"
    )
    assert (
        WorkOrderEventType.HUMAN2BOT_PUBLIC_ORDER_COMPLETED.title
        == "Bot 公开工单审批已结束"
    )
    assert (
        WorkOrderEventType.BOT2BOT_PUBLIC_ORDER_COMPLETED.title
        == "Bot 公开工单审批已结束"
    )


def test_default_event_copy_does_not_include_detail_action():
    for event_type in WorkOrderEventType:
        assert "查看详情" not in event_type.summary
    assert "查看详情" not in notification_summary_for("EXTERNAL_EVENT")
