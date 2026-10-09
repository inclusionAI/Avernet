"""
WorkerRuntimeStateStoreAdapter Protocol

Worker 运行态存储 Adapter 接口定义。

Stage 1 实现：先做 InMemory，后续替换 SQLite / Redis。

职责：
- online/offline 状态管理
- 高频状态更新

为什么值得单独抽 adapter：
- runtime_state 是高频更新的字段
- 未来可能需要独立的缓存层（Redis）
- 与 registry 存储可能不同（内存 vs 磁盘）
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker_runtime_state import WorkerRuntimeState


@runtime_checkable
class WorkerRuntimeStateStoreAdapter(Protocol):
    """
    Worker Runtime State Store Adapter

    职责：
    - online/offline 状态管理
    - 高频状态更新

    Stage 1 实现：InMemory（Phase 1）→ SQLite（Phase 2）
    未来可替换：Redis / Memcached
    """

    def get_runtime_state(self, worker_id: str) -> Optional[WorkerRuntimeState]:
        """
        获取运行态

        Args:
            worker_id: Worker ID

        Returns:
            WorkerRuntimeState 或 None
        """
        ...

    def set_runtime_state(
        self,
        worker_id: str,
        runtime_state: WorkerRuntimeState,
        updated_by: Optional[str] = None,
    ) -> bool:
        """
        设置运行态

        Args:
            worker_id: Worker ID
            runtime_state: 新状态
            updated_by: 更新来源

        Returns:
            是否更新成功
        """
        ...

    def batch_get_runtime_states(
        self,
        worker_ids: list[str]
    ) -> dict[str, WorkerRuntimeState]:
        """
        批量获取运行态

        Args:
            worker_ids: Worker ID 列表

        Returns:
            dict[worker_id, WorkerRuntimeState]
        """
        ...

    def count_by_state(self, runtime_state: WorkerRuntimeState) -> int:
        """
        按状态统计数量

        Args:
            runtime_state: 运行态

        Returns:
            数量
        """
        ...


__all__ = ["WorkerRuntimeStateStoreAdapter"]