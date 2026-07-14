"""
WorkerProfileBindingStoreAdapter Protocol

Worker 与 Profile 绑定关系存储 Adapter 接口定义。

Stage 1 实现：先做 InMemory，后续替换 SQLite。

职责：
- Worker 与 Profile 的绑定关系

Stage 1 规则：
- 一个 Worker 只允许一个 active profile
- 可以预留多 profile 结构，但不实现复杂绑定流程

为什么值得抽 adapter：
- Worker 和 Profile 是两个独立聚合根
- 绑定关系需要独立管理
- 未来支持多 profile 时只需要扩展这个 adapter
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker_profile_binding import WorkerProfileBinding
from src.domain.models.worker_source_info import WorkerSourceType


@runtime_checkable
class WorkerProfileBindingStoreAdapter(Protocol):
    """
    Profile Binding Store Adapter

    职责：
    - Worker 与 Profile 的绑定关系

    Stage 1 规则：
    - 一个 Worker 只允许一个 active profile

    Stage 1 实现：InMemory（Phase 1）→ SQLite（Phase 2）
    未来可替换：PostgreSQL / 分布式存储
    """

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
        ...

    def unbind_profile(self, worker_id: str, profile_key: str) -> bool:
        """
        解绑 Profile

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识

        Returns:
            是否解绑成功
        """
        ...

    def get_active_binding(self, worker_id: str) -> Optional[WorkerProfileBinding]:
        """
        获取活跃绑定

        Stage 1 只返回一个绑定（或 None）

        Args:
            worker_id: Worker ID

        Returns:
            WorkerProfileBinding 或 None
        """
        ...

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
        ...

    def list_bindings_by_worker(self, worker_id: str) -> list[WorkerProfileBinding]:
        """
        列出 Worker 的所有绑定

        Stage 1 只返回一个（或空列表）

        Args:
            worker_id: Worker ID

        Returns:
            WorkerProfileBinding 列表
        """
        ...

    def get_binding_by_profile_key(self, profile_key: str) -> Optional[WorkerProfileBinding]:
        """
        根据 profile_key 获取绑定

        用于从 participant_id (profile_key) 反查 worker_id。

        Args:
            profile_key: Profile 唯一标识

        Returns:
            WorkerProfileBinding 或 None
        """
        ...


__all__ = ["WorkerProfileBindingStoreAdapter"]