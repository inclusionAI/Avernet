"""
Registry Aware Worker Filter

Stage 1 Phase 4: Registry State → Candidate Filtering

根据 Worker Registry 的 lifecycle/runtime 状态过滤可用候选。

核心规则：
- lifecycle_state == active
- 过滤条件（与关系）：runtime_state 为 offline/null 且 availability 为 private/null
- 未注册的 profile 采用兼容模式，暂时放行

Feature Flags:
- ENABLE_REGISTRY_AWARE_FILTERING: 控制是否启用基于 Registry 的过滤

实现了 WorkerProfileFilterAdapter 协议。

使用方式：
```python
filter_service = RegistryAwareWorkerFilter(
    registry_store=registry_store,
    runtime_state_store=runtime_state_store,
)

# 获取允许的 profile_keys
allowed_keys = filter_service.get_allowed_profile_keys(all_profile_keys)

# 过滤 profile 列表
filtered_profiles = filter_service.filter_profiles(profiles)
```
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker import Availability
from src.infra.config.feature_flags import FeatureFlags
from src.infra.observability.fallback_logger import get_fallback_logger, get_fallback_metrics

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_registry_store_adapter import WorkerRegistryStoreAdapter
    from src.domain.services.adapters.worker_runtime_state_store_adapter import WorkerRuntimeStateStoreAdapter
    from src.domain.models.worker import Worker
    from src.domain.models.worker_profile import WorkerProfile


logger = logging.getLogger(__name__)


class RegistryAwareWorkerFilter:
    """
    Registry 感知的 Worker 过滤器

    根据 Worker Registry 的状态过滤候选 Worker/Profile。

    Stage 1 Phase 4 规则：
    - lifecycle_state 必须为 active
    - 同时满足以下条件才过滤：
      - runtime_state 为 offline 或 null
      - availability 为 private 或 null
    - 未注册的 profile 采用兼容模式

    设计决策：
    1. worker_id 与 profile_key 的映射：
       - Worker.active_profile_key 字段存储 profile_key
       - 格式为 "staff_id:profile_id"

    2. 未注册 profile 策略：
       - 兼容模式：未注册的 profile 暂时放行
       - 原因：不破坏现有 snapshot 驱动链路
       - 后续可收紧为 registry-first
    """

    def __init__(
        self,
        registry_store: "WorkerRegistryStoreAdapter",
        runtime_state_store: "WorkerRuntimeStateStoreAdapter",
        strict_mode: bool = False,
    ):
        """
        初始化过滤器

        Args:
            registry_store: Worker Registry Store
            runtime_state_store: Worker Runtime State Store
            strict_mode: 是否启用严格模式
                - False（默认）：未注册的 profile 放行（兼容模式）
                - True：未注册的 profile 过滤掉（严格模式）
        """
        self._registry_store = registry_store
        self._runtime_state_store = runtime_state_store
        self._strict_mode = strict_mode

        # 缓存允许的 profile_keys
        self._allowed_profile_keys_cache: Optional[set[str]] = None

    def get_allowed_profile_keys(
        self,
        all_profile_keys: Optional[list[str]] = None,
    ) -> set[str]:
        """
        获取允许的 profile_keys

        返回满足以下条件的 worker 对应的 profile_key：
        - lifecycle_state == active
        - 不同时满足：runtime_state 为 offline/null 且 availability 为 private/null

        Feature Flag:
        - 如果 ENABLE_REGISTRY_AWARE_FILTERING 为 False，返回所有 keys（不过滤）

        对于未注册的 profile:
        - 兼容模式（strict_mode=False）：加入结果集
        - 严格模式（strict_mode=True）：不加入结果集

        Args:
            all_profile_keys: 所有 profile_keys（用于兼容模式）

        Returns:
            允许的 profile_keys 集合
        """
        logger.info("[Filter] ========== 开始过滤 profile_keys ==========")
        logger.info("[Filter] all_profile_keys 数量: %s", len(all_profile_keys) if all_profile_keys else 0)
        logger.info("[Filter] strict_mode: %s", self._strict_mode)

        # Feature Flag 检查：如果未启用，返回所有 keys
        if not FeatureFlags.is_registry_aware_filtering_enabled():
            fallback_logger = get_fallback_logger()
            fallback_metrics = get_fallback_metrics()

            fallback_logger.log_fallback(
                fallback_type="registry_filter_disabled",
                reason="ENABLE_REGISTRY_AWARE_FILTERING is False",
                affected_component="registry_aware_worker_filter",
            )
            fallback_metrics.increment("registry_filter_fallback_count")
            logger.warning("[Filter] Registry-aware filtering 未启用，返回所有 profiles")
            if all_profile_keys:
                return set(all_profile_keys)
            return set()

        # 获取所有 active workers
        logger.info("[Filter] 查询所有 active workers...")
        active_workers = self._registry_store.list(
            lifecycle_states=[WorkerLifecycleState.ACTIVE]
        )
        logger.info("[Filter] 查询到 %d 个 active workers", len(active_workers))

        if not active_workers:
            # 没有 active workers
            logger.warning("[Filter] 没有 active workers")
            if self._strict_mode:
                logger.info("[Filter] 严格模式，返回空集")
                return set()
            else:
                logger.info("[Filter] 兼容模式，返回所有 profile_keys")
                return set(all_profile_keys or [])

        # 获取所有 active workers 的运行态
        worker_ids = [w.id for w in active_workers]
        logger.info("[Filter] 批量查询运行状态，worker_ids: %s", worker_ids[:5])  # 只打印前5个
        runtime_states = self._runtime_state_store.batch_get_runtime_states(worker_ids)
        logger.info("[Filter] 运行状态查询完成，结果数: %d", len(runtime_states))

        # 筛选可用 workers（runtime_state=offline/null 且 availability=private/null 才过滤）
        allowed_profile_keys: set[str] = set()
        online_count = 0
        offline_count = 0
        private_count = 0
        both_filtered = 0
        for worker in active_workers:
            runtime_state = runtime_states.get(worker.id)
            availability = worker.state.availability

            # 检查 runtime_state（offline 或 null）
            is_offline = runtime_state != WorkerRuntimeState.ONLINE

            # 检查 availability（private 或 null）
            is_private = availability is None or availability == Availability.PRIVATE

            # 与的关系：两个条件同时满足才过滤
            if is_offline and is_private:
                both_filtered += 1
                logger.debug("[Filter] Worker %s runtime_state=%s, availability=%s, 同时满足过滤条件",
                             worker.id,
                             runtime_state.value if runtime_state else "None",
                             availability if availability else "None")
                continue

            # 分别统计
            if is_offline:
                offline_count += 1
            if is_private:
                private_count += 1

            # 保留 worker（只满足一个条件或都不满足）
            online_count += 1
            profile_key = self._get_profile_key_from_worker(worker)
            if profile_key:
                allowed_profile_keys.add(profile_key)
                logger.debug("[Filter] Worker %s runtime_state=%s, availability=%s, 保留, profile_key=%s",
                             worker.id,
                             runtime_state.value if runtime_state else "None",
                             availability if availability else "None",
                             profile_key)

        logger.info("[Filter] 状态统计: 保留=%d, 同时满足过滤条件=%d, 仅offline=%d, 仅private=%d",
                    online_count, both_filtered, offline_count, private_count)
        logger.info("[Filter] 从 online workers 获取到 %d 个 profile_keys", len(allowed_profile_keys))

        # 兼容模式：将未注册的 profile_keys 也加入结果
        if not self._strict_mode and all_profile_keys:
            # 获取已注册的 profile_keys
            registered_keys = self._get_registered_profile_keys()
            logger.info("[Filter] 已注册 profile_keys 数量: %d", len(registered_keys))

            # 未注册的 profile_keys
            unregistered_keys = set(all_profile_keys) - registered_keys
            if unregistered_keys:
                logger.info("[Filter] 兼容模式，添加 %d 个未注册 profile_keys", len(unregistered_keys))
                allowed_profile_keys.update(unregistered_keys)

        logger.info("[Filter] 最终允许的 profile_keys 数量: %d", len(allowed_profile_keys))
        return allowed_profile_keys

    def filter_profiles(
        self,
        profiles: list["WorkerProfile"],
    ) -> list["WorkerProfile"]:
        """
        过滤允许的 profiles

        实现了 WorkerProfileFilterAdapter 协议。

        Args:
            profiles: 待过滤的 WorkerProfile 列表

        Returns:
            过滤后的 WorkerProfile 列表
        """
        all_keys = [p.profile_key for p in profiles]
        allowed_keys = self.get_allowed_profile_keys(all_keys)

        return [p for p in profiles if p.profile_key in allowed_keys]

    # Backward compatibility alias
    filter_allowed_profiles = filter_profiles

    def is_profile_allowed(self, profile_key: str) -> bool:
        """
        检查 profile 是否被允许

        Args:
            profile_key: Profile 唯一标识

        Returns:
            是否被允许
        """
        allowed_keys = self.get_allowed_profile_keys([profile_key])
        return profile_key in allowed_keys

    def get_filter_stats(self, all_profile_keys: list[str]) -> dict:
        """
        获取过滤统计信息

        用于调试和监控。

        Args:
            all_profile_keys: 所有 profile_keys

        Returns:
            统计信息字典
        """
        allowed_keys = self.get_allowed_profile_keys(all_profile_keys)

        # 获取 registry 统计
        total_workers = self._registry_store.count()
        active_workers = self._registry_store.count(
            lifecycle_states=[WorkerLifecycleState.ACTIVE]
        )

        # 获取 runtime_state 统计
        online_count = self._runtime_state_store.count_by_state(WorkerRuntimeState.ONLINE)
        offline_count = self._runtime_state_store.count_by_state(WorkerRuntimeState.OFFLINE)

        return {
            "total_profile_keys": len(all_profile_keys),
            "allowed_profile_keys": len(allowed_keys),
            "filtered_out": len(all_profile_keys) - len(allowed_keys),
            "registry_total_workers": total_workers,
            "registry_active_workers": active_workers,
            "runtime_online_count": online_count,
            "runtime_offline_count": offline_count,
            "strict_mode": self._strict_mode,
        }

    def clear_cache(self) -> None:
        """清除缓存（用于状态变更后）"""
        self._allowed_profile_keys_cache = None

    def _get_profile_key_from_worker(self, worker: "Worker") -> Optional[str]:
        """
        从 Worker 获取 profile_key

        优先使用 active_profile_key 字段。

        Args:
            worker: Worker 对象

        Returns:
            profile_key 或 None
        """
        # 优先使用 active_profile_key
        if worker.active_profile_key:
            return worker.active_profile_key

        # 如果没有 active_profile_key，尝试从 external_id 或其他字段构造
        # 这是 fallback 逻辑
        return None

    def _get_registered_profile_keys(self) -> set[str]:
        """
        获取已注册的 profile_keys

        Returns:
            已注册的 profile_keys 集合
        """
        all_workers = self._registry_store.list()
        keys = set()

        for worker in all_workers:
            profile_key = self._get_profile_key_from_worker(worker)
            if profile_key:
                keys.add(profile_key)

        return keys


__all__ = ["RegistryAwareWorkerFilter"]