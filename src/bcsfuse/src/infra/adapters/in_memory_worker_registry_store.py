"""
InMemory Worker Registry Store

Worker Registry 的内存存储实现。

Stage 1 Phase 1：用于测试和快速验证。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.domain.models.worker import Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.exceptions import (
    DuplicateWorkerException,
    WorkerNotFoundException,
)


class InMemoryWorkerRegistryStore:
    """
    Worker Registry 内存存储

    Stage 1 Phase 1 实现：
    - 纯内存存储（dict）
    - 无持久化
    - 非线程安全

    用于：
    - 测试
    - 快速验证业务逻辑
    - 契约测试
    """

    def __init__(self):
        """初始化空仓库"""
        self._workers: dict[str, Worker] = {}

    def create(self, worker: Worker) -> Worker:
        """
        创建 Worker

        Args:
            worker: 待创建的 Worker

        Returns:
            创建后的 Worker

        Raises:
            DuplicateWorkerException: Worker 已存在
        """
        if worker.id in self._workers:
            raise DuplicateWorkerException(worker.id)

        # 设置创建时间
        now = datetime.utcnow()
        worker.created_at = now
        worker.updated_at = now

        # 创建副本以避免外部修改影响存储
        self._workers[worker.id] = worker.model_copy(deep=True)
        return worker.model_copy(deep=True)

    def get_by_id(self, worker_id: str) -> Optional[Worker]:
        """
        根据 ID 获取 Worker

        Args:
            worker_id: Worker ID

        Returns:
            Worker 或 None
        """
        worker = self._workers.get(worker_id)
        if worker:
            return worker.model_copy(deep=True)
        return None

    def get_by_ids(self, worker_ids: list[str]) -> dict[str, Worker]:
        """根据 ID 列表批量获取 Worker"""
        result: dict[str, Worker] = {}
        for wid in worker_ids:
            worker = self._workers.get(wid)
            if worker:
                result[wid] = worker.model_copy(deep=True)
        return result

    def batch_get_configs(self, worker_ids: list[str]) -> tuple[dict[str, "WorkerConfig"], list[str]]:
        """批量获取 Worker config，仅返回配置，不返回完整 Worker"""
        from src.domain.models.worker_config import WorkerConfig
        configs: dict[str, "WorkerConfig"] = {}
        not_found: list[str] = []
        for wid in worker_ids:
            worker = self._workers.get(wid)
            if worker is None:
                not_found.append(wid)
            else:
                configs[wid] = worker.config.model_copy()
        return configs, not_found

    def list(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
        source_types: Optional[list[WorkerSourceType]] = None,
        domains: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[Worker]:
        """
        列出 Worker

        Stage 1 最小过滤支持：
        - lifecycle_states: AND 语义，如果提供则只返回匹配的
        - source_types: AND 语义，如果提供则只返回匹配的
        - domains: OR 语义，如果提供则返回任一匹配的

        Args:
            lifecycle_states: 过滤生命周期状态
            source_types: 过滤来源类型
            domains: 过滤领域（OR 语义）
            limit: 分页限制
            offset: 分页偏移

        Returns:
            Worker 列表
        """
        result = list(self._workers.values())

        # 按生命周期状态过滤
        if lifecycle_states is not None:
            result = [w for w in result if w.lifecycle_state in lifecycle_states]

        # 按来源类型过滤
        if source_types is not None:
            result = [w for w in result if w.source_type in source_types]

        # 按领域过滤（OR 语义）
        if domains is not None and len(domains) > 0:
            def has_any_domain(w: Worker, domain_list: list[str]) -> bool:
                worker_domains = set(w.domains)
                return any(d in worker_domains for d in domain_list)
            result = [w for w in result if has_any_domain(w, domains)]

        # 排序（按创建时间降序）
        result = sorted(result, key=lambda w: w.created_at, reverse=True)

        # 分页
        if offset is not None:
            result = result[offset:]
        if limit is not None:
            result = result[:limit]

        # 返回副本列表
        return [w.model_copy(deep=True) for w in result]

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
        """
        if worker.id not in self._workers:
            raise WorkerNotFoundException(worker.id)

        existing = self._workers[worker.id]

        # 乐观锁检查
        if worker.version != existing.version:
            raise ValueError(f"Version conflict: expected {existing.version}, got {worker.version}")

        # 创建副本，避免修改传入对象
        updated_worker = worker.model_copy(deep=True)

        # 更新时间和版本
        updated_worker.updated_at = datetime.utcnow()
        updated_worker.version = existing.version + 1

        # 存储
        self._workers[worker.id] = updated_worker.model_copy(deep=True)
        return updated_worker.model_copy(deep=True)

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
        """
        worker = self.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)

        # 乐观锁检查
        if worker.version != version:
            raise ValueError(f"Version conflict: expected {version}, got {worker.version}")

        # 更新状态
        worker.lifecycle_state = lifecycle_state
        worker.updated_at = datetime.utcnow()
        worker.version = version + 1

        # 存储
        self._workers[worker_id] = worker.model_copy(deep=True)
        return worker.model_copy(deep=True)

    def delete(self, worker_id: str) -> bool:
        """
        删除 Worker（硬删除）

        Args:
            worker_id: Worker ID

        Returns:
            是否删除成功

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        if worker_id not in self._workers:
            raise WorkerNotFoundException(worker_id)

        del self._workers[worker_id]
        return True

    def exists(self, worker_id: str) -> bool:
        """
        检查 Worker 是否存在

        Args:
            worker_id: Worker ID

        Returns:
            是否存在
        """
        return worker_id in self._workers

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
        if lifecycle_states is None:
            return len(self._workers)

        return sum(
            1 for w in self._workers.values()
            if w.lifecycle_state in lifecycle_states
        )

    def clear(self) -> None:
        """清空仓库（用于测试清理）"""
        self._workers.clear()


__all__ = ["InMemoryWorkerRegistryStore"]