"""
WorkerProfileFilterAdapter Protocol

Worker Profile 过滤器 Adapter 接口定义。

Stage 1 Phase 4: Registry State → Candidate Filtering

职责：
- 根据 Registry 状态过滤可用的 WorkerProfile
- 支持 active + online 条件过滤

使用方式：
```python
filter_adapter = RegistryAwareWorkerFilter(
    registry_store=registry_store,
    runtime_state_store=runtime_state_store,
)

# 过滤 profile 列表
filtered = filter_adapter.filter_profiles(profiles)

# 获取允许的 profile_keys
allowed_keys = filter_adapter.get_allowed_profile_keys(all_keys)
```
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker_profile import WorkerProfile


@runtime_checkable
class WorkerProfileFilterAdapter(Protocol):
    """
    Worker Profile 过滤器 Adapter

    根据 Registry 状态过滤可用的 WorkerProfile。

    Stage 1 Phase 4 规则：
    - lifecycle_state 必须为 active
    - runtime_state 必须为 online
    - 未注册的 profile 采用兼容模式（放行）

    实现可以是：
    - RegistryAwareWorkerFilter: 基于 Worker Registry 的过滤实现
    - NoOpProfileFilter: 不过滤（用于测试或后向兼容）
    """

    def filter_profiles(
        self,
        profiles: list[WorkerProfile],
    ) -> list[WorkerProfile]:
        """
        过滤允许的 profiles

        Args:
            profiles: 待过滤的 WorkerProfile 列表

        Returns:
            过滤后的 WorkerProfile 列表
        """
        ...

    def get_allowed_profile_keys(
        self,
        all_profile_keys: Optional[list[str]] = None,
    ) -> set[str]:
        """
        获取允许的 profile_keys

        返回满足条件的 worker 对应的 profile_key：
        - lifecycle_state == active
        - runtime_state == online

        Args:
            all_profile_keys: 所有 profile_keys（用于兼容模式）

        Returns:
            允许的 profile_keys 集合
        """
        ...

    def is_profile_allowed(self, profile_key: str) -> bool:
        """
        检查 profile 是否被允许

        Args:
            profile_key: Profile 唯一标识

        Returns:
            是否被允许
        """
        ...


__all__ = ["WorkerProfileFilterAdapter"]