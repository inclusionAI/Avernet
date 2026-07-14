"""
WorkerRegistryStoreAdapter Protocol

Worker Registry 存储 Adapter 接口定义。

Stage 1 实现：先做 InMemory，后续替换 SQLite / PostgreSQL / 分布式数据库。

职责：
- Worker 实体的 CRUD
- 生命周期状态查询/更新
- 来源追踪

为什么值得抽 adapter：
- 存储层一定会从 SQLite → PostgreSQL → 分布式数据库
- 测试时需要 InMemory 实现
- 接口定义即契约，倒逼设计思考清楚
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker import TrustLevel, Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.models.worker_config import WorkerConfig


@runtime_checkable
class WorkerRegistryStoreAdapter(Protocol):
    """
    Worker Registry 存储 Adapter

    职责：
    - Worker CRUD
    - 生命周期状态管理
    - 来源追踪

    Stage 1 实现：InMemory（Phase 1）→ SQLite（Phase 2）
    未来可替换：PostgreSQL / 分布式数据库
    """

    def create(self, worker: Worker) -> Worker:
        """
        创建 Worker

        Args:
            worker: 待创建的 Worker

        Returns:
            创建后的 Worker（含数据库生成的字段）

        Raises:
            DuplicateWorkerException: ID 已存在
        """
        ...

    def get_by_id(self, worker_id: str) -> Optional[Worker]:
        """
        根据 ID 获取 Worker

        Args:
            worker_id: Worker ID

        Returns:
            Worker 或 None
        """
        ...

    def list(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
        source_types: Optional[list[WorkerSourceType]] = None,
        domains: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[Worker]:
        """
        列出 Worker（支持过滤）

        Stage 1 最小过滤支持：
        - lifecycle_states: 过滤生命周期状态
        - source_types: 过滤来源类型
        - domains: 过滤领域（OR 语义）
        - limit/offset: 分页

        Args:
            lifecycle_states: 过滤生命周期状态
            source_types: 过滤来源类型
            domains: 过滤领域（OR 语义）
            limit: 分页限制
            offset: 分页偏移

        Returns:
            Worker 列表
        """
        ...

    def update(self, worker: Worker) -> Worker:
        """
        更新 Worker

        使用乐观锁（version 字段）

        Args:
            worker: 待更新的 Worker

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
            OptimisticLockException: 版本冲突
        """
        ...

    def update_lifecycle_state(
        self,
        worker_id: str,
        lifecycle_state: WorkerLifecycleState,
        version: int,
    ) -> Worker:
        """
        更新生命周期状态

        Args:
            worker_id: Worker ID
            lifecycle_state: 新状态
            version: 当前版本（乐观锁）

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
            OptimisticLockException: 版本冲突
        """
        ...

    def delete(self, worker_id: str) -> bool:
        """
        删除 Worker（硬删除）

        注意：Stage 1 简化为硬删除，未来可改为软删除

        Args:
            worker_id: Worker ID

        Returns:
            是否删除成功

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        ...

    def exists(self, worker_id: str) -> bool:
        """
        检查 Worker 是否存在

        Args:
            worker_id: Worker ID

        Returns:
            是否存在
        """
        ...

    def count(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
    ) -> int:
        """
        统计 Worker 数量

        Args:
            lifecycle_states: 过滤生命周期状态

        Returns:
            Worker 数量
        """
        ...

    def update_trust_level(
        self,
        worker_id: str,
        trust_level: TrustLevel,
    ) -> Worker:
        """
        更新 Worker 信任级别

        Args:
            worker_id: Worker ID
            trust_level: 新信任级别

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        ...

    def get_by_ids(self, worker_ids: list[str]) -> dict[str, Worker]:
        """
        根据 ID 列表批量获取 Worker

        Args:
            worker_ids: Worker ID 列表

        Returns:
            dict: {worker_id: Worker}，未找到的 ID 不包含在结果中
        """
        ...

    def batch_get_configs(self, worker_ids: list[str]) -> tuple[dict[str, "WorkerConfig"], list[str]]:
        """
        批量获取 Worker 的 config 配置（轻量查询，仅查 id + config 列）

        Args:
            worker_ids: Worker ID 列表

        Returns:
            (configs, not_found_ids):
                configs: {worker_id: WorkerConfig}
                not_found_ids: 不存在的 worker_id 列表
        """
        ...


__all__ = ["WorkerRegistryStoreAdapter"]