"""L1 桥接记录(ACCS-101 §2/§5:上下层对象、ID 与状态分离的唯一证据)。

每次上层操作 → A2A 载体的映射产出一条 ``A2ABridgeRecord``;桥接可验证
是一致性硬条款(§10-5)。禁止把 A2A Task ID 复用为协作/方法内部 ID(§7)。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    ObjectRef,
    _enum_str,
    _optional_str,
    _required_keys,
    _required_str,
)

_A2A_OBJECT_TYPES = frozenset({"MESSAGE", "TASK", "ARTIFACT"})
_DIRECTIONS = frozenset({"INBOUND", "OUTBOUND"})


@dataclass(frozen=True)
class A2ABridgeRecord:
    """上层对象与 A2A 线级对象的显式桥;``content_digest`` 供来源验证。"""

    bridge_id: str
    accs_object_ref: ObjectRef
    a2a_object_type: str     # MESSAGE | TASK | ARTIFACT
    a2a_object_id: str
    direction: str           # INBOUND | OUTBOUND
    transformation_profile_ref: ObjectRef | None
    content_digest: str | None
    created_at: str          # ISO 字符串(wire 侧时间戳,裁决 E)

    @classmethod
    def from_dict(cls, data: dict) -> "A2ABridgeRecord":
        _required_keys(data, "A2ABridgeRecord", frozenset({
            "bridge_id", "accs_object_ref", "a2a_object_type",
            "a2a_object_id", "direction", "transformation_profile_ref",
            "content_digest", "created_at",
        }))
        transformation = data.get("transformation_profile_ref")
        return cls(
            bridge_id=_required_str(data, "bridge_id", "A2ABridgeRecord"),
            accs_object_ref=ObjectRef.from_dict(
                data["accs_object_ref"]
            ),
            a2a_object_type=_enum_str(
                data, "a2a_object_type", _A2A_OBJECT_TYPES,
                "A2ABridgeRecord",
            ),
            a2a_object_id=_required_str(
                data, "a2a_object_id", "A2ABridgeRecord"
            ),
            direction=_enum_str(
                data, "direction", _DIRECTIONS, "A2ABridgeRecord"
            ),
            transformation_profile_ref=(
                ObjectRef.from_dict(transformation)
                if transformation is not None
                else None
            ),
            content_digest=_optional_str(
                data, "content_digest", "A2ABridgeRecord"
            ),
            created_at=_required_str(data, "created_at", "A2ABridgeRecord"),
        )

    def to_dict(self) -> dict:
        return {
            "bridge_id": self.bridge_id,
            "accs_object_ref": self.accs_object_ref.to_dict(),
            "a2a_object_type": self.a2a_object_type,
            "a2a_object_id": self.a2a_object_id,
            "direction": self.direction,
            "transformation_profile_ref": (
                self.transformation_profile_ref.to_dict()
                if self.transformation_profile_ref is not None
                else None
            ),
            "content_digest": self.content_digest,
            "created_at": self.created_at,
        }
