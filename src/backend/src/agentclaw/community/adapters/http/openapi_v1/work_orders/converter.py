"""Presentation compatibility for persisted work-order content and titles."""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderEventType,
    WorkOrderMessageTitle,
    WorkOrderStatus,
    WorkOrderTitleKey,
    notification_summary_for,
    notification_title_for,
)

JsonObject = dict[str, Any]
ContentValue = str | JsonObject | None

_TITLE_KEY_BY_STORED_VALUE = {
    WorkOrderTitleKey.SPACE_JOIN_PENDING.value: WorkOrderTitleKey.SPACE_JOIN_PENDING,
    WorkOrderTitleKey.SPACE_JOIN_APPROVED.value: WorkOrderTitleKey.SPACE_JOIN_APPROVED,
    WorkOrderTitleKey.SPACE_JOIN_REJECTED.value: WorkOrderTitleKey.SPACE_JOIN_REJECTED,
    WorkOrderMessageTitle.SPACE_JOIN_PENDING.value: WorkOrderTitleKey.SPACE_JOIN_PENDING,
    WorkOrderMessageTitle.SPACE_JOIN_APPROVED.value: WorkOrderTitleKey.SPACE_JOIN_APPROVED,
    WorkOrderMessageTitle.SPACE_JOIN_REJECTED.value: WorkOrderTitleKey.SPACE_JOIN_REJECTED,
    "SPACE_JOIN APPROVED": WorkOrderTitleKey.SPACE_JOIN_APPROVED,
    "SPACE_JOIN REJECTED": WorkOrderTitleKey.SPACE_JOIN_REJECTED,
}
_TITLE_BY_KEY = {
    WorkOrderTitleKey.SPACE_JOIN_PENDING: WorkOrderMessageTitle.SPACE_JOIN_PENDING.value,
    WorkOrderTitleKey.SPACE_JOIN_APPROVED: WorkOrderMessageTitle.SPACE_JOIN_APPROVED.value,
    WorkOrderTitleKey.SPACE_JOIN_REJECTED: WorkOrderMessageTitle.SPACE_JOIN_REJECTED.value,
}
_TITLE_BY_BIZ_STATUS = {
    (WorkOrderBizType.SPACE_JOIN.value, WorkOrderStatus.PENDING): WorkOrderMessageTitle.SPACE_JOIN_PENDING.value,
    (WorkOrderBizType.SPACE_JOIN.value, WorkOrderStatus.APPROVED): WorkOrderMessageTitle.SPACE_JOIN_APPROVED.value,
    (WorkOrderBizType.SPACE_JOIN.value, WorkOrderStatus.REJECTED): WorkOrderMessageTitle.SPACE_JOIN_REJECTED.value,
    (WorkOrderBizType.BOT_COLLABORATOR.value, WorkOrderStatus.PENDING): WorkOrderMessageTitle.BOT_COLLABORATOR_PENDING.value,
    (WorkOrderBizType.BOT_COLLABORATOR.value, WorkOrderStatus.APPROVED): WorkOrderMessageTitle.BOT_COLLABORATOR_APPROVED.value,
    (WorkOrderBizType.BOT_COLLABORATOR.value, WorkOrderStatus.REJECTED): WorkOrderMessageTitle.BOT_COLLABORATOR_REJECTED.value,
    (WorkOrderBizType.SKILL_COLLABORATOR.value, WorkOrderStatus.PENDING): WorkOrderMessageTitle.SKILL_COLLABORATOR_PENDING.value,
    (WorkOrderBizType.SKILL_COLLABORATOR.value, WorkOrderStatus.APPROVED): WorkOrderMessageTitle.SKILL_COLLABORATOR_APPROVED.value,
    (WorkOrderBizType.SKILL_COLLABORATOR.value, WorkOrderStatus.REJECTED): WorkOrderMessageTitle.SKILL_COLLABORATOR_REJECTED.value,
    (WorkOrderBizType.BOT_FRIEND.value, WorkOrderStatus.PENDING): "好友申请待审批",
    (WorkOrderBizType.BOT_FRIEND.value, WorkOrderStatus.APPROVED): "好友申请已通过",
    (WorkOrderBizType.BOT_FRIEND.value, WorkOrderStatus.REJECTED): "好友申请未通过",
}
_SUMMARY_BY_BIZ_TYPE = {
    WorkOrderBizType.SPACE_JOIN.value: "有新的空间加入申请，请及时处理。",
    WorkOrderBizType.BOT_COLLABORATOR.value: "有新的 Bot 共同编辑申请，请及时处理。",
    WorkOrderBizType.SKILL_COLLABORATOR.value: "有新的 Skill 共同编辑申请，请及时处理。",
    WorkOrderBizType.BOT_FRIEND.value: "有新的好友申请，请及时处理。",
}
_GENERIC_SUMMARY = "你有一条新的通知，请查看详情。"
_REVIEWED_TITLE_BY_EVENT_STATUS = {
    (
        WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED.value,
        WorkOrderStatus.APPROVED,
    ): WorkOrderMessageTitle.HUMAN_FRIEND_APPROVED.value,
    (
        WorkOrderEventType.HUMAN2BOT_FRIEND_REVIEWED.value,
        WorkOrderStatus.REJECTED,
    ): WorkOrderMessageTitle.HUMAN_FRIEND_REJECTED.value,
    (
        WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED.value,
        WorkOrderStatus.APPROVED,
    ): WorkOrderMessageTitle.BOT_FRIEND_APPROVED.value,
    (
        WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED.value,
        WorkOrderStatus.REJECTED,
    ): WorkOrderMessageTitle.BOT_FRIEND_REJECTED.value,
}


