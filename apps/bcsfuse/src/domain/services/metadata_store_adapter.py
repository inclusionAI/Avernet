"""MetadataStoreAdapter Protocol

Metadata Store Adapter 接口定义。

职责：
- 定义元数据存储的基本操作
- 不依赖具体存储实现（文件、数据库等）
- 供 application 层调用，由 infra 层实现
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.metadata_record import MetadataRecord


@runtime_checkable
class MetadataStoreAdapter(Protocol):
    """
    Metadata Store Adapter 协议

    定义元数据存储的基本操作，支持多种后端实现：
    - 本地文件存储 (JSON/JSONL)
    - SQLite
    - PostgreSQL
    - 分布式数据库

    设计原则：
    - MetadataStoreAdapter 只负责元数据存取和过滤
    - 不负责向量相似度计算
    - 不负责 rerank 策略
    - 不负责业务 recommendation 决策

    查询链路设计：
    1. metadata filter (by MetadataStoreAdapter)
    2. vector ANN search (by VectorStoreAdapter)
    3. business rerank (by application layer)

    核心功能：
    - profile_key -> record 映射
    - vector_id -> record 映射
    - 基于 domains / skills / roles 的过滤
    - 批量读取 metadata
    """

    def upsert(self, records: list[MetadataRecord]) -> None:
        """
        插入或更新元数据记录

        如果 profile_key 已存在，则更新；否则插入新记录。

        Args:
            records: 元数据记录列表

        Raises:
            ValueError: 如果记录数据不合法
        """
        ...

    def get(self, profile_key: str) -> MetadataRecord | None:
        """
        根据 profile_key 获取元数据记录

        Args:
            profile_key: Profile 唯一标识

        Returns:
            元数据记录或 None
        """
        ...

    def get_by_vector_ids(self, vector_ids: list[int]) -> list[MetadataRecord]:
        """
        根据 vector_id 列表批量获取元数据记录

        Args:
            vector_ids: Vector ID 列表

        Returns:
            元数据记录列表（不存在的 vector_id 会被忽略）
        """
        ...

    def filter(self, filters: dict | None = None) -> list[MetadataRecord]:
        """
        根据条件过滤元数据记录

        支持的过滤条件：
        - domains: 包含指定 domain（OR 语义）
        - profile_type: 匹配指定 profile_type
        - active_skill_names: 包含指定 skill（OR 语义）
        - suitable_roles: 包含指定 role（OR 语义）

        示例:
            >>> filters = {
            ...     "domains": ["backend", "frontend"],
            ...     "active_skill_names": ["python"],
            ...     "profile_type": "default"
            ... }

        Args:
            filters: 过滤条件字典

        Returns:
            匹配的元数据记录列表
        """
        ...

    def delete(self, profile_keys: list[str]) -> None:
        """
        删除元数据记录

        如果 profile_key 不存在，静默忽略。

        Args:
            profile_keys: 要删除的 profile_key 列表
        """
        ...

    def save(self, path: str) -> None:
        """
        保存元数据到文件

        Args:
            path: 保存路径（目录）

        Raises:
            IOError: 如果保存失败
        """
        ...

    def load(self, path: str) -> None:
        """
        从文件加载元数据

        Args:
            path: 加载路径（目录）

        Raises:
            FileNotFoundError: 如果文件不存在
            IOError: 如果加载失败
        """
        ...

    def size(self) -> int:
        """
        获取元数据记录数量

        Returns:
            记录数量
        """
        ...


__all__ = ["MetadataStoreAdapter"]