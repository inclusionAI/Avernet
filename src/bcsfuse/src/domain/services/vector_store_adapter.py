"""VectorStoreAdapter Protocol

Vector Store Adapter 接口定义。

职责：
- 定义向量存储的基本操作
- 不依赖具体存储实现（FAISS, Qdrant, Milvus 等）
- 供 application 层调用，由 infra 层实现
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit


@runtime_checkable
class VectorStoreAdapter(Protocol):
    """
    Vector Store Adapter 协议

    定义向量存储的基本操作，支持多种后端实现：
    - FAISS (本地)
    - Qdrant (分布式)
    - Milvus (分布式)
    - OpenSearch / pgvector (数据库扩展)

    设计原则：
    - VectorStoreAdapter 只负责向量检索，不负责业务规则
    - 不负责 domain coverage / skills 过滤 / participant sufficiency
    - 不负责 recommendation 逻辑

    查询链路设计：
    1. metadata filter (by MetadataStoreAdapter)
    2. vector ANN search (by VectorStoreAdapter)
    3. business rerank (by application layer)

    注意：
    - filters 参数在本地 FAISS 实现中可能不直接生效
    - filters 主要为未来分布式向量库预留
    - 如需过滤，请先在 MetadataStore 层完成
    """

    def upsert(self, points: list[VectorPoint]) -> None:
        """
        插入或更新向量点

        如果 id 已存在，则更新；否则插入新记录。

        Args:
            points: 向量点列表

        Raises:
            ValueError: 如果 vector 维度不一致或为空
        """
        ...

    def delete(self, ids: list[str]) -> None:
        """
        删除向量点

        如果 id 不存在，静默忽略。

        Args:
            ids: 要删除的向量 ID 列表
        """
        ...

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        向量相似度搜索

        Args:
            vector: 查询向量
            top_k: 返回结果数量
            filters: 可选过滤条件（本地实现可能不支持）

        Returns:
            搜索结果列表，按相似度降序排列

        Raises:
            ValueError: 如果索引为空或 vector 维度不匹配
        """
        ...

    def batch_search(
        self,
        vectors: list[list[float]],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        批量向量相似度搜索

        Args:
            vectors: 查询向量列表
            top_k: 每个查询返回的结果数量
            filters: 可选过滤条件（本地实现可能不支持）

        Returns:
            搜索结果列表的列表，每个子列表对应一个查询的结果

        Raises:
            ValueError: 如果索引为空或 vector 维度不匹配
        """
        ...

    def save_snapshot(self, path: str) -> None:
        """
        保存索引快照到文件

        Args:
            path: 快照保存路径

        Raises:
            IOError: 如果保存失败
        """
        ...

    def load_snapshot(self, path: str) -> None:
        """
        从文件加载索引快照

        Args:
            path: 快照文件路径

        Raises:
            FileNotFoundError: 如果文件不存在
            IOError: 如果加载失败或格式不匹配
        """
        ...

    def size(self) -> int:
        """
        获取索引中向量数量

        Returns:
            向量数量
        """
        ...

    def get(self, id: str) -> VectorPoint | None:
        """
        根据 ID 获取向量点

        Args:
            id: 向量点 ID

        Returns:
            向量点或 None（如果不存在）
        """
        ...

    def get_vector_ids(self) -> list[str]:
        """
        获取所有向量 ID

        Returns:
            所有向量 ID 列表
        """
        ...

    def text_search(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        BM25 关键词搜索

        基于 BM25 算法对存储的文本内容进行关键词检索。
        检索范围是存储在 payload 中的文本字段（如 content, content_preview）。

        Args:
            query: 查询关键词字符串
            top_k: 返回结果数量
            filters: 可选过滤条件（本地实现可能不支持）

        Returns:
            搜索结果列表，按 BM25 分数降序排列

        Raises:
            ValueError: 如果索引为空或 BM25 索引未初始化
            NotImplementedError: 如果后端不支持 BM25 搜索

        Note:
            - 需要预先建立 BM25 索引（将 payload 中的文本内容索引）
            - 默认检索 payload["content"] 或 payload["content_preview"] 字段
            - 如果未找到文本内容，该记录会被跳过
        """
        ...

    def batch_text_search(
        self,
        queries: list[str],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        批量 BM25 关键词搜索

        Args:
            queries: 查询关键词列表
            top_k: 每个查询返回的结果数量
            filters: 可选过滤条件（本地实现可能不支持）

        Returns:
            搜索结果列表的列表，每个子列表对应一个查询的结果

        Raises:
            ValueError: 如果索引为空或 BM25 索引未初始化
            NotImplementedError: 如果后端不支持 BM25 搜索
        """
        ...


__all__ = ["VectorStoreAdapter"]