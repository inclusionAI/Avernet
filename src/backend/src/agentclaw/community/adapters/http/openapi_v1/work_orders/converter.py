"""Presentation compatibility for persisted work-order JSON and title codes."""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderMessageTitle,
    WorkOrderStatus,
    WorkOrderTitleKey,
    notification_summary_for,
    notification_title_for,
)

JsonObject = dict[str, Any]

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
_TITLE_KEY_BY_STATUS = {
    WorkOrderStatus.PENDING: WorkOrderTitleKey.SPACE_JOIN_PENDING,
    WorkOrderStatus.APPROVED: WorkOrderTitleKey.SPACE_JOIN_APPROVED,
    WorkOrderStatus.REJECTED: WorkOrderTitleKey.SPACE_JOIN_REJECTED,
}


def display_title(
    stored_title: str | None,
    *,
    event_type: str | None = None,
    biz_type: str | None = None,
    status: WorkOrderStatus | None = None,
) -> str:
    """Resolve titles from the event contract with legacy compatibility."""

    if event_type is not None:
        return notification_title_for(event_type, stored_title)

    key = _TITLE_KEY_BY_STORED_VALUE.get(stored_title or "")
    if key is not None:
        return _TITLE_BY_KEY[key]
    if stored_title:
        return stored_title
    if biz_type == WorkOrderBizType.SPACE_JOIN.value and status is not None:
        return _TITLE_BY_KEY[_TITLE_KEY_BY_STATUS[status]]
    return stored_title


def display_summary(event_type: str | None) -> str:
    """Return the stable list/detail summary for an event."""
    return notification_summary_for(event_type)


def json_object(raw: str | None) -> JsonObject | None:
    """Decode stored JSON objects and safely expose historical scalar text.

    New API writes always store JSON objects. Historical rows may contain raw
    text or another JSON scalar, so they are exposed under ``legacy_value`` to
    keep the response contract object-shaped without losing the old value.
    """

    if raw is None:
        return None
    try:
        parsed: Any = json.loads(raw)
    except (TypeError, ValueError):
        return {"legacy_value": raw}
    return parsed if isinstance(parsed, dict) else {"legacy_value": parsed}
