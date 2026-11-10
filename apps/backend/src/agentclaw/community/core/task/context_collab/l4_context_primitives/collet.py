"""L4 Collet:协作上下文聚合根及其快照(spec §3.4)。

``ContextSpec`` 承载创建请求中长期保留的引用与目标;``CurrentSnapshot``
是 Collet 的当前读写快照(不是第二权威状态);``ProgressSnapshot`` 保存
GET 查询投影使用的任务进度计数;``Collet`` 聚合根只持有快照与版本,事件
事实一律由 Event record 承载。本模块只有纯值对象,不做任何 IO。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _optional_str(data: dict, key: str, owner: str) -> str | None:
    """从 dict 中取出可空字符串字段;缺失或 ``None`` 返回 ``None``。"""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string or null, got: {value!r}"
        )
    return value


def _required_dict(data: dict, key: str, owner: str) -> dict:
    """从 dict 中取出必填对象字段;缺失或非 dict 抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    return value


def _method_kind_from_dict(data: dict, owner: str) -> str:
    """校验并返回 ``MethodKind`` 合法字符串;未知 wire 值抛校验错误。"""
    return MethodKind.from_wire(_required_str(data, "method_kind", owner)).value


def _principal_ref_from_dict(data: dict, owner: str) -> PrincipalRef:
    return PrincipalRef.from_dict(_required_dict(data, "principal_ref", owner))


def _executor_ref_from_dict(data: dict, owner: str) -> AgentRef | None:
    """取可空执行 Agent 引用;``None`` / 缺失表示当前没有指定执行 Agent。"""
    value = data.get("executor_ref")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.executor_ref must be an object or null, got: {value!r}"
        )
    return AgentRef.from_dict(value)


@dataclass
class ContextSpec:
    """创建请求中长期保留的协作上下文参数(spec §9.2)。

    ``executor_ref`` 为 ``None`` 表示方法未指定执行 Agent(公开讨论即如此);
    ``resource_refs`` 是外部资源引用元组,不复制资源正文;``product_kind`` /
    ``workflow_kind`` 是可选的 L6 Profile 引用,不建平行权威类型。
    """

    space: SpaceRef
    principal_ref: PrincipalRef
    method_kind: str
    executor_ref: AgentRef | None
    objective: str
    resource_refs: tuple[ResourceRef, ...]
    product_kind: str | None
    workflow_kind: str | None

    @classmethod
    def from_dict(cls, data: dict) -> "ContextSpec":
        raw_refs = data.get("resource_refs")
        if not isinstance(raw_refs, list):
            raise ContextCollabValidationError(
                f"ContextSpec.resource_refs must be a list, got: {raw_refs!r}"
            )
        return cls(
            space=SpaceRef.from_dict(_required_dict(data, "space", "ContextSpec")),
            principal_ref=_principal_ref_from_dict(data, "ContextSpec"),
            method_kind=_method_kind_from_dict(data, "ContextSpec"),
            executor_ref=_executor_ref_from_dict(data, "ContextSpec"),
            objective=_required_str(data, "objective", "ContextSpec"),
            resource_refs=tuple(
                ResourceRef.from_dict(item) for item in raw_refs
            ),
            product_kind=_optional_str(data, "product_kind", "ContextSpec"),
            workflow_kind=_optional_str(data, "workflow_kind", "ContextSpec"),
        )

    def to_dict(self) -> dict:
        return {
            "space": self.space.to_dict(),
            "principal_ref": self.principal_ref.to_dict(),
            "method_kind": self.method_kind,
            "executor_ref": (
                self.executor_ref.to_dict() if self.executor_ref else None
            ),
            "objective": self.objective,
            "resource_refs": [ref.to_dict() for ref in self.resource_refs],
            "product_kind": self.product_kind,
            "workflow_kind": self.workflow_kind,
        }


@dataclass
class ProgressSnapshot:
    """任务进度计数与当前事件引用;GET 查询投影,不是权威状态。"""

    percent: int = 0
    completed_count: int = 0
    active_count: int = 0
    blocked_count: int = 0
    current_event_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "percent": self.percent,
            "completed_count": self.completed_count,
            "active_count": self.active_count,
            "blocked_count": self.blocked_count,
            "current_event_id": self.current_event_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProgressSnapshot":
        """从快照 JSON 恢复进度;缺省计数按 0 处理。"""
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"ProgressSnapshot must be an object, got: {data!r}"
            )
        return cls(
            percent=data.get("percent", 0),
            completed_count=data.get("completed_count", 0),
            active_count=data.get("active_count", 0),
            blocked_count=data.get("blocked_count", 0),
            current_event_id=data.get("current_event_id"),
        )


@dataclass
class CurrentSnapshot:
    """Collet 的当前读写快照(spec §3.4.2)。

    ``status`` 必须是 ``TaskStatus`` 合法字符串;``method_kind`` 必须是
    ``MethodKind`` 合法字符串;两者在 ``from_dict`` 时校验。
    """

    objective: str
    status: str
    principal_ref: PrincipalRef
    executor_ref: AgentRef | None
    method_kind: str
    progress: ProgressSnapshot

    @classmethod
    def from_dict(cls, data: dict) -> "CurrentSnapshot":
        raw_status = _required_str(data, "status", "CurrentSnapshot")
        if raw_status not in (status.value for status in TaskStatus):
            raise ContextCollabValidationError(
                f"unknown status value: {raw_status!r}"
            )
        return cls(
            objective=_required_str(data, "objective", "CurrentSnapshot"),
            status=raw_status,
            principal_ref=_principal_ref_from_dict(data, "CurrentSnapshot"),
            executor_ref=_executor_ref_from_dict(data, "CurrentSnapshot"),
            method_kind=_method_kind_from_dict(data, "CurrentSnapshot"),
            progress=ProgressSnapshot.from_dict(
                _required_dict(data, "progress", "CurrentSnapshot")
            ),
        )

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "status": self.status,
            "principal_ref": self.principal_ref.to_dict(),
            "executor_ref": (
                self.executor_ref.to_dict() if self.executor_ref else None
            ),
            "method_kind": self.method_kind,
            "progress": self.progress.to_dict(),
        }


@dataclass
class Collet:
    """L4 协作上下文聚合根。

    持有聚合标识、创建幂等键、创建参数、当前快照与聚合版本;事件事实由
    ``ac_context_collab_event`` 表(见 ``event_envelope.EventRecord``)承载,
    快照由事件改写,不在此复制事件历史。
    """

    collab_context_id: str
    create_request_id: str
    context_spec: ContextSpec
    current_snapshot: CurrentSnapshot
    current_event_id: str | None
    context_version: int
