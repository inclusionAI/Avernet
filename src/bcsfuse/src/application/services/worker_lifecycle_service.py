"""
Worker Lifecycle Service

Worker 生命周期管理服务。

职责：
- 激活 Worker（ACTIVE）
- 休眠 Worker（INACTIVE）
- 禁用 Worker（DISABLED）

注意：API 端点后续单独迭代，本服务仅提供内部调用入口。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.domain.models.worker import Worker
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.exceptions import WorkerNotFoundException

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_registry_store_adapter import (
        WorkerRegistryStoreAdapter,
    )

logger = logging.getLogger(__name__)


class WorkerLifecycleService:
    """
    Worker 生命周期服务

    提供 Worker 生命周期状态变更的内部调用入口。
    API 端点（/activate, /deactivate, /disable）后续单独迭代。

    使用场景：
    - 管理员操作：激活/休眠/禁用 Worker
    - 系统自动化：基于规则自动激活/休眠
    """

    def __init__(self, registry_store: "WorkerRegistryStoreAdapter"):
        """
        初始化服务

        Args:
            registry_store: Worker Registry Store
        """
        self._store = registry_store

    def activate(self, worker_id: str, version: int, updated_by: str = None) -> Worker:
        """
        激活 Worker

        将 Worker 状态设置为 ACTIVE，允许 set_online。

        Args:
            worker_id: Worker ID
            version: 当前版本（乐观锁）
            updated_by: 更新来源

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
            ValueError: 版本冲突
        """
        logger.info(f"Activating worker {worker_id} by {updated_by}")

        worker = self._store.update_lifecycle_state(
            worker_id=worker_id,
            lifecycle_state=WorkerLifecycleState.ACTIVE,
            version=version,
        )

        logger.info(f"Worker {worker_id} activated successfully")
        return worker

    def deactivate(
        self, worker_id: str, version: int, updated_by: str = None
    ) -> Worker:
        """
        休眠 Worker

        将 Worker 状态设置为 INACTIVE，不允许 set_online。

        Args:
            worker_id: Worker ID
            version: 当前版本（乐观锁）
            updated_by: 更新来源

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
            ValueError: 版本冲突
        """
        logger.info(f"Deactivating worker {worker_id} by {updated_by}")

        worker = self._store.update_lifecycle_state(
            worker_id=worker_id,
            lifecycle_state=WorkerLifecycleState.INACTIVE,
            version=version,
        )

        logger.info(f"Worker {worker_id} deactivated successfully")
        return worker

    def disable(self, worker_id: str, version: int, updated_by: str = None) -> Worker:
        """
        禁用 Worker

        将 Worker 状态设置为 DISABLED，不允许 set_online。

        Args:
            worker_id: Worker ID
            version: 当前版本（乐观锁）
            updated_by: 更新来源

        Returns:
            更新后的 Worker

        Raises:
            WorkerNotFoundException: Worker 不存在
            ValueError: 版本冲突
        """
        logger.info(f"Disabling worker {worker_id} by {updated_by}")

        worker = self._store.update_lifecycle_state(
            worker_id=worker_id,
            lifecycle_state=WorkerLifecycleState.DISABLED,
            version=version,
        )

        logger.info(f"Worker {worker_id} disabled successfully")
        return worker

    def get_lifecycle_state(self, worker_id: str) -> WorkerLifecycleState:
        """
        获取 Worker 生命周期状态

        Args:
            worker_id: Worker ID

        Returns:
            生命周期状态

        Raises:
            WorkerNotFoundException: Worker 不存在
        """
        worker = self._store.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)
        return worker.lifecycle_state


__all__ = ["WorkerLifecycleService"]