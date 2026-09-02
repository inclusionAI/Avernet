"""Static support definitions shared by the TaskService facade."""

from __future__ import annotations

from agentclaw.community.core.task.domain.models import Status


def parse_status_filter(status: str | None) -> list[Status] | None:
    """Parse comma-separated runtime statuses for repository filtering."""
    if not status or not status.strip():
        return None
    parts = [token.strip().upper() for token in status.split(",") if token.strip()]
    if not parts:
        return None
    return [Status(part) for part in parts]


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
