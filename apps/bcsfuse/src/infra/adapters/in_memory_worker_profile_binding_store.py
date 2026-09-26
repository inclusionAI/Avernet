"""
InMemory Worker Profile Binding Store

Worker 与 Profile 绑定关系的内存存储实现。

Stage 1 Phase 1：用于测试和快速验证。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.domain.models.worker_profile_binding import WorkerProfileBinding
from src.domain.models.worker_source_info import WorkerSourceType


class InMemoryWorkerProfileBindingStore:
    """
    Worker Profile Binding 内存存储

    Stage 1 Phase 1 实现：
    - 纯内存存储（dict）
    - 无持久化
    - 非线程安全

    Stage 1 规则：
    - 一个 Worker 只允许一个 active profile

    用于：
    - 测试
    - 快速验证业务逻辑
    - 契约测试
    """

    def __init__(self):
        """初始化空仓库"""
        # key: worker_id, value: WorkerProfileBinding
        self._bindings: dict[str, WorkerProfileBinding] = {}
        # key: profile_key, value: worker_id
        self._profile_to_worker: dict[str, str] = {}

    def bind_profile(
        self,
        worker_id: str,
        profile_key: str,
        source_type: WorkerSourceType,
    ) -> WorkerProfileBinding:
        """
        绑定 Profile 到 Worker

        Stage 1 规则：
        - 如果已有 active binding，先 deactive 旧的

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识
            source_type: 来源类型

        Returns:
            WorkerProfileBinding
        """
        now = datetime.utcnow()

        # 如果已有旧的绑定，标记为非活跃
        if worker_id in self._bindings:
            old_binding = self._bindings[worker_id]
            if old_binding.is_active:
                old_binding.is_active = False
                old_binding.updated_at = now

        # 创建新绑定
        binding = WorkerProfileBinding(
            worker_id=worker_id,
            profile_key=profile_key,
            is_active=True,
            source_type=source_type,
            bound_at=now,
            updated_at=now,
        )

        # 存储
        self._bindings[worker_id] = binding
        self._profile_to_worker[profile_key] = worker_id

        return binding.model_copy(deep=True)

    def unbind_profile(self, worker_id: str, profile_key: str) -> bool:
        """
        解绑 Profile

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识

        Returns:
            是否解绑成功
        """
        if worker_id not in self._bindings:
            return False

        binding = self._bindings[worker_id]
        if binding.profile_key != profile_key:
            return False

        # 标记为非活跃
        binding.is_active = False
        binding.updated_at = datetime.utcnow()

        # 从映射中移除
        if profile_key in self._profile_to_worker:
            del self._profile_to_worker[profile_key]

        return True

    def get_active_binding(self, worker_id: str) -> Optional[WorkerProfileBinding]:
        """
        获取活跃绑定

        Stage 1 只返回一个绑定（或 None）

        Args:
            worker_id: Worker ID

        Returns:
            WorkerProfileBinding 或 None
        """
        binding = self._bindings.get(worker_id)
        if binding and binding.is_active:
            return binding.model_copy(deep=True)
        return None

    def set_active_profile(
        self,
        worker_id: str,
        profile_key: str,
    ) -> bool:
        """
        设置活跃 Profile

        Stage 1 只支持一个 active，会替换现有的

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识

        Returns:
            是否设置成功
        """
        if worker_id not in self._bindings:
            return False

        binding = self._bindings[worker_id]

        # 如果是同一个 profile，不需要操作
        if binding.profile_key == profile_key and binding.is_active:
            return True

        # 标记旧的为非活跃
        old_profile_key = binding.profile_key
        binding.is_active = False
        binding.updated_at = datetime.utcnow()

        # 移除旧的映射
        if old_profile_key in self._profile_to_worker:
            del self._profile_to_worker[old_profile_key]

        # 创建新的活跃绑定
        self.bind_profile(
            worker_id=worker_id,
            profile_key=profile_key,
            source_type=binding.source_type,
        )

        return True

    def list_bindings_by_worker(self, worker_id: str) -> list[WorkerProfileBinding]:
        """
        列出 Worker 的所有绑定

        Stage 1 只返回一个（或空列表）

        Args:
            worker_id: Worker ID

        Returns:
            WorkerProfileBinding 列表
        """
        binding = self._bindings.get(worker_id)
        if binding:
            return [binding.model_copy(deep=True)]
        return []

    def get_binding_by_profile_key(self, profile_key: str) -> Optional[WorkerProfileBinding]:
        """
        根据 profile_key 获取绑定

        用于从 participant_id (profile_key) 反查 worker_id。

        Args:
            profile_key: Profile 唯一标识

        Returns:
            WorkerProfileBinding 或 None
        """
        worker_id = self._profile_to_worker.get(profile_key)
        if worker_id is None:
            return None

        binding = self._bindings.get(worker_id)
        if binding and binding.is_active:
            return binding.model_copy(deep=True)
        return None

    def clear(self) -> None:
        """清空仓库（用于测试清理）"""
        self._bindings.clear()
        self._profile_to_worker.clear()


__all__ = ["InMemoryWorkerProfileBindingStore"]