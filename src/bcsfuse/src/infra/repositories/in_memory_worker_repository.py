"""
InMemoryWorkerRepository

Worker 的内存存储实现。

M1: FAKE/PLACEHOLDER 实现，仅供测试和开发使用。

注意：
- 不用于生产环境
- 数据不会持久化
- 线程不安全

PLACEHOLDER: 这是一个内存实现，生产环境应替换为真实数据库实现。
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.worker import Worker
from src.domain.services.worker_repository import WorkerRepository
from src.domain.exceptions import (
    DuplicateWorkerException,
    WorkerNotFoundException,
)


class InMemoryWorkerRepository:
    """
    Worker 内存仓库实现

    PLACEHOLDER: 仅供测试和开发使用，不用于生产环境。

    Features:
    - 纯内存存储
    - 无持久化
    - 非线程安全
    - 支持基本 CRUD
    - 支持简单筛选
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

    def list(
        self,
        type: Optional[str] = None,
        domain: Optional[str] = None,
        availability: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
        skills: Optional[list[str]] = None,
        resources: Optional[list[str]] = None,
    ) -> list[Worker]:
        """
        列出 Worker

        筛选语义：
        - 不同筛选维度之间使用 AND 语义
        - 同一筛选维度内，如果传入多个值，使用 OR 语义
        - 未传该筛选条件时，不对该维度做过滤
        - 空列表不做过滤（不返回空结果）

        Args:
            type: 筛选类型 (human/bot)
            domain: 筛选领域
            availability: 筛选可用性
            capabilities: 筛选能力列表（OR 语义）
            skills: 筛选技能列表（OR 语义）
            resources: 筛选资源 ID 列表（OR 语义）

        Returns:
            Worker 列表
        """
        result = list(self._workers.values())

        # 按类型筛选
        if type is not None:
            result = [w for w in result if w.type == type]

        # 按领域筛选
        if domain is not None:
            result = [w for w in result if domain in w.domains]

        # 按可用性筛选
        if availability is not None:
            result = [w for w in result if w.state.availability == availability]

        # 按 capability 筛选（OR 语义）
        # 空列表不做过滤
        if capabilities is not None and len(capabilities) > 0:
            def has_any_capability(w: Worker, caps: list[str]) -> bool:
                worker_caps = {c.name for c in w.capabilities}
                return any(cap in worker_caps for cap in caps)
            result = [w for w in result if has_any_capability(w, capabilities)]

        # 按 skill 筛选（OR 语义）
        # 空列表不做过滤
        if skills is not None and len(skills) > 0:
            def has_any_skill(w: Worker, sks: list[str]) -> bool:
                worker_sks = {s.name for s in w.skills}
                return any(sk in worker_sks for sk in sks)
            result = [w for w in result if has_any_skill(w, skills)]

        # 按 resource 筛选（OR 语义）
        # 空列表不做过滤
        if resources is not None and len(resources) > 0:
            def has_any_resource(w: Worker, res_ids: list[str]) -> bool:
                worker_res_ids = {r.id for r in w.resources}
                return any(res_id in worker_res_ids for res_id in res_ids)
            result = [w for w in result if has_any_resource(w, resources)]

        # 返回副本列表
        return [w.model_copy(deep=True) for w in result]

    def update(self, worker: Worker) -> Worker:
        """
        更新 Worker

        Args:
            worker: 待更新的 Worker

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        if worker.id not in self._workers:
            raise WorkerNotFoundException(worker.id)

        self._workers[worker.id] = worker.model_copy(deep=True)
        return worker.model_copy(deep=True)

    def delete(self, worker_id: str) -> bool:
        """
        删除 Worker

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

    def clear(self) -> None:
        """
        清空仓库

        用于测试清理。
        """
        self._workers.clear()


__all__ = ["InMemoryWorkerRepository"]