"""Composition input and immutable creation metadata, NOT an L4 primitive.

Method/product fields describe the initial selection. Runtime Method authority
belongs to MethodBinding, product selection to ProductInstance, and work state
to L4 Task. This DTO is retained for creation compatibility and role references;
its objective/executor fields must not adjudicate an existing Task's state.
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
    RoleBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
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


def _optional_version(data: dict, key: str) -> int | None:
    value = data.get(key)
    if value is not None and (type(value) is not int or value < 1):
        raise ContextCollabValidationError(
            f"ContextSpec.{key} must be a positive integer or null"
        )
    return value


def _object_list(data: dict, key: str) -> list[dict]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContextCollabValidationError(
            f"ContextSpec.{key} must be a list of objects"
        )
    return value


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
    # None denotes a pre-versioning row (or a context without L6), not "latest".
    product_profile_version: int | None = None
    workflow_profile_version: int | None = None
    participants: tuple[Participant, ...] = ()
    role_bindings: tuple[RoleBinding, ...] = ()

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
            resource_refs=tuple(ResourceRef.from_dict(item) for item in raw_refs),
            product_kind=_optional_str(data, "product_kind", "ContextSpec"),
            workflow_kind=_optional_str(data, "workflow_kind", "ContextSpec"),
            product_profile_version=_optional_version(data, "product_profile_version"),
            workflow_profile_version=_optional_version(
                data, "workflow_profile_version"
            ),
            participants=tuple(
                Participant.from_dict(item)
                for item in _object_list(data, "participants")
            ),
            role_bindings=tuple(
                RoleBinding.from_dict(item)
                for item in _object_list(data, "role_bindings")
            ),
        )

    def to_dict(self) -> dict:
        data = {
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
        # Preserve the original JSON shape of legacy/unprofiled contexts.
        if (
            self.product_profile_version is not None
            or self.workflow_profile_version is not None
            or self.participants
            or self.role_bindings
        ):
            data.update(
                product_profile_version=self.product_profile_version,
                workflow_profile_version=self.workflow_profile_version,
                participants=[item.to_dict() for item in self.participants],
                role_bindings=[item.to_dict() for item in self.role_bindings],
            )
        return data
