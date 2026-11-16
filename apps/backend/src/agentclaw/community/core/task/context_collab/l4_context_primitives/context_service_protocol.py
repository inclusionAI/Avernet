"""Transport-free L4 collaboration Service API; no L5/L6 composition inputs."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    EventAppendCommand,
    WorkStartCommand,
)


@dataclass
class EventAcceptedResult:
    """追加事件用例结果(spec §6.3 响应)。"""

    accepted: bool
    collab_context_id: str
    event_id: str
    parent_event: str | None
    event_kind: str
    primitive_type: str
    context_version: int
    # 可空:历史部分行没有 Task,非 Work 追加后无任务状态可报。
    current_status: str | None


@dataclass
class TaskProgressProjectionDTO:
    """GET 查询的任务进度投影(spec §6.4 ``task_progress``)。"""

    total_events: int
    events: list[
        dict
    ]  # 每项含 event_id,parent_event,event_kind,primitive_type,status,summary,content


@dataclass
class ContextCollabStatusProjection:
    """查询用例结果(GET 最新状态与任务进度)。

    ``current_snapshot`` is a derived response, never Task authority. It is
    null for an explicitly partial legacy row with no Task, or when the latest
    Task-state evidence is not visible to this caller. Event projection remains
    independently filtered; progress counts Tasks, not historic events.
    """

    collab_context_id: str
    context_version: int
    current_snapshot: dict | None
    current_event: dict | None
    task_progress: TaskProgressProjectionDTO


@runtime_checkable
class ContextServiceProtocol(Protocol):
    """L4 owns creation, Work transitions, event append and authorized reads.

    G1/G4 policy extensions may enforce an installed product's constraints,
    but the L4 service owns schema checks, version fences, Task resolution,
    commit and G3 projections. Starting Work does not dispatch an Agent.
    """

    @abstractmethod
    def create_collet(
        self, command: ColletCreateCommand, context: ActorContext
    ) -> Collet:
        """Authorize and atomically persist Collet, root Task and initial Event.

        Retain zero, one or many executors; never select a Profile/Method or
        create Method/Role/ResourceBindings. No idempotency key; errors propagate.
        """
        ...

    @abstractmethod
    def start(
        self, collab_context_id: str, command: WorkStartCommand, context: ActorContext
    ) -> EventAcceptedResult:
        """L4 Work.start: start this Collet's root Task through G1/G4 and CAS.

        Requires an existing Task and a matching lifecycle transition. Product
        role/admission constraints still apply. The event actor is context.actor;
        the result reports persisted Task status and Collet revision. No Agent
        dispatch, Activity creation, acceptance or Task completion is implied.
        Repeated/stale requests are rejected, not treated as successful no-ops.
        """
        ...

    @abstractmethod
    def append_event(
        self, collab_context_id: str, command: EventAppendCommand, context: ActorContext
    ) -> EventAcceptedResult: ...
    @abstractmethod
    def get_status(
        self, collab_context_id: str, *, trunk_events: bool, context: ActorContext
    ) -> ContextCollabStatusProjection: ...

    @abstractmethod
    def get_events(
        self, collab_context_id: str, *, context: ActorContext
    ) -> list[EventRecord]:
        """按接纳顺序返回 G1 可见事件,包含成果引用等原始 payload;不写状态。"""
        ...
