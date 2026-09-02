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
    ("okr-implementation", ("okr", "转化率", "双十一", "大促")),
)
