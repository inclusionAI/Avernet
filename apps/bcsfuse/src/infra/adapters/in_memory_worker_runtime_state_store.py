"""
InMemory Worker Runtime State Store

Worker 运行态的内存存储实现。

Stage 1 Phase 1：用于测试和快速验证。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.domain.models.worker_runtime_state import WorkerRuntimeState


class InMemoryWorkerRuntimeStateStore:
    """
    Worker Runtime State 内存存储

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
        self._states: dict[str, WorkerRuntimeState] = {}
        self._updated_at: dict[str, datetime] = {}
        self._updated_by: dict[str, str] = {}

    def get_runtime_state(self, worker_id: str) -> Optional[WorkerRuntimeState]:
        """
        获取运行态

        Args:
            worker_id: Worker ID

        Returns:
            WorkerRuntimeState 或 None
        """
        return self._states.get(worker_id)

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
        self._states[worker_id] = runtime_state
        self._updated_at[worker_id] = datetime.utcnow()
        if updated_by:
            self._updated_by[worker_id] = updated_by
        return True

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
        result = {}
        for worker_id in worker_ids:
            if worker_id in self._states:
                result[worker_id] = self._states[worker_id]
        return result

    def count_by_state(self, runtime_state: WorkerRuntimeState) -> int:
        """
        按状态统计数量

        Args:
            runtime_state: 运行态

        Returns:
            数量
        """
        return sum(
            1 for state in self._states.values()
            if state == runtime_state
        )

    def clear(self) -> None:
        """清空仓库（用于测试清理）"""
        self._states.clear()
        self._updated_at.clear()
        self._updated_by.clear()


__all__ = ["InMemoryWorkerRuntimeStateStore"]