def display_title(
    stored_title: str | None,
    *,
    event_type: str | None = None,
    biz_type: str | None = None,
    status: WorkOrderStatus | None = None,
) -> str | None:
    """Resolve canonical titles while retaining legacy persisted values."""

    if event_type is not None:
        reviewed_title = _REVIEWED_TITLE_BY_EVENT_STATUS.get((event_type, status))
        if reviewed_title is not None:
            return reviewed_title
        return notification_title_for(event_type, stored_title)

    key = _TITLE_KEY_BY_STORED_VALUE.get(stored_title or "")
    if key is not None:
        return _TITLE_BY_KEY[key]
    derived = _TITLE_BY_BIZ_STATUS.get((biz_type or "", status))
    if derived is not None:
        return derived
    if stored_title and stored_title != "新的系统通知":
        return stored_title
    return None


def _parse_content(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def preserve_content(raw: Any) -> ContentValue:
    """Expose content without reshaping historical strings or JSON objects."""

    parsed = _parse_content(raw)
    if parsed is None or isinstance(parsed, (str, dict)):
        return parsed
    # Preserve unsupported historical JSON values as their original database
    # text rather than changing their shape or representation.
    return raw if isinstance(raw, str) else str(parsed)


def extract_content_text(raw: Any) -> str | None:
    """Extract user-facing text from legacy text and structured content."""

    parsed = _parse_content(raw)
    if isinstance(parsed, str):
        return parsed or None
    if not isinstance(parsed, dict):
        return None
    for key in ("text", "legacy_value", "workitem_name"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def display_summary(
    event_type: str | None,
    content: Any = None,
    *,
    biz_type: str | None = None,
) -> str:
    """Return content text first, then the event/business default summary."""

    content_text = extract_content_text(content)
    if content_text is not None:
        return content_text
    if event_type is not None:
        return notification_summary_for(event_type)
    return _SUMMARY_BY_BIZ_TYPE.get(biz_type or "", _GENERIC_SUMMARY)


def json_object(raw: str | None) -> JsonObject | None:
    """Legacy object-shaped decoder retained for business-data responses."""

    if raw is None:
        return None
    try:
        parsed: Any = json.loads(raw)
    except (TypeError, ValueError):
        return {"legacy_value": raw}
    return parsed if isinstance(parsed, dict) else {"legacy_value": parsed}
