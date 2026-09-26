"""
InMemory Worker Index Sync Adapter

Worker 索引同步的内存实现。

Stage 1 Phase 1：用于测试和快速验证。

Stage 1 实现：
- 本地同步（空实现/记录调用）
- 不做真实的索引同步
- 用于测试验证调用链路
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.worker import Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState


class InMemoryWorkerIndexSyncAdapter:
    """
    Worker Index Sync 内存实现

    Stage 1 Phase 1 实现：
    - 记录所有调用（用于测试验证）
    - 不做真实的索引同步

    用于：
    - 测试
    - 快速验证业务逻辑
    - 契约测试
    """

    def __init__(self):
        """初始化"""
        # 记录所有调用
        self.calls: list[dict] = []

    def on_worker_created(self, worker: Worker) -> None:
        """
        Worker 创建后同步

        Args:
            worker: 创建的 Worker
        """
        self.calls.append({
            "event": "worker_created",
            "worker_id": worker.id,
            "worker": worker.model_dump(),
        })

    def on_worker_updated(self, worker: Worker) -> None:
        """
        Worker 更新后同步

        Args:
            worker: 更新后的 Worker
        """
        self.calls.append({
            "event": "worker_updated",
            "worker_id": worker.id,
            "worker": worker.model_dump(),
        })

    def on_lifecycle_state_changed(
        self,
        worker_id: str,
        old_state: WorkerLifecycleState,
        new_state: WorkerLifecycleState,
    ) -> None:
        """
        生命周期状态变化后同步

        Stage 1 实现：
        - 更新 metadata index 的 lifecycle_state 字段
        - 不重建 embedding

        Args:
            worker_id: Worker ID
            old_state: 旧状态
            new_state: 新状态
        """
        self.calls.append({
            "event": "lifecycle_state_changed",
            "worker_id": worker_id,
            "old_state": old_state.value,
            "new_state": new_state.value,
        })

    def on_runtime_state_changed(
        self,
        worker_id: str,
        old_state: WorkerRuntimeState,
        new_state: WorkerRuntimeState,
    ) -> None:
        """
        运行态变化后同步

        Stage 1 实现：
        - 更新 metadata index 的 runtime_state 字段
        - 不重建 embedding

        Args:
            worker_id: Worker ID
            old_state: 旧状态
            new_state: 新状态
        """
        self.calls.append({
            "event": "runtime_state_changed",
            "worker_id": worker_id,
            "old_state": old_state.value,
            "new_state": new_state.value,
        })

    def on_worker_deleted(self, worker_id: str) -> None:
        """
        Worker 删除后同步

        Stage 1 实现：
        - 从 metadata index 和 vector index 中删除

        Args:
            worker_id: Worker ID
        """
        self.calls.append({
            "event": "worker_deleted",
            "worker_id": worker_id,
        })

    def rebuild_all_indexes(self) -> None:
        """
        重建所有索引

        Stage 1 实现：
        - 清空 metadata index 和 vector index
        - 重新加载所有 worker 并构建索引
        """
        self.calls.append({
            "event": "rebuild_all_indexes",
        })

    def clear(self) -> None:
        """清空调用记录（用于测试清理）"""
        self.calls.clear()

    def get_calls_by_event(self, event: str) -> list[dict]:
        """
        获取特定事件的所有调用

        Args:
            event: 事件名称

        Returns:
            调用列表
        """
        return [call for call in self.calls if call.get("event") == event]

    def has_event(self, event: str) -> bool:
        """
        检查是否有特定事件

        Args:
            event: 事件名称

        Returns:
            是否存在
        """
        return any(call.get("event") == event for call in self.calls)


__all__ = ["InMemoryWorkerIndexSyncAdapter"]