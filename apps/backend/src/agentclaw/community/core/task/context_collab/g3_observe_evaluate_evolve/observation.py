"""G3 观察投影:GET 查询的事件投影与进度统计(spec §6.4)。

``EventProjection`` 是事件在查询响应 ``task_progress.events[]`` 中的
只读投影;``project_events`` 做主干筛选与状态推导;``build_progress``
从独立 Task 状态及可见进度证据统计 ProgressSnapshot。事件状态仅表示
历史位置,不代表 Task 完成。三者都是只读投影,不做 IO 或权威状态写入。

事件顺序即输入顺序(仓储按自增 id 升序,等价接纳顺序);is_trunk 不再
落库,``trunk_ids`` 由调用方(G3 活动链推导)传入,Ruling R9。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskStatus,
)

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.views import (
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)

STATUS_COMPLETED = "completed"
STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class EventProjection:
    """事件在 GET 查询投影中的只读形态(spec §6.4)。

    ``status`` 是查询视角推导出的进度状态,不是权威状态;
    ``summary`` / ``content`` 取自事件 payload,可能为 ``None``。
    """

    event_id: str
    parent_event: str | None
    event_kind: str
    primitive_type: str | None  # 外部部分行可为 NULL;仅展示,不作为 Task 计数
    status: str  # "completed" | "active" | "pending"
    summary: str | None  # payload.get("summary")
    content: str | None  # payload.get("content")(Message)
    occurred_at: datetime


def default_projected_status() -> str:
    """current_event_id 为 None(或不在事件集内)时的投影状态。"""
    return STATUS_PENDING


def active_chain_ids(
    events: Sequence[EventRecord], current_event_id: str | None
) -> frozenset[str]:
    """主干链 = 从 current_event_id 沿 parent_event 回溯到根的事件集合。

    Ruling R9:is_trunk 不再落库,"主事件"在查询时由当前活动链推导;
    current 为 None 或不在事件集内时,主干链为空(全部按派生事件处理)。
    """
    parents = {record.event_id: record.parent_event for record in events}
    chain: set[str] = set()
    node = current_event_id
    while node is not None and node in parents:
        chain.add(node)
        node = parents[node]
    return frozenset(chain)


def project_events(
    events: Sequence[EventRecord],
    *,
    current_event_id: str | None,
    trunk_ids: frozenset[str] | None = None,
) -> list[EventProjection]:
    """把事件记录投影为查询响应事件列表(spec §6.4)。

    ``trunk_ids`` 提供时仅保留其中的事件(不改变事件 status 与顺序,
    §12.2 #4;由 ``active_chain_ids`` 推导);event status:列表中位于
    current 之前为 "completed",等于为 "active",其后(或 current 为
    None / 不在集合内)为 "pending"。保持输入顺序(输入按自增 id 有序)。
    """
    current_index: int | None = None
    if current_event_id is not None:
        for index, record in enumerate(events):
            if record.event_id == current_event_id:
                current_index = index
                break
    projections: list[EventProjection] = []
    for index, record in enumerate(events):
        if trunk_ids is not None and record.event_id not in trunk_ids:
            continue
        if current_index is None:
            status = default_projected_status()
        elif index < current_index:
            status = STATUS_COMPLETED
        elif index == current_index:
            status = STATUS_ACTIVE
        else:
            status = STATUS_PENDING
        payload = record.payload if record.payload is not None else {}
        projections.append(
            EventProjection(
                event_id=record.event_id,
                parent_event=record.parent_event,
                event_kind=record.event_kind,
                primitive_type=record.primitive_type,
                status=status,
                summary=payload.get("summary"),
                content=payload.get("content"),
                occurred_at=record.occurred_at,
            )
        )
    return projections


def build_progress(
    tasks: Sequence[Task],
    *,
    current_event_id: str | None,
    visible_event_ids: frozenset[str],
) -> ProgressSnapshot:
    """Count work items, not events. Reports are observations, never completion.

    Counts are independent of event-list/trunk position. Percent is an equal
    weight average over noncancelled Tasks: DONE=100, otherwise only a visible,
    evidenced executor report contributes. CREATED is pending, not active.
    Callers must first apply G1 visibility to each Task's current-state evidence.
    """
    completed = sum(task.status == TaskStatus.DONE.value for task in tasks)
    active = sum(
        task.status in {TaskStatus.IN_PROGRESS.value, TaskStatus.REVIEWING.value}
        for task in tasks
    )
    blocked = sum(task.status == TaskStatus.BLOCKED.value for task in tasks)
    eligible = [task for task in tasks if task.status != TaskStatus.CANCELLED.value]
    percents = [
        100
        if task.status == TaskStatus.DONE.value
        else (
            task.reported_percent
            if task.reported_percent is not None
            and task.progress_event_ref in visible_event_ids
            else 0
        )
        for task in eligible
    ]
    return ProgressSnapshot(
        percent=round(sum(percents) / len(percents)) if percents else 0,
        completed_count=completed,
        active_count=active,
        blocked_count=blocked,
        current_event_id=current_event_id,
    )
