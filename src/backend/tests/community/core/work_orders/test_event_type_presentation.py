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
    assert notification_summary_for("EXTERNAL_EVENT") == "你有一条新的通知，请查看详情。"


def test_known_event_overrides_historical_title():
    assert (
        notification_title_for(
            WorkOrderEventType.SKILL_COLLABORATOR_APPLIED.value, "旧标题"
        )
        == "Skill 共同编辑申请待审批"
    )
