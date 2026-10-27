"""
Worker Lifecycle State

Stage 1 Worker 生命周期状态枚举。

Stage 1 只支持：
- active: 活跃，可参与协作
- inactive: 休眠，不参与协作
- disabled: 禁用，管理操作
"""

from enum import Enum


class WorkerLifecycleState(str, Enum):
    """
    Worker 生命周期状态

    Stage 1 简化版，只支持三种状态：
    - active: 活跃状态，可以参与协作任务
    - inactive: 休眠状态，暂时不参与协作
    - disabled: 禁用状态，管理操作

    状态规则：
    - active -> inactive: 休眠操作
    - inactive -> active: 唤醒操作
    - active -> disabled: 禁用操作
    - disabled -> active: 启用操作

    过滤规则：
    - retrieval 默认只返回 lifecycle=active 的 worker
    - recommendation 默认只推荐 lifecycle=active 的 worker
    """
    ACTIVE = "active"
    INACTIVE = "inactive"
    DISABLED = "disabled"


__all__ = ["WorkerLifecycleState"]