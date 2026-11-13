"""Authorized response projections; no business-state ownership or writes."""

from __future__ import annotations

from dataclasses import dataclass
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
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
    """Compatibility response view, not ordinary runtime state authority.

    Explicit legacy storage decoding is the only import boundary; new writes
    persist Task and transitions consume Task, not this response view.

    ``status`` 必须是 ``TaskStatus`` 合法字符串;``method_kind`` 必须是
    non-empty display label; it does not own MethodBinding state.
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
            raise ContextCollabValidationError(f"unknown status value: {raw_status!r}")
        return cls(
            objective=_required_str(data, "objective", "CurrentSnapshot"),
            status=raw_status,
            principal_ref=_principal_ref_from_dict(data, "CurrentSnapshot"),
            executor_ref=_executor_ref_from_dict(data, "CurrentSnapshot"),
            method_kind=_required_str(data, "method_kind", "CurrentSnapshot"),
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


def project_current_snapshot(
    task: Task | None, method_kind: str
) -> CurrentSnapshot | None:
    if task is None:
        return None
    return CurrentSnapshot(
        objective=task.objective,
        status=task.status,
        principal_ref=task.principal_ref,
        executor_ref=task.executor_ref,
        method_kind=method_kind,
        progress=ProgressSnapshot(),
    )
