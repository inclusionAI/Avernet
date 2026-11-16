"""L4 Decision 值对象(spec §3.3 层表)。

责任主体对 Task / 交付的裁决语义;没有独立生命周期和独立表,裁决
事实由 Event record 承载(``task.accepted`` / ``task.rejected`` 等
event_name),本对象仅作为事件 payload / 查询投影。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _required_principal(data: dict, owner: str) -> PrincipalRef:
    """取必填裁决主体引用;缺字段由 ``PrincipalRef.from_dict`` 拒绝。"""
    value = data.get("decided_by")
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.decided_by must be an object, got: {value!r}"
        )
    return PrincipalRef.from_dict(value)


@dataclass(frozen=True)
class Decision:
    """裁决语义投影;``decided_by`` 是执行裁决的责任主体(Principal)。"""

    decision_id: str
    decision_kind: str
    decided_by: PrincipalRef

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        return cls(
            decision_id=_required_str(data, "decision_id", "Decision"),
            decision_kind=_required_str(data, "decision_kind", "Decision"),
            decided_by=_required_principal(data, "Decision"),
        )

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "decision_kind": self.decision_kind,
            "decided_by": self.decided_by.to_dict(),
        }
