"""
VectorPersistenceBackend Protocol

向量持久化后端接口定义。

职责：
- 定义向量数据的持久化操作
- 不依赖具体存储实现（ZDAS, SQLite, OSS 等）
- 与内存索引配合，实现分布式一致性

架构设计：
┌─────────────────────────────────────────────────────────┐
│              PersistentVectorStoreAdapter               │
│  (组合: FAISS 内存索引 + PersistenceBackend 持久化)      │
└─────────────────────────────────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
┌─────────────────────┐       ┌─────────────────────┐
│  ZDAS Backend       │       │  SQLite Backend     │
│  (预发/灰度/生产)    │       │  (本地开发)          │
└─────────────────────┘       └─────────────────────┘

工作流程：
1. 写入: 先持久化 → 成功后更新内存索引
2. 读取: 直接查内存索引（快速）
3. 同步: 启动/定期从持久化层重建内存索引
4. 一致性: 通过 version 字段 + 定期刷新保证
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.vector_point import VectorPoint


@runtime_checkable
class VectorPersistenceBackend(Protocol):
    """
    向量持久化后端协议

    职责：
    - 向量数据的持久化存储
    - 支持多节点数据同步
    - 不负责向量检索（由 FAISS 内存索引负责）

    设计原则：
    - 持久化层只存储数据，不负责 ANN 检索
    - 检索由内存 FAISS 索引负责
    - 通过版本号/时间戳实现最终一致性

    支持的后端：
    - ZDAS (MySQL): 生产环境，多节点共享
    - SQLite: 本地开发，单节点
    """

    def save(self, point: VectorPoint) -> None:
        """
        保存单个向量点

        如果 id 已存在，则更新；否则插入新记录。

        Args:
            point: 向量点

        Raises:
            ValueError: 如果 vector 维度不一致
            IOError: 如果持久化失败
        """
        ...

    def save_batch(self, points: list[VectorPoint]) -> None:
        """
        批量保存向量点

        Args:
            points: 向量点列表

        Raises:
            ValueError: 如果 vector 维度不一致
            IOError: 如果持久化失败
        """
        ...

    def load_all(self) -> list[VectorPoint]:
        """
        加载所有向量点

        用于启动时重建内存索引。

        Returns:
            所有向量点列表
        """
        ...

    def delete(self, id: str) -> bool:
        """
        删除单个向量点

        Args:
            id: 向量 ID

        Returns:
            是否删除成功（id 不存在时返回 False）
        """
        ...

    def delete_batch(self, ids: list[str]) -> int:
        """
        批量删除向量点

        Args:
            ids: 向量 ID 列表

        Returns:
            实际删除的数量
        """
        ...

    def exists(self, id: str) -> bool:
        """
        检查向量点是否存在

        Args:
            id: 向量 ID

        Returns:
            是否存在
        """
        ...

    def count(self) -> int:
        """
        获取向量点数量

        Returns:
            向量点数量
        """
        ...

    def get_last_modified_time(self) -> float:
        """
        获取最后修改时间戳

        用于判断是否需要刷新内存索引。

        Returns:
            Unix 时间戳（秒），如果没有数据返回 0
        """
        ...


__all__ = ["VectorPersistenceBackend"]