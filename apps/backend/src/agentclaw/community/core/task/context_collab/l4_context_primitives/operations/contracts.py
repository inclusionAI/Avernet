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
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
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
    """L4 Collet and root Work creation; no product, method or binding inputs.

    Creates unassigned Work. Product Instantiate supplies initial participants
    and establishes scoped L4 participation/role facts separately.
    title/description optionally describe the context, not its Task objective
    or completion conditions. Schema/revision/audit values are server-owned.
    """

    to_space: SpaceRef
    initiator_ref: PrincipalRef
    objective: str
    title: str | None = field(default=None, kw_only=True)
    description: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ContextCollabValidationError("objective must be a non-empty string")
        if not isinstance(self.to_space, SpaceRef) or not isinstance(
            self.initiator_ref, PrincipalRef
        ):
            raise ContextCollabValidationError(
                "creation requires typed Space/Principal references"
            )
        validate_collet_text(self.title, "title")
        validate_collet_text(self.description, "description")
        SpaceRef.from_dict(self.to_space.to_dict())
        PrincipalRef.from_dict(self.initiator_ref.to_dict())


class CreatedCollet(Protocol):
    @property
    def collet(self) -> Collet: ...


class ColletWriter(Protocol):
    def create_l4_collet(
        self, command: ColletCreateCommand, actor_ref: PrincipalRef
    ) -> CreatedCollet:
        """Atomically persist Collet, root Task and task.created, without bindings."""
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
    """Start the root Task of an existing Collet, fencing its Collet revision.

    Actor/event identity and public visibility are chosen by the L4 entrypoint,
    not supplied by the caller. Starting neither dispatches an Agent nor creates
    an Activity/external execution attempt.
    """

    expected_version: int
    note: str = ""

    def __post_init__(self) -> None:
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError("expected_version must be positive")
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
