"""Context collab 仓储契约:``ContextCollabRepository`` 的 Protocol 与
出入参类型(Task 8/9 落地 ORM 实现)。

本文件目前只声明协议与 dataclass,不 import SQLAlchemy /
``DatabasePlugin``,因此任何测试都用不到数据库;真实现
(``ContextCollabRepository``,``@inject def __init__(self, db)``)
与本任务服务测试用的手写 fake 共享这里的签名。
``AppendEventRecordRequest`` / ``EventAppendedRecord`` /
``ContextStatusRecord`` 在追加/查询任务中补充定义,协议签名先行锁定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)


@dataclass
class InitialEventDraft:
    """创建时固定的初始事件草稿(service 提供):task.created / Task。"""

    event_kind: str       # "task.created"
    primitive_type: str   # "Task"
    actor_ref: PrincipalRef  # 创建人
    payload: dict         # {"objective": <objective>}


@dataclass
class CreateContextRecordRequest:
    """仓储创建请求;``create_request_id`` 是 G2 幂等键。"""

    create_request_id: str
    context_spec: ContextSpec
    initial_event: InitialEventDraft


@dataclass
class ContextCreatedRecord:
    """仓储创建返回;``created=False`` 表示幂等命中返回原上下文。"""

    collet: Collet
    initial_event: EventRecord
    created: bool


@runtime_checkable
class ContextCollabRepositoryProtocol(Protocol):
    """Collet 聚合的两张表(ac_context_collab_collet/event)存取协议。

    ``create_context`` 首次创建插 collet(initial
    snapshot/context_version=1)+ 初始事件(sequence_no=1、
    is_trunk=True、parent=None),重复 ``create_request_id`` 返回
    原行且 ``created=False``。
    """

    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord: ...
    def append_event(self, collab_context_id: str, request: "AppendEventRecordRequest") -> "EventAppendedRecord": ...
    def get_current_status(self, collab_context_id: str, *, trunk_events: bool) -> "ContextStatusRecord | None": ...
