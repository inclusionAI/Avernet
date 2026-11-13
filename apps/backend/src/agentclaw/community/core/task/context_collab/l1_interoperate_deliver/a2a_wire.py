"""L1 A2A wire 投影对象(ACCS-101 §2/§6;线上语义属 A2A,ACCS 不重定义)。

边界铁律:Agent Card 可投影 L2 Agent/Capability 但不是其权威记录;
a2a.Message/Task/Artifacts 是线级载体,不等于 L4 对象、不取得权威状态。
P1 状态词汇为 mock 子集(SUBMITTED/WORKING/COMPLETED/FAILED/CANCELED),
P2 真实传输按所声明 A2A 版本对齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
    _dict_of,
    _enum_str,
    _list_of,
    _refs_tuple,
    _required_keys,
    _required_str,
)

_PART_KINDS = frozenset({"TEXT", "ENVELOPE", "REF"})
_G3_PROFILES = frozenset({
    "OBSERVABLE", "EVALUATABLE", "IMPROVABLE", "RECURSIVE_IMPROVEMENT",
})
_TASK_STATUSES = frozenset({
    "SUBMITTED", "WORKING", "COMPLETED", "FAILED", "CANCELED",
})


def _str_tuple(data: dict, key: str, owner: str) -> tuple[str, ...]:
    items = _list_of(data, key, owner)
    for item in items:
        if not isinstance(item, str):
            raise ContextCollabValidationError(
                f"{owner}.{key} entries must be strings, got: {item!r}"
            )
    return tuple(items)


@dataclass(frozen=True)
class Part:
    """A2A Part:承载 AccsOperationEnvelope、引用或内容片段。"""

    part_kind: str   # TEXT | ENVELOPE | REF
    body: dict

    @classmethod
    def from_dict(cls, data: dict) -> "Part":
        _required_keys(data, "Part", frozenset({"part_kind", "body"}))
        return cls(
            part_kind=_enum_str(data, "part_kind", _PART_KINDS, "Part"),
            body=dict(_dict_of(data, "body", "Part")),
        )

    def to_dict(self) -> dict:
        return {"part_kind": self.part_kind, "body": dict(self.body)}


@dataclass(frozen=True)
class A2aMessage:
    """线级交互内容载体;可携带上层 Operation,不等于 L4 Message。"""

    message_id: str
    role: str
    parts: tuple[Part, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "A2aMessage":
        _required_keys(data, "A2aMessage", frozenset(
            {"message_id", "role", "parts"}))
        return cls(
            message_id=_required_str(data, "message_id", "A2aMessage"),
            role=_required_str(data, "role", "A2aMessage"),
            parts=tuple(
                Part.from_dict(item)
                for item in _list_of(data, "parts", "A2aMessage")
            ),
        )

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "parts": [part.to_dict() for part in self.parts],
        }


@dataclass(frozen=True)
class A2aTask:
    """一次远程 Agent 调用的线级生命周期;是 attempt,不替代上层状态机。"""

    task_id: str
    status: str       # P1 mock 子集,五值闭包
    result: Any | None

    @classmethod
    def from_dict(cls, data: dict) -> "A2aTask":
        _required_keys(data, "A2aTask", frozenset(
            {"task_id", "status", "result"}))
        return cls(
            task_id=_required_str(data, "task_id", "A2aTask"),
            status=_enum_str(data, "status", _TASK_STATUSES, "A2aTask"),
            result=data.get("result"),
        )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "result": self.result,
        }


@dataclass(frozen=True)
class A2aArtifact:
    """远程调用的线级交付物;不等于 L3 Resource 或 L4 Artifact。"""

    artifact_id: str
    parts: tuple[Part, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "A2aArtifact":
        _required_keys(data, "A2aArtifact", frozenset(
            {"artifact_id", "parts"}))
        return cls(
            artifact_id=_required_str(data, "artifact_id", "A2aArtifact"),
            parts=tuple(
                Part.from_dict(item)
                for item in _list_of(data, "parts", "A2aArtifact")
            ),
        )

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "parts": [part.to_dict() for part in self.parts],
        }


@dataclass(frozen=True)
class AccsAgentCardExtension:
    """ACCS-101 §6:Agent Card 的 ACCS 扩展声明。

    Agent Card 的 skill/capability 最多投影 L2 ``DECLARED`` Capability;
    用于发现与匹配,不构成 Delegation、Grant 或 G1 授权。未知实现必须能
    安全忽略或拒绝本扩展(一致性 §10-2)。
    """

    accs_versions: tuple[str, ...]
    identity_ref: IdentityRef
    accountable_principal_refs: tuple[IdentityRef, ...]
    capability_profile_refs: tuple[ObjectRef, ...]
    supported_method_profiles: tuple[ObjectRef, ...]
    supported_product_profiles: tuple[ObjectRef, ...]
    supported_g3_profiles: tuple[str, ...]
    conformance_evidence_refs: tuple[ObjectRef, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "AccsAgentCardExtension":
        _required_keys(data, "AccsAgentCardExtension", frozenset({
            "accs_versions", "identity_ref", "accountable_principal_refs",
            "capability_profile_refs", "supported_method_profiles",
            "supported_product_profiles", "supported_g3_profiles",
            "conformance_evidence_refs",
        }))
        return cls(
            accs_versions=_str_tuple(
                data, "accs_versions", "AccsAgentCardExtension"
            ),
            identity_ref=IdentityRef.from_dict(
                _dict_of(data, "identity_ref", "AccsAgentCardExtension")
            ),
            accountable_principal_refs=tuple(
                IdentityRef.from_dict(item)
                for item in _list_of(
                    data, "accountable_principal_refs",
                    "AccsAgentCardExtension",
                )
            ),
            capability_profile_refs=_refs_tuple(
                data, "capability_profile_refs", "AccsAgentCardExtension"
            ),
            supported_method_profiles=_refs_tuple(
                data, "supported_method_profiles", "AccsAgentCardExtension"
            ),
            supported_product_profiles=_refs_tuple(
                data, "supported_product_profiles", "AccsAgentCardExtension"
            ),
            supported_g3_profiles=tuple(
                _enum_str({"g3": item}, "g3", _G3_PROFILES,
                          "AccsAgentCardExtension")
                for item in _list_of(
                    data, "supported_g3_profiles", "AccsAgentCardExtension"
                )
            ),
            conformance_evidence_refs=_refs_tuple(
                data, "conformance_evidence_refs", "AccsAgentCardExtension"
            ),
        )

    def to_dict(self) -> dict:
        return {
            "accs_versions": list(self.accs_versions),
            "identity_ref": self.identity_ref.to_dict(),
            "accountable_principal_refs": [
                ref.to_dict() for ref in self.accountable_principal_refs
            ],
            "capability_profile_refs": [
                ref.to_dict() for ref in self.capability_profile_refs
            ],
            "supported_method_profiles": [
                ref.to_dict() for ref in self.supported_method_profiles
            ],
            "supported_product_profiles": [
                ref.to_dict() for ref in self.supported_product_profiles
            ],
            "supported_g3_profiles": list(self.supported_g3_profiles),
            "conformance_evidence_refs": [
                ref.to_dict() for ref in self.conformance_evidence_refs
            ],
        }


@dataclass(frozen=True)
class AgentCard:
    """A2A 发现入口的线级投影(端点、技能、认证与扩展能力)。"""

    agent_id: str
    endpoint: str
    skills: tuple[str, ...]
    protocol_version: str
    accs_extension: AccsAgentCardExtension | None

    @classmethod
    def from_dict(cls, data: dict) -> "AgentCard":
        _required_keys(data, "AgentCard", frozenset({
            "agent_id", "endpoint", "skills", "protocol_version",
            "accs_extension",
        }))
        extension = data.get("accs_extension")
        return cls(
            agent_id=_required_str(data, "agent_id", "AgentCard"),
            endpoint=_required_str(data, "endpoint", "AgentCard"),
            skills=_str_tuple(data, "skills", "AgentCard"),
            protocol_version=_required_str(
                data, "protocol_version", "AgentCard"
            ),
            accs_extension=(
                AccsAgentCardExtension.from_dict(extension)
                if extension is not None
                else None
            ),
        )

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "endpoint": self.endpoint,
            "skills": list(self.skills),
            "protocol_version": self.protocol_version,
            "accs_extension": (
                self.accs_extension.to_dict()
                if self.accs_extension is not None
                else None
            ),
        }
