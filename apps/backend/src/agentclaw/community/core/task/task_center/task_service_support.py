"""Static support definitions shared by the TaskService facade."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentclaw.community.core.task.domain.models import Status, TaskInfo


def parse_status_filter(status: str | None) -> list[Status] | None:
    """Parse comma-separated runtime statuses for repository filtering."""
    if not status or not status.strip():
        return None
    parts = [token.strip().upper() for token in status.split(",") if token.strip()]
    if not parts:
        return None
    return [Status(part) for part in parts]


def build_submit_trajectory_event_kwargs(
    task_info: TaskInfo, submitted_at_ms: int,
) -> dict[str, Any]:
    """Build REQ-6 SUBMIT fields for the generic trajectory emitter."""
    try:
        payload = json.dumps(
            task_info.task_spec.to_dict(), sort_keys=True, ensure_ascii=False
        )
        task_spec_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 trajectory assembly must not block submit
        task_spec_digest = None
    try:
        raw_task_type = task_info.execution_config.get("task_type")
        task_type = getattr(raw_task_type, "value", raw_task_type)
    except Exception:  # noqa: BLE001 trajectory assembly must not block submit
        task_type = None
    return {
        "action_result": "success",
        "action_input": task_spec_digest,
        "ext_info": {
            "source": task_info.source_type,
            "task_type": task_type,
            "owner_user_id": task_info.owner_user_id,
            "owner_bot_id": task_info.owner_bot_id,
            "submitted_at": submitted_at_ms,
        },
        "status_from": None,
        "status_to": Status.PENDING,
        "attempt": 0,
        "now_ms": submitted_at_ms,
    }


def resolve_coop_collab_mode(has_yaml: bool, group_kind: str | None) -> str:
    """Resolve the BCS collaboration mode from task execution metadata."""
    if has_yaml:
        return "state_machine"
    if group_kind in ("chat", "manager_worker"):
        return group_kind
    if group_kind is None:
        return "manager_worker"
    if group_kind == "state_machine":
        raise ValueError("group_kind=state_machine 需要 yaml 定义")
    raise ValueError(f"未知 group_kind: {group_kind!r}")


def split_owner_bot_id(owner_bot_id: str, owner_user_id: str) -> tuple[str, str]:
    """Normalize legacy ``bot_id:owner_id`` composite storage without writing it back.

    Extracted verbatim from :class:`TaskService` (was the ``_split_owner_bot_id``
    ``@staticmethod``) to free headroom under the 1000-line CI limit on
    ``task_service.py`` — pure function, no ``self`` reference, behavior
    identical. ``TaskService`` and its only internal call sites now call this
    module-level function directly.
    """
    bot_id, separator, embedded_owner_id = str(owner_bot_id or "").partition(":")
    effective_owner_id = (
        embedded_owner_id if separator and embedded_owner_id else owner_user_id
    )
    return bot_id, effective_owner_id


# Content routing is internal to execute. There is no public template endpoint.
STATIC_PLAN_TEMPLATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 商家经营目标 → 经营方案模板。关键词保持为业务语义词，避免仅凭“活动/投诉”等单一
    # 专业词误触发整条经营方案链路；匹配仍由 execute 统一做首个命中优先。
    (
        "merchant-operations-goal-to-plan",
        (
            "门店经营",
            "商家经营",
            "经营目标",
            "经营方案",
            "经营计划",
            "店庆",
            "周年庆",
            "门店营业额",
            "到店复购",
            "到店转化",
            "套餐复购",
        ),
    ),
    # 串行接力版置顶承接 OKR 内容;老 okr-implementation 保留于 plans/(by-id/历史测试可达),
    # 同关键字在后被遮蔽/内容不再触发。切回老模板只需删掉 relay 条目。
    ("okr-implementation-relay", ("okr", "转化率", "双十一", "大促")),
    ("okr-implementation", ("okr", "转化率", "双十一", "大促")),
)
