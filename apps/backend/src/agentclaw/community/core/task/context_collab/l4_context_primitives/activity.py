"""L4 Activity 值对象(spec §3.4 表)。

一次真实发生的互动、执行尝试或进度活动;没有独立生命周期和独立表,
事实由 Event record 承载,本对象仅作为事件 payload / 查询投影。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


def _required_str(data: dict, key: str, owner: str) -> str:
    """从 dict 中取出必填字符串字段;缺失或空串抛校验错误。"""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class Activity:
    """互动、执行尝试或进度活动的语义投影。

    ``activity_kind`` 引用 G4 State Machine 的 event_name(例如
    ``task.progress.updated``);``status`` 是该活动事实在查询投影里的
    投影状态(例如 ``completed``),状态权威仍在 G4 State Machine。
    """

    activity_id: str
    activity_kind: str
    status: str

    @classmethod
    def from_dict(cls, data: dict) -> "Activity":
        return cls(
            activity_id=_required_str(data, "activity_id", "Activity"),
            activity_kind=_required_str(data, "activity_kind", "Activity"),
            status=_required_str(data, "status", "Activity"),
        )

    def to_dict(self) -> dict:
        return {
            "activity_id": self.activity_id,
            "activity_kind": self.activity_kind,
            "status": self.status,
        }
