"""L4 Artifact / ResourceBinding 值对象(spec §3.4 表 / §3.3 L3 边界)。

ResourceBinding 把 L3 Resource 带入 Collet 并保留授权快照引用,写入
``context_spec``;Artifact 是代码、文档、PR 等输入、证据、草稿或交付物
的协作投影,只保存外部引用,不复制资源正文。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
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


def _required_resource_binding(data: dict, owner: str) -> "ResourceBinding":
    value = data.get("resource_binding")
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.resource_binding must be an object, got: {value!r}"
        )
    return ResourceBinding.from_dict(value)


@dataclass(frozen=True)
class ResourceBinding:
    """把 L3 Resource 带入 Collet 的绑定值对象。

    ``grant_ref`` 引用 L3 Grant 的授权快照,可为 ``None``,表示绑定时不
    带授权引用;授权权威仍在 G1 / L3,不复制授权数据。
    """

    resource_ref: ResourceRef
    grant_ref: str | None

    @classmethod
    def from_dict(cls, data: dict) -> "ResourceBinding":
        resource_ref = data.get("resource_ref")
        if not isinstance(resource_ref, dict):
            raise ContextCollabValidationError(
                f"ResourceBinding.resource_ref must be an object, got: {resource_ref!r}"
            )
        return cls(
            resource_ref=ResourceRef.from_dict(resource_ref),
            grant_ref=_optional_str(data, "grant_ref", "ResourceBinding"),
        )

    def to_dict(self) -> dict:
        return {
            "resource_ref": self.resource_ref.to_dict(),
            "grant_ref": self.grant_ref,
        }


@dataclass(frozen=True)
class Artifact:
    """交付物 / 证据 / 草稿的协作投影;只保存 ResourceBinding 外部引用。"""

    artifact_id: str
    resource_binding: ResourceBinding

    @classmethod
    def from_dict(cls, data: dict) -> "Artifact":
        return cls(
            artifact_id=_required_str(data, "artifact_id", "Artifact"),
            resource_binding=_required_resource_binding(data, "Artifact"),
        )

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "resource_binding": self.resource_binding.to_dict(),
        }
