"""Context collab Service API 契约(transport-agnostic,spec §6)。

创建返回 L4 ``Collet``;其余命令/结果由 delivery 层解析与序列化,
本模块不感知 HTTP。查询投影命名带 ``Projection``
后缀,避免与 adapter Pydantic DTO(spec §6.5 的
``ContextCollabStatusResponse`` 等 wire 名)重名。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,  # 兼容再导出:权威居所在 G1(2026-11-13 归位),本模块
)  # 的既有 import 零破坏;新代码请从 G1 新家 import。
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import Collet
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    EventAppendCommand as EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)


@dataclass
class ColletCreateCommand:
    """创建协作上下文命令;字段与 spec §6.2 表格一一对应。

    创建不设幂等键(spec 作者裁决:调用方无需传入防重号)。
    """

    to_space: SpaceRef
    principal_ref: PrincipalRef
    product_kind: str | None
    workflow_kind: str | None
    method_kind: str
    executor_ref: AgentRef | None
    objective: str
    resource_refs: list[ResourceRef]
    # Optional exact L6 selection; omission preserves the generic v1 fallback.
    product_profile_ref: VersionedProfileRef | None = None


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
class ContextCollabServiceProtocol(Protocol):
    """北向创建、追加与只读查询协议;delivery 依赖本协议,不依赖实现类。"""

    def create_collet(
        self, command: ColletCreateCommand, context: ActorContext
    ) -> Collet:
        """创建并返回持久化的 Collet;成功前完成初始 Task/Event 与 L5 绑定。

        不返回 Task 或 HTTP 快照;创建不设幂等键,写入失败原样抛出。
        """
        ...

    def append_event(
        self, collab_context_id: str, command: EventAppendCommand, context: ActorContext
    ) -> EventAcceptedResult: ...
    def get_status(
        self, collab_context_id: str, *, trunk_events: bool, context: ActorContext
    ) -> ContextCollabStatusProjection: ...

    def get_events(
        self, collab_context_id: str, *, context: ActorContext
    ) -> list[EventRecord]:
        """按接纳顺序返回 G1 可见事件,包含成果引用等原始 payload;不写状态。"""
        ...
