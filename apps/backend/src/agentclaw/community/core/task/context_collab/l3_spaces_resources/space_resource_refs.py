"""L3 Spaces and Resources 稳定引用值对象(Space / Resource)。

只保存外部空间/资源系统的引用,不复制 Membership、Grant 或资源正文;
``ResourceRef.revision`` 为可空字段(资源无版本要求时为 ``None``)。
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


def _optional_str(data: dict, key: str, ref: str) -> str | None:
    """从 dict 中取出可空字符串字段;缺失或 ``None`` 返回 ``None``。"""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{ref}.{key} must be a non-empty string or null, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class SpaceRef:
    """L3 Space 引用;授权计算由外部 Membership / Grant / Policy Authority 提供。"""

    space_id: str
    environment: str

    @classmethod
    def from_dict(cls, data: dict) -> "SpaceRef":
        return cls(
            space_id=_required_str(data, "space_id", "SpaceRef"),
            environment=_required_str(data, "environment", "SpaceRef"),
        )

    def to_dict(self) -> dict:
        return {"space_id": self.space_id, "environment": self.environment}


@dataclass(frozen=True)
class ResourceRef:
    """L3 外部资源引用;``revision`` 为资源版本引用,无版本要求时为 ``None``。"""

    resource_type: str
    resource_id: str
    revision: str | None

    @classmethod
    def from_dict(cls, data: dict) -> "ResourceRef":
        return cls(
            resource_type=_required_str(data, "resource_type", "ResourceRef"),
            resource_id=_required_str(data, "resource_id", "ResourceRef"),
            revision=_optional_str(data, "revision", "ResourceRef"),
        )

    def to_dict(self) -> dict:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "revision": self.revision,
        }
