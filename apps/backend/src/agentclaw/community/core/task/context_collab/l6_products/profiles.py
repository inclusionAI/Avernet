"""L6 Products:当前产品场景的 Profile 实例值。

``ProductProfile`` / ``WorkflowKind`` 是 Profile 的实例键值,
不新建 ProductKind/WorkflowKind 平行类型。版本化定义见 ``contracts`` /
``catalog``;枚举是兼容 wire ID,不是完整产品合同或注册授权。
"""

from __future__ import annotations

from enum import Enum


class ProductProfile(str, Enum):
    """L6 产品协议组合 Profile 实例值。"""

    TEAM_TODO = "team_todo"
    # Retained as the P1 wire-compatible generic BBS discussion value.
    PUBLIC_DISCUSSION = "public_discussion"
    BBS_PUBLIC_DISCUSSION = "bbs_public_discussion"
    BBS_HELP_REQUEST = "bbs_help_request"
    TASK_COMMISSION = "task_commission"


class WorkflowKind(str, Enum):
    """L6 WorkflowProfile 实例值;API 兼容字段名为 ``workflow_kind``。"""

    TEAM_TODO_EXECUTION = "team_todo_execution"
    FORUM_PUBLIC_DISCUSSION = "forum_public_discussion"
    BBS_PUBLIC_DISCUSSION = "bbs_public_discussion"
    BBS_HELP_REQUEST_GOAL = "bbs_help_request_goal"
    TASK_COMMISSION_GOAL = "task_commission_goal"
