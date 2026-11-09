"""
Worker Runtime State

Stage 1 Worker 运行态枚举。

Stage 1 只支持：
- online: 在线，可参与检索/推荐/融合
- offline: 离线，单独工作，不可见
"""

from enum import Enum


class WorkerRuntimeState(str, Enum):
    """
    Worker 运行态

    Stage 1 简化版，只支持两种状态：
    - online: 在线，可被检索、推荐、融合
    - offline: 离线，单独工作，不参与检索/推荐/融合

    状态规则：
    - online <-> offline: 手动切换或工作台回传

    约束规则：
    - lifecycle=inactive/disabled 时，runtime 必须为 offline
    - lifecycle=active 时，runtime 可以为 online 或 offline

    过滤规则（关键）：
    - retrieval 默认过滤 runtime != online
    - recommendation 默认过滤 runtime != online
    - matching 默认过滤 runtime != online
    """
    ONLINE = "online"
    OFFLINE = "offline"


__all__ = ["WorkerRuntimeState"]