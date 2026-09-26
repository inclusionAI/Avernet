"""
WorkerIndexSyncAdapter Protocol

Worker 索引同步 Adapter 接口定义。

Stage 1 实现：先做本地同步实现，后续可换异步队列。

职责：
- Worker 状态变化后，同步更新 metadata/vector index

Stage 1 规则：
- runtime_state 变化 → 更新 metadata filter 层，不重建 embedding
- profile 内容变化 → 更新 metadata + 必要时更新 vector index
- 先同步更新，不搞异步队列

为什么值得抽 adapter：
- 索引同步策略可能变化（同步→异步）
- 可能需要接入消息队列
- 测试时可以 mock
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.worker import Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState


@runtime_checkable
class WorkerIndexSyncAdapter(Protocol):
    """
    Index Sync Adapter

    职责：
    - Worker 状态变化后，同步更新索引

    Stage 1 实现：本地同步调用
    未来可替换：异步队列 / 事件总线（Kafka / RabbitMQ）
    """

    def on_worker_created(self, worker: Worker) -> None:
        """
        Worker 创建后同步

        Args:
            worker: 创建的 Worker
        """
        ...

    def on_worker_updated(self, worker: Worker) -> None:
        """
        Worker 更新后同步

        Args:
            worker: 更新后的 Worker
        """
        ...

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
        ...

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
        ...

    def on_worker_deleted(self, worker_id: str) -> None:
        """
        Worker 删除后同步

        Stage 1 实现：
        - 从 metadata index 和 vector index 中删除

        Args:
            worker_id: Worker ID
        """
        ...

    def rebuild_all_indexes(self) -> None:
        """
        重建所有索引

        Stage 1 实现：
        - 清空 metadata index 和 vector index
        - 重新加载所有 worker 并构建索引
        """
        ...


__all__ = ["WorkerIndexSyncAdapter"]