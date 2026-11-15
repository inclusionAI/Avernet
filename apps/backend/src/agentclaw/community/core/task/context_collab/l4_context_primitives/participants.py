"""L4 Participant / RoleBinding / scoped participation evidence.

参与者是 Principal 或 Agent 在 Collet 内的协作身份,作为 Collet JSON /
查询对象保存;RoleBinding 把产品角色绑定到 Participant,作为 Collet
JSON / 事件事实保存。两者都没有独立生命周期,不建表。
ScopedParticipantBinding 另存录取产生的限定 Collet 参与证据,不改写
创建时的角色事实,也不引入独立成员生命周期。
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


@dataclass(frozen=True)
class ScopedParticipantBinding:
    """Bind an Agent IdentityRef to one Collet, independently of Group.

    This local reference record is created by admission, not by designation.
    It is immutable evidence, not an independent membership state machine.
    Its authority must be checked against the admitting decision/application
    and ACTIVE Method binding; retaining it after revocation grants no access.
    No Space membership or resource grant is implied. Identity references use
    the same opaque string form as Open applications, without guessing Agent
    types or changing the creation-time Participant/RoleBinding facts.
    """

    participant_binding_id: str
    scope_ref: str
    participant_ref: str
    role_binding_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "ScopedParticipantBinding":
        fields = {
            "participant_binding_id", "scope_ref", "participant_ref",
            "role_binding_refs",
        }
        if not isinstance(data, dict) or set(data) - fields:
            raise ContextCollabValidationError("invalid ScopedParticipantBinding fields")
        roles = data.get("role_binding_refs", ())
        if not isinstance(roles, (list, tuple)) or any(
            not isinstance(ref, str) or not ref.strip() for ref in roles
        ):
            raise ContextCollabValidationError("invalid participant role references")
        return cls(
            participant_binding_id=_required_str(
                data, "participant_binding_id", "ScopedParticipantBinding"
            ),
            scope_ref=_required_str(data, "scope_ref", "ScopedParticipantBinding"),
            participant_ref=_required_str(
                data, "participant_ref", "ScopedParticipantBinding"
            ),
            role_binding_refs=tuple(roles),
        )

    def to_dict(self) -> dict:
        return {
            "participant_binding_id": self.participant_binding_id,
            "scope_ref": self.scope_ref,
            "participant_ref": self.participant_ref,
            "role_binding_refs": list(self.role_binding_refs),
        }
