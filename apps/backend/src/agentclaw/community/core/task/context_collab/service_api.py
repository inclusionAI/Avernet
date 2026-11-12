"""Context collab Service API 契约(transport-agnostic,spec §6)。

命令/结果对象字段与三个北向 API 请求表格一一对应;delivery 层做
解析与序列化,本模块不感知 HTTP。返回投影命名带 ``Projection``
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


@dataclass
class ContextCreateCommand:
    """创建协作上下文命令;字段与 spec §6.2 表格一一对应。

    创建不设幂等键(spec 作者裁决:调用方无需传入防重号)。
    """

    space: SpaceRef
    principal_ref: PrincipalRef
    product_kind: str | None
    workflow_kind: str | None
    method_kind: str
    executor_ref: AgentRef | None
    objective: str
    resource_refs: list[ResourceRef]


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


@dataclass
class ContextCollabCreatedResult:
    """创建用例结果;``current_snapshot`` 为 §6.2 响应形态(含 progress)。"""

    collab_context_id: str
    context_version: int
    current_snapshot: dict  # CurrentSnapshot 派生的 §6.2 响应投影
    current_event: dict      # 初始事件基础字段投影


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
    # 可空:快照为 NULL 的外部部分行追加事件后无状态可报。
    current_status: str | None


@dataclass
class TaskProgressProjectionDTO:
    """GET 查询的任务进度投影(spec §6.4 ``task_progress``)。"""

    total_events: int
    events: list[dict]  # 每项含 event_id,parent_event,event_kind,primitive_type,status,summary,content


@dataclass
class ContextCollabStatusProjection:
    """查询用例结果(GET 最新状态与任务进度)。

    ``current_snapshot`` 可空:外部写入的只有 spec 的聚合行返回 null,
    事件链投影照常。
    """

    collab_context_id: str
    context_version: int
    current_snapshot: dict | None
    current_event: dict | None
    task_progress: TaskProgressProjectionDTO


@runtime_checkable
class ContextCollabServiceProtocol(Protocol):
    """三个北向用例的服务协议;delivery 依赖本协议,不依赖实现类。"""

    def create_context(self, command: ContextCreateCommand,
                       context: ActorContext) -> ContextCollabCreatedResult: ...
    def append_event(self, collab_context_id: str, command: EventAppendCommand,
                     context: ActorContext) -> EventAcceptedResult: ...
    def get_status(self, collab_context_id: str, *, trunk_events: bool,
                   context: ActorContext) -> ContextCollabStatusProjection: ...
