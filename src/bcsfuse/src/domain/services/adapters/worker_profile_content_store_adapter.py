"""
Worker Profile Content Store Adapter Protocol

Profile API MVP - Profile 内容存储适配器协议

定义 Profile 内容存储的抽象接口，支持未来替换为：
- SQLite (MVP)
- PostgreSQL
- MongoDB/文档库
- 云存储
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.domain.models.worker_profile_content import (
    WorkerProfileContent,
    WorkerProfileContentList,
)


@runtime_checkable
class WorkerProfileContentStoreAdapter(Protocol):
    """
    Worker Profile Content Store Adapter 协议

    定义 Profile 内容存储的基本操作。

    核心方法：
    - save: 保存/更新 profile
    - get: 获取 profile
    - list: 列出 profiles
    - delete: 删除 profile
    - activate: 设置活跃 profile
    - get_active: 获取活跃 profile
    """

    def save(self, content: WorkerProfileContent) -> WorkerProfileContent:
        """
        保存 Profile 内容

        如果已存在则更新，否则创建。

        Args:
            content: Profile 内容

        Returns:
            保存后的 Profile 内容（包含时间戳、版本等）
        """
        ...

    def get(self, worker_id: str, profile_id: str) -> Optional[WorkerProfileContent]:
        """
        获取指定 Profile

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            Profile 内容或 None
        """
        ...

    def list_by_worker(self, worker_id: str) -> WorkerProfileContentList:
        """
        列出 Worker 的所有 Profiles

        Args:
            worker_id: Worker ID

        Returns:
            Profile 列表
        """
        ...

    def delete(self, worker_id: str, profile_id: str) -> bool:
        """
        删除 Profile

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            是否删除成功
        """
        ...

    def activate(self, worker_id: str, profile_id: str) -> Optional[WorkerProfileContent]:
        """
        设置活跃 Profile

        将指定 Profile 设为活跃，其他 Profile 设为非活跃。

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            激活后的 Profile 内容或 None（如果不存在）
        """
        ...

    def get_active(self, worker_id: str) -> Optional[WorkerProfileContent]:
        """
        获取活跃 Profile

        Args:
            worker_id: Worker ID

        Returns:
            活跃的 Profile 内容或 None
        """
        ...

    def exists(self, worker_id: str, profile_id: str) -> bool:
        """
        检查 Profile 是否存在

        Args:
            worker_id: Worker ID
            profile_id: Profile ID

        Returns:
            是否存在
        """
        ...

    def count(self, worker_id: Optional[str] = None) -> int:
        """
        统计 Profile 数量

        Args:
            worker_id: 可选，指定 Worker ID

        Returns:
            Profile 数量
        """
        ...

    def get_all_active(self) -> list[WorkerProfileContent]:
        """
        获取所有活跃 Profile

        用于检索/推荐系统批量加载。

        Returns:
            所有活跃 Profile 列表
        """
        ...


__all__ = ["WorkerProfileContentStoreAdapter"]