"""L2 Agent Network 稳定引用值对象(Principal / Agent)。

只保存外部身份系统的 ObjectRef,不复制注册表;引用缺字段或空串时抛出
``ContextCollabValidationError``。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


def _required_str(data: dict, key: str, ref: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{ref}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class PrincipalRef:
    """L2 责任主体引用;``principal_type`` 取 "human" / "bot" / "org" / "system"。"""

    principal_type: str
    principal_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "PrincipalRef":
        return cls(
            principal_type=_required_str(data, "principal_type", "PrincipalRef"),
            principal_id=_required_str(data, "principal_id", "PrincipalRef"),
        )

    def to_dict(self) -> dict:
        return {
            "principal_type": self.principal_type,
            "principal_id": self.principal_id,
        }


@dataclass(frozen=True)
class AgentRef:
    """L2 执行 Agent 引用;``agent_type`` 取 "bot" / "service"。"""

    agent_type: str
    agent_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "AgentRef":
        return cls(
            agent_type=_required_str(data, "agent_type", "AgentRef"),
            agent_id=_required_str(data, "agent_id", "AgentRef"),
        )

    def to_dict(self) -> dict:
        return {"agent_type": self.agent_type, "agent_id": self.agent_id}
