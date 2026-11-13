"""L4 Participant / RoleBinding 值对象(spec §3.4 表)。

参与者是 Principal 或 Agent 在 Collet 内的协作身份,作为 Collet JSON /
查询对象保存;RoleBinding 把产品角色绑定到 Participant,作为 Collet
JSON / 事件事实保存。两者都没有独立生命周期,不建表。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)

ROLE_RESPONSIBLE_USER = "responsible_user"
"""L6 ScenarioRole 映射到的协作角色:责任主体(spec §5 示例)。"""

ROLE_SELECTED_AGENT = "selected-agent"
"""L6 ScenarioRole 映射到的协作角色:指定执行 Agent(spec §5 示例)。"""


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _optional_ref(data: dict, key: str, owner: str, ref_cls):
    """取可空嵌套引用对象;缺失或 ``None`` 返回 ``None``。"""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object or null, got: {value!r}"
        )
    return ref_cls.from_dict(value)


@dataclass(frozen=True)
class Participant:
    """Principal 或 Agent 在 Collet 内的协作身份。

    ``principal_ref`` 与 ``agent_ref`` 二选一;同一对象不能既是 Principal
    又是 Agent(与 L2 引用层级一致,不引入 Participant 注册表)。
    """

    participant_id: str
    principal_ref: PrincipalRef | None
    agent_ref: AgentRef | None

    @classmethod
    def from_dict(cls, data: dict) -> "Participant":
        principal_ref = _optional_ref(
            data, "principal_ref", "Participant", PrincipalRef
        )
        agent_ref = _optional_ref(data, "agent_ref", "Participant", AgentRef)
        if (principal_ref is None) == (agent_ref is None):
            raise ContextCollabValidationError(
                "Participant must reference exactly one PrincipalRef or AgentRef"
            )
        return cls(
            participant_id=_required_str(data, "participant_id", "Participant"),
            principal_ref=principal_ref,
            agent_ref=agent_ref,
        )

    def to_dict(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "principal_ref": (
                self.principal_ref.to_dict() if self.principal_ref else None
            ),
            "agent_ref": self.agent_ref.to_dict() if self.agent_ref else None,
        }


@dataclass(frozen=True)
class RoleBinding:
    """把 ``responsible_user``、``selected-agent`` 等角色绑定到 Participant。"""

    role: str
    participant_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "RoleBinding":
        return cls(
            role=_required_str(data, "role", "RoleBinding"),
            participant_id=_required_str(data, "participant_id", "RoleBinding"),
        )

    def to_dict(self) -> dict:
        return {"role": self.role, "participant_id": self.participant_id}


def role_binding_ref(collet_id: str, binding: RoleBinding) -> str:
    """Stable local reference to the existing (Collet, role, participant) fact."""
    return "/".join(
        (
            quote(collet_id, safe=""),
            "role-bindings",
            quote(binding.role, safe=""),
            quote(binding.participant_id, safe=""),
        )
    )
