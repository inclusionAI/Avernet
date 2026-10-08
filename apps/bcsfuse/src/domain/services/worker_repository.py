"""
WorkerRepository Interface

Worker 仓库接口定义。

M1: 定义 Worker 持久化的抽象接口。

职责：
- 定义 Worker 存储的基本操作
- 不依赖具体存储实现
- 供 application 层调用，由 infra 层实现
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker import Worker


@runtime_checkable
class WorkerRepository(Protocol):
    """
    Worker Repository 接口

    定义 Worker 持久化的基本操作。

    使用 Protocol 而非 ABC，允许 duck typing，
    但仍能通过 isinstance 检查。
    """

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
        ...

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
        ...

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


__all__ = ["WorkerRepository"]