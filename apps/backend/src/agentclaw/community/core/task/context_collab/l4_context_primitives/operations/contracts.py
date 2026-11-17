"""L4 commands and narrow storage ports.

Storage implementations may return richer application records.

Reader exposes Work authority and creation identities, never a ProductProfile.
Writer must atomically fence Collet and (when supplied) Task revisions; errors
propagate. These internal ports do not replace authorization at public entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet_metadata import (
    validate_collet_text,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskUpdate,
)


@dataclass(frozen=True)
class ColletCreateCommand:
    """Create only a bounded context; no Space, Work or product selection.

    title/description describe the context. Schema/revision/audit are server-owned.
    Task objectives belong to Work.start (or explicit existing-Work creation);
    participants and scoped role bindings come from Product Instantiate.
    """

    initiator_ref: PrincipalRef
    title: str | None = field(default=None, kw_only=True)
    description: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.initiator_ref, PrincipalRef):
            raise ContextCollabValidationError(
                "creation requires a typed Principal reference"
            )
        PrincipalRef.from_dict(self.initiator_ref.to_dict())
        validate_collet_text(self.title, "title")
        validate_collet_text(self.description, "description")


@dataclass(frozen=True)
class TaskCreateCommand:
    """Explicit root Work creation in an existing Collet, without assignment."""

    expected_version: int
    principal_ref: PrincipalRef
    objective: str
    completion_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError("expected_version must be positive")
        if not isinstance(self.principal_ref, PrincipalRef):
            raise ContextCollabValidationError(
                "Task requires a typed Principal reference"
            )
        PrincipalRef.from_dict(self.principal_ref.to_dict())
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ContextCollabValidationError("objective must be a non-empty string")
        if not isinstance(self.completion_conditions, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.completion_conditions
        ):
            raise ContextCollabValidationError(
                "completion_conditions must contain non-empty strings"
            )


class CreatedCollet(Protocol):
    @property
    def collet(self) -> Collet: ...


class CreatedTask(Protocol):
    @property
    def task(self) -> Task: ...


class TaskWriter(Protocol):
    def create_l4_task(
        self,
        collab_context_id: str,
        command: TaskCreateCommand,
        actor_ref: PrincipalRef,
    ) -> CreatedTask:
        """Atomically create root Task/evidence and fence the Collet revision."""
        ...


class ColletWriter(Protocol):
    def create_l4_collet(
        self, command: ColletCreateCommand, actor_ref: PrincipalRef
    ) -> CreatedCollet:
        """Persist only Collet identity/description/audit, without Work or bindings."""
        ...


@dataclass
class AppendEventRecordRequest:
    """``append_event`` 请求;追加不设幂等键,重复提交即重复事件。

    ``task_update`` is G4's resolved Work intent plus exact Task ID/version.
    Both Task and Collet revisions are fenced inside the SQL transaction.
    Ordinary Messages omit it and cannot alter Task state.
    """

    expected_version: int
    event_kind: str
    primitive_type: str
    actor_ref: PrincipalRef
    parent_event: str | None
    visibility: str
    payload: dict
    task_update: TaskUpdate | None = None
    # Internal L4-only draft, persisted atomically with task.created + task.started.
    task_creation: Task | None = None


class CollaborationRecord(Protocol):
    @property
    def collet(self) -> Collet: ...
    @property
    def task(self) -> Task | None: ...
    @property
    def owner_principal_id(self) -> str: ...
    @property
    def designated_executor_id(self) -> str | None: ...


class CollaborationReader(Protocol):
    def get_current_status(
        self, collab_context_id: str
    ) -> CollaborationRecord | None: ...


class AppendedEvent(Protocol):
    @property
    def event(self) -> EventRecord: ...
    @property
    def collet(self) -> Collet: ...
    @property
    def task(self) -> Task | None: ...


class EventWriter(Protocol):
    def append_event(
        self, collab_context_id: str, request: AppendEventRecordRequest
    ) -> AppendedEvent: ...


@dataclass(frozen=True)
class WorkStartCommand:
    """Create/start root Work in an existing Collet, fencing its revision.

    If no Task exists, objective is required and completion_conditions initialize
    the new Task. Existing Work can omit them; supplied values cannot redefine it.
    Assignees come from persisted L4 initial bindings, never caller-supplied IDs.
    Actor/event identity and public visibility are chosen by the L4 entrypoint,
    not supplied by the caller. Starting neither dispatches an Agent nor creates
    an Activity/external execution attempt.
    """

    expected_version: int
    note: str = ""
    objective: str | None = None
    completion_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError("expected_version must be positive")
        if self.objective is not None and (
            not isinstance(self.objective, str) or not self.objective.strip()
        ):
            raise ContextCollabValidationError("objective must be a non-empty string")
        if not isinstance(self.completion_conditions, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.completion_conditions
        ):
            raise ContextCollabValidationError("invalid completion_conditions")
        if not isinstance(self.note, str):
            raise ContextCollabValidationError("Work start note must be a string")


@dataclass
class EventAppendCommand:
    """追加事件命令;字段与 spec §6.3 表格一一对应。

    追加不设幂等键(spec 作者裁决):重复提交即重复事件。
    """

    expected_version: int
    event_kind: str
    primitive_type: str
    actor_ref: PrincipalRef
    parent_event: str | None
    visibility: str
    payload: dict


class ColletLifecycleStore(Protocol):
    """Collet 生命周期状态存取协议(P1 内存实现,P2 持久化整体替换)。"""

    def get(self, collet_id: str) -> ColletState | None:
        """取状态;未见过的 collet 返回 ``None``(不落种)。"""
        ...

    def set(self, collet_id: str, state: ColletState) -> None:
        """写入新状态;整锁内原子替换。"""
        ...

    def get_or_seed(self, collet_id: str) -> ColletState:
        """取状态;未见过的 collet 种子 ``ACTIVE`` 并写回。"""
        ...
