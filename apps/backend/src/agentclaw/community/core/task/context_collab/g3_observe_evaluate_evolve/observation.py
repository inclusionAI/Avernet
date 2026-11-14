"""G3 观察投影:GET 查询的事件投影与进度统计(spec §6.4)。

``EventProjection`` 是事件在查询响应 ``task_progress.events[]`` 中的
只读投影;``project_events`` 做主干筛选与状态推导;``build_progress``
从投影统计 ProgressSnapshot。三者都是非权威查询投影,不反向改写
Collet 快照(§12.2 #6),也不做任何 IO。

事件顺序即输入顺序(仓储按自增 id 升序,等价接纳顺序);is_trunk 不再
落库,``trunk_ids`` 由调用方(G3 活动链推导)传入,Ruling R9。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
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
    event_seq: int
    parent_event: str | None
    event_kind: str
    primitive_type: str | None  # 外部部分行可为 NULL;统计仅排除 Message
    actor_id: str
    actor_type: str
    execution_id: str
    context_version: int | None
    status: str  # "completed" | "active" | "pending"
    summary: str | None  # payload.get("summary")
    content: str | None  # payload.get("content")(Message)
    artifacts: tuple[object, ...]
    created_at: datetime
    payload: dict[str, object] = field(default_factory=dict)


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
        artifacts = payload.get("artifacts")
        projections.append(
            EventProjection(
                event_id=record.event_id,
                event_seq=index + 1,
                parent_event=record.parent_event,
                event_kind=record.event_kind,
                primitive_type=record.primitive_type,
                actor_id=(
                    record.actor_ref.principal_id
                    if record.actor_ref is not None
                    else ""
                ),
                actor_type=(
                    record.actor_ref.principal_type
                    if record.actor_ref is not None
                    else ""
                ),
                execution_id=str(
                    payload.get("run_id") or payload.get("execution_id") or ""
                ),
                context_version=record.context_version_after,
                status=status,
                summary=payload.get("summary"),
                content=payload.get("content"),
                artifacts=(tuple(artifacts) if isinstance(artifacts, list) else ()),
                created_at=record.occurred_at,
                payload=dict(payload),
            )
        )
    return projections


def build_progress(
    events: Sequence[EventProjection],
    *,
    current_event_id: str | None,
    last_percent: int | None,
) -> ProgressSnapshot:
    """从事件投影统计任务进度快照(spec §6.4)。

    completed_count/active_count/blocked_count 由投影 status 统计,且仅
    统计 primitive_type != "Message" 的事件(Message 是互动内容,不是
    进度;spec §6.4 示例:completed_count=2 即 evt_001/003 两个 Task
    事件)。percent = last_percent(最后一个 payload 带 "percent" 整数的
    事件),否则 0 if 无计数事件 else round(completed_count*100/计数的
    非 Message 事件数)。current_event_id 沿用调用方传入的
    collet.current_event_id,不重新推导。
    """
    completed_count = 0
    active_count = 0
    blocked_count = 0
    for projection in events:
        if projection.primitive_type == "Message":
            continue
        if projection.status == STATUS_COMPLETED:
            completed_count += 1
        elif projection.status == STATUS_ACTIVE:
            active_count += 1
        elif projection.status == STATUS_BLOCKED:
            blocked_count += 1
    counted = completed_count + active_count + blocked_count

    if last_percent is not None:
        percent = last_percent
    elif counted == 0:
        percent = 0
    else:
        percent = round(completed_count * 100 / counted)

    return ProgressSnapshot(
        percent=percent,
        completed_count=completed_count,
        active_count=active_count,
        blocked_count=blocked_count,
        current_event_id=current_event_id,
    )
