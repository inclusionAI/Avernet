"""G3 观察投影:GET 查询的事件投影与进度统计(spec §6.4)。

``EventProjection`` 是事件在查询响应 ``task_progress.events[]`` 中的
只读投影;``project_events`` 做主干筛选与状态推导;``build_progress``
从投影统计 ProgressSnapshot。三者都是非权威查询投影,不反向改写
Collet 快照(§12.2 #6),也不做任何 IO。
"""

from __future__ import annotations

from dataclasses import dataclass
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
    parent_event: str | None
    event_kind: str
    primitive_type: str
    status: str  # "completed" | "active" | "pending"
    summary: str | None  # payload.get("summary")
    content: str | None  # payload.get("content")(Message)
    occurred_at: datetime


def default_projected_status() -> str:
    """current_event_id 为 None(或不在事件集内)时的投影状态。"""
    return STATUS_PENDING


def _current_sequence_no(
    events: Sequence[EventRecord], current_event_id: str | None
) -> int | None:
    """找到 current 事件的 sequence_no;找不到视为没有 current。"""
    if current_event_id is None:
        return None
    for record in events:
        if record.event_id == current_event_id:
            return record.sequence_no
    return None


def _projected_status(sequence_no: int, current_sequence_no: int | None) -> str:
    """sequence 小于 current 为 completed,等于为 active,其后为 pending。"""
    if current_sequence_no is None:
        return default_projected_status()
    if sequence_no < current_sequence_no:
        return STATUS_COMPLETED
    if sequence_no == current_sequence_no:
        return STATUS_ACTIVE
    return STATUS_PENDING


def project_events(
    events: Sequence[EventRecord],
    *,
    current_event_id: str | None,
    trunk_events: bool,
) -> list[EventProjection]:
    """把事件记录投影为查询响应事件列表(spec §6.4)。

    trunk_events=True 仅筛 is_trunk=True(不改变事件 status 与顺序,
    §12.2 #4);event status:sequence 小于 current 为 "completed",等于为
    "active",其后(或 current 为 None)为 "pending"。保持输入顺序
    (输入按 sequence_no 有序)。
    """
    current_sequence_no = _current_sequence_no(events, current_event_id)
    projections: list[EventProjection] = []
    for record in events:
        if trunk_events and not record.is_trunk:
            continue
        projections.append(
            EventProjection(
                event_id=record.event_id,
                parent_event=record.parent_event,
                event_kind=record.event_kind,
                primitive_type=record.primitive_type,
                status=_projected_status(record.sequence_no, current_sequence_no),
                summary=record.payload.get("summary"),
                content=record.payload.get("content"),
                occurred_at=record.occurred_at,
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
