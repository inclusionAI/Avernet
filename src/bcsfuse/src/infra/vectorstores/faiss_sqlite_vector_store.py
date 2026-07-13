"""
FAISS + SQLite Vector Store

组合 FAISS 内存索引和 SQLite 持久化后端的向量存储实现。

用于本地开发环境，支持：
- 快速向量检索（FAISS 内存索引）
- 数据持久化（SQLite）
- 启动时自动加载
- 写入时同步持久化
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.infra.config.data_paths import resolve_data_path
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter
from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import SQLiteVectorPersistenceBackend

logger = logging.getLogger(__name__)


class FaissSqliteVectorStore:
    """
    FAISS + SQLite 向量存储

    组合模式：
    - FaissVectorStoreAdapter: 内存索引，快速检索
    - SQLiteVectorPersistenceBackend: 持久化后端，本地存储

    特性：
    - 实现 VectorStoreAdapter 接口，可无缝替换 FaissVectorStoreAdapter
    - 写入时先持久化，再更新内存索引
    - 启动时自动从 SQLite 加载到 FAISS
    - 支持定期刷新（用于多进程场景，需外部触发）

    Attributes:
        dimension: 向量维度
    """

    def __init__(
        self,
        dimension: int,
        db_path: str = "data/vector_store.db",
        auto_load: bool = True,
    ):
        """
        初始化 FAISS + SQLite 向量存储

        Args:
            dimension: 向量维度
            db_path: SQLite 数据库路径
            auto_load: 是否自动从持久化加载到内存索引
        """
        # 解析为绝对路径，确保工作目录不影响数据文件位置
        resolved_db_path = resolve_data_path(db_path)

        self._dimension = dimension
        self._faiss_store = FaissVectorStoreAdapter(dimension=dimension)
        self._backend = SQLiteVectorPersistenceBackend(db_path=resolved_db_path)
        self._last_sync_time = 0.0

        # 自动加载
        if auto_load:
            self._load_from_backend()

        logger.info(
            "[FaissSqliteVectorStore] Initialized (dimension=%d, db_path=%s, loaded=%d)",
            dimension, resolved_db_path, self._faiss_store.size()
        )

    @property
    def dimension(self) -> int:
        """获取向量维度"""
        return self._dimension

    def _load_from_backend(self) -> None:
        """从持久化后端加载到内存索引"""
        try:
            points = self._backend.load_all()
            if points:
                self._faiss_store.upsert(points)
                logger.info(
                    "[FaissSqliteVectorStore] Loaded %d vectors from SQLite",
                    len(points)
                )
            self._last_sync_time = self._backend.get_last_modified_time()
        except Exception as e:
            logger.error(
                "[FaissSqliteVectorStore] Failed to load from backend: %s",
                e
            )

    def upsert(self, points: list[VectorPoint]) -> None:
        """
        插入或更新向量点

        流程：
        1. 持久化到 SQLite
        2. 更新 FAISS 内存索引

        Args:
            points: 向量点列表

        Raises:
            ValueError: 如果 vector 维度不一致或为空
        """
        if not points:
            return

        # 1. 持久化
        self._backend.save_batch(points)

        # 2. 更新内存索引
        self._faiss_store.upsert(points)

        logger.debug(
            "[FaissSqliteVectorStore] Upserted %d vectors",
            len(points)
        )

    def delete(self, ids: list[str]) -> None:
        """
        删除向量点

        流程：
        1. 从 SQLite 删除
        2. 从 FAISS 内存索引删除

        Args:
            ids: 要删除的向量 ID 列表
        """
        if not ids:
            return

        # 1. 从持久化删除
        self._backend.delete_batch(ids)

        # 2. 从内存索引删除
        self._faiss_store.delete(ids)

        logger.debug(
            "[FaissSqliteVectorStore] Deleted %d vectors",
            len(ids)
        )

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        向量相似度搜索

        直接查询 FAISS 内存索引。

        Args:
            vector: 查询向量
            top_k: 返回结果数量
            filters: 可选过滤条件（未实现）

        Returns:
            搜索结果列表，按相似度降序排列

        Raises:
            ValueError: 如果索引为空或 vector 维度不匹配
        """
        return self._faiss_store.search(vector, top_k, filters)

    def batch_search(
        self,
        vectors: list[list[float]],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        批量向量相似度搜索

        直接查询 FAISS 内存索引。

        Args:
            vectors: 查询向量列表
            top_k: 每个查询返回的结果数量
            filters: 可选过滤条件（未实现）

        Returns:
            搜索结果列表的列表

        Raises:
            ValueError: 如果索引为空或 vector 维度不匹配
        """
        return self._faiss_store.batch_search(vectors, top_k, filters)

    def save_snapshot(self, path: str) -> None:
        """
        保存索引快照到文件

        注意：此实现同步持久化和文件快照。

        Args:
            path: 快照保存路径
        """
        # 同时保存 FAISS 快照和 SQLite 持久化
        self._faiss_store.save_snapshot(path)
        logger.info(
            "[FaissSqliteVectorStore] Snapshot saved to %s",
            path
        )

    def load_snapshot(self, path: str) -> None:
        """
        从文件加载索引快照

        注意：此实现从文件加载 FAISS 索引，但不会更新 SQLite。

        Args:
            path: 快照文件路径
        """
        self._faiss_store.load_snapshot(path)
        logger.info(
            "[FaissSqliteVectorStore] Snapshot loaded from %s",
            path
        )

    def size(self) -> int:
        """
        获取索引中向量数量

        Returns:
            向量数量
        """
        return self._faiss_store.size()

    def get_vector_ids(self) -> list[str]:
        """
        获取所有活跃的向量 ID

        Returns:
            向量 ID 列表
        """
        return self._faiss_store.get_vector_ids()

    def sync_from_backend(self, force: bool = False) -> int:
        """
        从持久化后端同步数据

        用于多进程场景，定期刷新内存索引。

        Args:
            force: 是否强制刷新（忽略时间检查）

        Returns:
            同步的向量数量
        """
        backend_time = self._backend.get_last_modified_time()

        if not force and backend_time <= self._last_sync_time:
            logger.debug(
                "[FaissSqliteVectorStore] No new data to sync (backend_time=%s, last_sync=%s)",
                backend_time, self._last_sync_time
            )
            return 0

        # 重新加载所有数据
        self._faiss_store = FaissVectorStoreAdapter(dimension=self._dimension)
        self._load_from_backend()

        logger.info(
            "[FaissSqliteVectorStore] Synced %d vectors from backend",
            self._faiss_store.size()
        )
        return self._faiss_store.size()

    def clear(self) -> None:
        """
        清空所有数据

        同时清空内存索引和持久化存储。
        """
        # 获取所有 ID
        all_ids = self._faiss_store.get_vector_ids()

        # 从持久化删除
        if all_ids:
            self._backend.delete_batch(all_ids)

        # 重置内存索引
        self._faiss_store = FaissVectorStoreAdapter(dimension=self._dimension)

        logger.info("[FaissSqliteVectorStore] Cleared all data")

    def update_payload_by_worker(
        self,
        worker_id: str,
        payload_updates: dict,
    ) -> int:
        """
        Update payload fields for all vectors belonging to a worker.

        Faiss does not support partial payload updates natively (unlike
        Qdrant's ``set_payload``).  This method updates the in-memory
        payload dict AND the SQLite persistence backend in one call,
        so that subsequent searches return the new payload values.

        Typical use-case: synchronise availability / runtime_state changes
        to vector payloads so that post-filters work correctly, without
        re-embedding the profile.

        Args:
            worker_id: Worker ID to match against payload["worker_id"]
            payload_updates: Dict of payload fields to merge
                (e.g. {"availability": "public", "runtime_state": "online"})

        Returns:
            Number of vectors updated
        """
        # 1. Update in-memory payloads (FaissVectorStoreAdapter._id_to_payload)
        memory_count = self._faiss_store.update_payloads_by_worker_id(
            worker_id, payload_updates,
        )

        # 2. Persist to SQLite backend (so restart survives)
        backend_count = self._backend.update_payloads_by_worker_id(
            worker_id, payload_updates,
        )

        count = max(memory_count, backend_count)
        if count > 0:
            logger.info(
                "[FaissSqliteVectorStore] Updated %d vector payloads for "
                "worker_id=%s, fields=%s",
                count, worker_id, list(payload_updates.keys()),
            )
        else:
            logger.debug(
                "[FaissSqliteVectorStore] No vectors found for worker_id=%s",
                worker_id,
            )

        return count

    def text_search(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        BM25 关键词搜索

        委托给底层 FAISS 存储进行 BM25 检索。

        Args:
            query: 查询关键词字符串
            top_k: 返回结果数量
            filters: 可选过滤条件

        Returns:
            搜索结果列表，按 BM25 分数降序排列
        """
        return self._faiss_store.text_search(query, top_k, filters)

    def batch_text_search(
        self,
        queries: list[str],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        批量 BM25 关键词搜索

        委托给底层 FAISS 存储进行 BM25 检索。

        Args:
            queries: 查询关键词列表
            top_k: 每个查询返回的结果数量
            filters: 可选过滤条件

        Returns:
            搜索结果列表的列表
        """
        return self._faiss_store.batch_text_search(queries, top_k, filters)


# 类型检查：确保实现了 VectorStoreAdapter
def _check_protocol() -> None:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        _: VectorStoreAdapter = FaissSqliteVectorStore(dimension=128)


__all__ = ["FaissSqliteVectorStore"]