"""Transport-free L4 collaboration Service API; no L5/L6 composition inputs."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_collet import (
    LegacyCollet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    EventAppendCommand,
    TaskCreateCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task


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
    ) -> LegacyCollet:
        """Authorize and persist only Collet identity, description and audit.

        No Space, root Task, initial Work event, Profile or bindings are created.
        No idempotency key; write failures propagate. New Collets carry complete
        UTC audit from the authenticated actor and a derived representation ETag.
        """
        ...

    @abstractmethod
    def create_task(
        self, collab_context_id: str, command: TaskCreateCommand, context: ActorContext
    ) -> Task:
        """Explicitly create this Collet's root Task and task.created, not start it.

        Only the Collet initiator may create Work; principal_ref supplies Task
        accountability independently. Fences Collet version, creates Task at
        version 1 and advances Collet once. Duplicate roots fail without writes.
        Works before or after product instantiation: existing Collet executor
        binding evidence supplies assignees; absent bindings leave Work unassigned.
        Preserves installed selection metadata and participation. No product
        selection, method initialization, participant binding or dispatch.
        """
        ...

    @abstractmethod
    def start(
        self, collab_context_id: str, command: WorkStartCommand, context: ActorContext
    ) -> EventAcceptedResult:
        """L4 Work.start: start this Collet's root Task through G1/G4 and CAS.

        With no root Task, requires command.objective and creates/starts Work
        atomically after G1/G4 validation, using actual initial executor bindings.
        Existing Tasks retain identity, objective and completion conditions.
        Requires a matching lifecycle transition; product role/admission checks
        still apply. The event actor is context.actor;
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
