"""L6 Products:当前两个产品场景的 Profile 实例值。

``ProductProfile`` / ``WorkflowKind`` 是 Profile 的实例键值,
不新建 ProductKind/WorkflowKind 平行类型,也不建 Profile 注册表。
"""

from __future__ import annotations

from enum import Enum


class ProductProfile(str, Enum):
    """L6 产品协议组合 Profile 实例值。"""

    TEAM_TODO = "team_todo"
    PUBLIC_DISCUSSION = "public_discussion"


class WorkflowKind(str, Enum):
    """L6 WorkflowProfile 实例值;API 兼容字段名为 ``workflow_kind``。"""

    TEAM_TODO_EXECUTION = "team_todo_execution"
    FORUM_PUBLIC_DISCUSSION = "forum_public_discussion"
