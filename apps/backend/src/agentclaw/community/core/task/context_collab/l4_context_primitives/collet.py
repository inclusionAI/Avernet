"""L4 Collet:协作上下文聚合根与伴随值对象(spec §3.4)。

``Collet`` 聚合根只有 ACCS 六字段(聚合标识 / 发起主体引用 / 顶层
collet 状态 / 归属 Space / 当前事件指针 / 聚合版本);``context_spec``
与 ``current_snapshot`` 不再挂在聚合上——spec 与快照是仓储 record 的
伴随投影字段(落库行同值,见 persistence.repository 出入参 dataclass)。
``ContextSpec`` 承载创建请求中长期保留的引用与目标;``CurrentSnapshot``
是 Collet 的当前读写快照(不是第二权威状态);``ProgressSnapshot`` 保存
GET 查询投影使用的任务进度计数;事件事实一律由 Event record 承载。
本模块只有纯值对象,不做任何 IO。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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


class ColletState(str, Enum):
    """Collet 大写机器状态词汇(spec §4)。

    与 ``CurrentSnapshot.status`` 的 ``TaskStatus`` 小写词汇是两套闭合
    词汇:前者服务 G4 ``collet_lifecycle`` 状态机(见
    ``g4_contracts_consistency.context_state_machines``),后者仍是
    事件快照投影的 task 状态列。两套词汇不互相渗透/schema 不互认。
    """

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


# Collet 顶层键集与合法 state 值(from_dict 严格解码的两张白名单)。
_COLLET_KEYS = frozenset(
    {
        "collet_id",
        "initiator_ref",
        "state",
        "scope",
        "current_event_id",
        "version",
    }
)
_COLLET_STATES = frozenset({state.value for state in ColletState})


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
    """L4 协作上下文聚合根(ACCS 六字段,spec §3.4)。

    只持有协议身份与顶层生命:聚合标识(``collab_context_id`` 落库列同名
    同值)、发起主体引用(与初始 task.created 事件的 actor_ref 同值)、
    ``collet_lifecycle`` 顶层状态、归属 Space、当前事件指针与聚合版本。
    ``context_spec`` / ``current_snapshot`` 不在聚合上——它们是仓储
    record 的伴随投影字段;事件事实由 ``ac_context_collab_event`` 表
    (见 ``event_envelope.EventRecord``)承载,快照由事件改写,不在此
    复制事件历史。
    """

    collet_id: str
    initiator_ref: PrincipalRef
    state: ColletState
    scope: SpaceRef
    current_event_id: str | None
    version: int

    @classmethod
    def from_dict(cls, data: dict) -> "Collet":
        """六字段严格解码;``to_dict`` 结果可无损 round-trip。

        校验:``collet_id`` 非空;``initiator_ref`` / ``scope`` /
        ``state`` / ``version`` 必填;``state`` 必须命中
        ``ColletState`` 值;``version`` 必须是 ≥1 整数;未知顶层键
        (键集 = 六字段名)拒绝。
        """
        if not isinstance(data, dict):
            raise ContextCollabValidationError(
                f"Collet must be an object, got: {data!r}"
            )
        unknown = set(data) - _COLLET_KEYS
        if unknown:
            raise ContextCollabValidationError(
                f"Collet has unknown top-level keys: {sorted(unknown)}; "
                f"allowed keys: {sorted(_COLLET_KEYS)}"
            )
        raw_state = data.get("state")
        if not isinstance(raw_state, str) or raw_state not in _COLLET_STATES:
            raise ContextCollabValidationError(
                f"unknown state value: {raw_state!r}"
            )
        raw_version = data.get("version")
        if (
            isinstance(raw_version, bool)
            or not isinstance(raw_version, int)
            or raw_version < 1
        ):
            raise ContextCollabValidationError(
                f"Collet.version must be an integer >= 1, got: {raw_version!r}"
            )
        return cls(
            collet_id=_required_str(data, "collet_id", "Collet"),
            initiator_ref=PrincipalRef.from_dict(
                _required_dict(data, "initiator_ref", "Collet")
            ),
            state=ColletState(raw_state),
            scope=SpaceRef.from_dict(_required_dict(data, "scope", "Collet")),
            current_event_id=_optional_str(data, "current_event_id", "Collet"),
            version=raw_version,
        )

    def to_dict(self) -> dict:
        return {
            "collet_id": self.collet_id,
            "initiator_ref": self.initiator_ref.to_dict(),
            "state": self.state.value,
            "scope": self.scope.to_dict(),
            "current_event_id": self.current_event_id,
            "version": self.version,
        }
