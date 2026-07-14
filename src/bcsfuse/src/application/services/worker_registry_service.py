"""
WorkerRegistryService

Worker Registry 应用服务。

M1: 编排 Worker 的 CRUD 操作。

职责：
- 接收 DTO 数据
- 调用 Repository 进行持久化
- 处理领域异常
- 返回领域对象
"""

from __future__ import annotations

from typing import Any, Optional

from src.domain.models.worker import Worker, WorkerIdentity
from src.domain.services.worker_repository import WorkerRepository
from src.domain.exceptions import (
    WorkerNotFoundException,
    DuplicateWorkerException,
)


class WorkerRegistryService:
    """
    Worker Registry 应用服务

    提供 Worker 的创建、查询、更新、删除操作。
    """

    def __init__(self, repository: WorkerRepository):
        """
        初始化服务

        Args:
            repository: Worker 仓库实现
        """
        self._repository = repository

    def create_worker(self, worker_data: dict[str, Any]) -> Worker:
        """
        创建 Worker

        Args:
            worker_data: Worker 数据（字典格式）

        Returns:
            创建的 Worker

        Raises:
            DuplicateWorkerException: Worker 已存在
            ValidationError: 数据校验失败
        """
        # 使用 Pydantic 模型进行校验和创建
        worker = Worker.model_validate(worker_data)

        # 调用仓库创建
        return self._repository.create(worker)

    def get_worker(self, worker_id: str) -> Optional[Worker]:
        """
        获取 Worker

        Args:
            worker_id: Worker ID

        Returns:
            Worker 或 None
        """
        return self._repository.get_by_id(worker_id)

    def list_workers(
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
        - 空列表不做过滤

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
        return self._repository.list(
            type=type,
            domain=domain,
            availability=availability,
            capabilities=capabilities,
            skills=skills,
            resources=resources,
        )

    def update_worker(self, worker_id: str, update_data: dict[str, Any]) -> Worker:
        """
        更新 Worker

        Args:
            worker_id: Worker ID
            update_data: 更新数据

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        # 获取现有 Worker
        existing = self._repository.get_by_id(worker_id)
        if existing is None:
            raise WorkerNotFoundException(worker_id)

        # 合并更新数据
        existing_dict = existing.model_dump(exclude_none=True)

        # 处理嵌套更新（如 identity）
        for key, value in update_data.items():
            if key == "identity" and isinstance(value, dict):
                # 特殊处理 identity
                existing_identity = existing_dict.get("identity", {})
                existing_identity.update(value)
                existing_dict["identity"] = existing_identity
            else:
                existing_dict[key] = value

        # 重新校验并创建更新后的 Worker
        updated_worker = Worker.model_validate(existing_dict)

        return self._repository.update(updated_worker)

    def delete_worker(self, worker_id: str) -> bool:
        """
        删除 Worker

        Args:
            worker_id: Worker ID

        Returns:
            是否删除成功

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        return self._repository.delete(worker_id)


__all__ = ["WorkerRegistryService"]