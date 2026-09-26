"""
Profile Embedding Store (Open-Core Safe - Local Only)

Phase E: Profile Embedding 持久化存储（Fragment 化支持）

职责：
- 管理 profile embedding 的持久化
- 使用 Qdrant Embedded as 向量引擎（本地文件持久化）
- 提供索引构建和查询接口
- 支持 Fragment ID 格式解析（新旧兼容）

Open-Core版本：
- 仅支持 local 模式（Qdrant + 本地文件）
- 不支持 ZDAS 模式（ZDAS 版本请使用 bcsfuse_internal.providers）
- 不依赖 Database 或 ZDAS 相关基础设施

ID 格式：
- 旧格式: "{profile_key}" (如 "2088888:default")
- 新格式: "{profile_key}:{fragment_type}" (如 "2088888:default:soul")
- 新格式（带索引）: "{profile_key}:{fragment_type}:{index}"
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.infra.config.data_paths import resolve_data_path
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class ZdasInternalOnlyProviderUnavailable(RuntimeError):
    """Raised when attempting to use ZDAS mode in open-core."""
    pass


class ProfileEmbeddingStore:
    """
    Profile Embedding 存储（Open-Core - Local Only）

    使用 Qdrant Embedded 作为向量引擎，本地文件持久化。

    Open-Core版本限制：
    - 仅支持 local 模式（Qdrant + 本地文件）
    - ZDAS 模式不可用（请使用 bcsfuse_internal.providers）

    Attributes:
        vector_store: VectorStoreAdapter 实例
        dimension: 向量维度
        index_type: 索引类型（固定为 "local"）
    """

    # Fragment ID 解析正则
    # 格式: {profile_key}:{fragment_type} 或 {profile_key}:{fragment_type}:{index}
    # profile_key 格式: {staff_id}:{profile_id}，可能包含冒号
    # 所以我们从右往左解析
    FRAGMENT_ID_PATTERN = re.compile(
        r"^(?P<profile_key>[\w:]+):(?P<fragment_type>[a-z]+)(?::(?P<index>\d+))?$"
    )

    def __init__(
        self,
        dimension: int = 4096,
        index_type: str = "local",
        db_path: str = "data/vector_store.db",
        database: Optional[object] = None,  # Ignored in open-core
        datasource_name: str = "agentclaw_ds",  # Ignored in open-core
        refresh_interval_seconds: int = 60,  # Ignored in open-core
        vector_store=None,  # Optional: reuse existing vector store
    ):
        """
        初始化 Profile Embedding Store（Open-Core - Local Only）

        Args:
            dimension: 向量维度（默认 4096）
            index_type: 索引类型（仅支持 "local"，其他值会抛出错误）
            db_path: 数据库路径（用于确定 qdrant_storage 目录）
            database: 数据库实例（忽略，open-core 不使用）
            datasource_name: 数据源名称（忽略，open-core 不使用）
            refresh_interval_seconds: 刷新间隔秒数（忽略，open-core 不使用）

        Raises:
            ZdasInternalOnlyProviderUnavailable: 如果尝试使用 ZDAS 模式
        """
        self._dimension = dimension
        self._db_path = db_path

        # Open-core: 仅支持 local 模式
        if index_type != "local":
            raise ZdasInternalOnlyProviderUnavailable(
                f"ZDAS profile embedding store is internal-only and has moved to "
                f"bcsfuse_internal.providers.indexing. Open-core must use local mode. "
                f"Received index_type='{index_type}'. Please use bcsfuse_internal "
                f"provider wiring for ZDAS support."
            )

        self._index_type = "local"

        # Reuse existing vector store if provided (avoid Qdrant lock conflicts)
        if vector_store is not None:
            self._vector_store = vector_store
            logger.info(
                "[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore "
                f"vector_store_id={id(vector_store)} "
                f"storage_path={getattr(vector_store, 'path', 'N/A')} "
                f"collection={getattr(vector_store, 'collection_name', 'N/A')} "
                f"source=injected dimension={dimension} size={self._vector_store.size()}"
            )
        else:
            # 创建新的 QdrantLocalVectorStore（本地文件持久化）
            from src.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore

            storage_dir = os.path.dirname(db_path)
            storage_path = os.path.join(storage_dir, "qdrant_storage")

            logger.warning(
                "[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore "
                f"vector_store_id=None source=created WARNING=NEW_QDRANT_CLIENT_CREATED "
                f"storage_path={storage_path} dimension={dimension}"
            )

            self._vector_store = QdrantLocalVectorStore(
                dimension=dimension,
                path=storage_path,
                collection_name="bcsfuse_profiles",
            )

            logger.info(
                "[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore "
                f"vector_store_id={id(self._vector_store)} "
                f"storage_path={storage_path} "
                f"source=created dimension={dimension} size={self._vector_store.size()}"
            )

    @property
    def vector_store(self) -> VectorStoreAdapter:
        """获取底层 vector store"""
        return self._vector_store

    @property
    def dimension(self) -> int:
        """获取向量维度"""
        return self._dimension

    @property
    def size(self) -> int:
        """获取索引大小"""
        return self._vector_store.size()

    @property
    def index_type(self) -> str:
        """获取索引类型（固定为 "local"）"""
        return self._index_type

    def _parse_fragment_id(self, point_id: str) -> tuple[str, str, Optional[int]]:
        """
        解析 Fragment ID

        Args:
            point_id: 向量点 ID，可能是：
                - 旧格式: "{profile_key}"
                - 新格式: "{profile_key}:{fragment_type}"
                - 新格式（带索引）: "{profile_key}:{fragment_type}:{index}"

        Returns:
            (profile_key, fragment_type, index) 元组
        """
        match = self.FRAGMENT_ID_PATTERN.match(point_id)
        if match:
            profile_key = match.group("profile_key")
            fragment_type = match.group("fragment_type")
            index = int(match.group("index")) if match.group("index") else None
            return profile_key, fragment_type, index

        # 旧格式：直接返回 profile_key
        return point_id, "default", None

    def upsert(self, points: list[VectorPoint]) -> None:
        """
        插入或更新向量

        Args:
            points: 向量点列表
        """
        self._vector_store.upsert(points)
        logger.debug("[ProfileEmbeddingStore] Upserted %d vectors", len(points))

    def upsert_embeddings(
        self,
        embeddings: list[tuple[str, list[float], dict]],
    ) -> int:
        """
        批量插入或更新 embeddings

        Args:
            embeddings: (profile_key, vector, metadata) 列表

        Returns:
            插入/更新的数量
        """
        if not embeddings:
            return 0

        points = []
        for profile_key, vector, metadata in embeddings:
            points.append(VectorPoint(
                id=profile_key,
                vector=vector,
                payload=metadata,
            ))

        self._vector_store.upsert(points)

        logger.info(
            "[ProfileEmbeddingStore] Upserted %d embeddings",
            len(points)
        )

        return len(points)

    def update_payloads(self, updates: list[tuple[str, dict]]) -> int:
        """
        批量更新 fragment payloads（不重新计算 embedding）

        用于 worker_state 标签变化时只更新 metadata。

        Args:
            updates: 更新列表，每个元素为 (fragment_id, new_payload)

        Returns:
            成功更新的数量
        """
        if not updates:
            return 0

        updated_count = 0
        try:
            # 需要复用现有的 vectors，只更新 payload
            # 但底层 storage 的 upsert 会覆盖整个 point
            # 所以先 get 再 update
            for fragment_id, new_payload in updates:
                try:
                    # 尝试获取现有 point
                    existing_point = self._vector_store.get(fragment_id)
                    if existing_point and hasattr(existing_point, 'vector'):
                        # 创建新的 point，保持原有 vector，更新 payload
                        updated_point = VectorPoint(
                            id=fragment_id,
                            vector=existing_point.vector,
                            payload=new_payload,
                        )
                        self._vector_store.upsert([updated_point])
                        updated_count += 1
                    else:
                        logger.debug(
                            f"[ProfileEmbeddingStore] Fragment not found for payload update: {fragment_id}"
                        )
                except Exception as e:
                    logger.debug(
                        f"[ProfileEmbeddingStore] Failed to update payload for {fragment_id}: {e}"
                    )

            if updated_count > 0:
                logger.info(
                    f"[ProfileEmbeddingStore] Updated payloads for {updated_count}/{len(updates)} fragments"
                )

        except Exception as e:
            logger.error(f"[ProfileEmbeddingStore] Failed to update payloads: {e}")

        return updated_count

    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        filter_conditions: Optional[dict] = None,
    ) -> list[VectorSearchHit]:
        """
        搜索相似向量

        Args:
            vector: 查询向量
            top_k: 返回结果数量
            filter_conditions: 过滤条件

        Returns:
            相似向量列表
        """
        return self._vector_store.search(vector, top_k=top_k, filter_conditions=filter_conditions)

    def delete(self, ids: list[str]) -> None:
        """
        删除向量

        Args:
            ids: 向量 ID 列表
        """
        self._vector_store.delete(ids)
        logger.debug("[ProfileEmbeddingStore] Deleted %d vectors", len(ids))

    def get_fragments_by_profile(self, profile_key: str) -> list[tuple[str, list[float], dict]]:
        """
        获取指定 profile 的所有 fragment vectors

        用于智能更新策略：获取已有 payload 以比较 content_hash。
        Aligned with internal ``QdrantZdasVectorStore`` implementation.

        Args:
            profile_key: Profile 标识 (如 "worker_id:profile_id")

        Returns:
            Fragment 列表，每个元素为 (fragment_id, vector, payload)
        """
        prefix = f"{profile_key}:"
        results: list[tuple[str, list[float], dict]] = []

        try:
            all_ids = self._vector_store.get_vector_ids()

            # 筛选属于该 profile 的 fragments（精确匹配 + 前缀匹配 fragment IDs）
            fragment_ids = [
                doc_id for doc_id in all_ids
                if doc_id == profile_key or doc_id.startswith(prefix)
            ]

            if not fragment_ids:
                return []

            for doc_id in fragment_ids:
                try:
                    point = self._vector_store.get(doc_id)
                    if point and hasattr(point, 'payload') and point.payload:
                        vector = list(point.vector) if hasattr(point, 'vector') and point.vector else []
                        results.append((doc_id, vector, point.payload))
                except Exception as e:
                    logger.debug(
                        "[ProfileEmbeddingStore] Failed to get fragment %s: %s", doc_id, e
                    )

            if results:
                logger.debug(
                    "[ProfileEmbeddingStore] Retrieved %d fragments for %s",
                    len(results), profile_key
                )

        except Exception as e:
            logger.error(
                "[ProfileEmbeddingStore] Failed to get fragments for %s: %s",
                profile_key, e
            )

        return results

    def delete_by_profile_key(self, profile_key: str) -> int:
        """
        删除指定 profile 的所有向量

        使用 get_vector_ids() + 前缀匹配代替不存在的 list_all()。

        Args:
            profile_key: Profile key

        Returns:
            删除的向量数量
        """
        prefix = f"{profile_key}:"
        try:
            all_ids = self._vector_store.get_vector_ids()
        except Exception as e:
            logger.error(
                "[ProfileEmbeddingStore] delete_by_profile_key: get_vector_ids failed: %s", e
            )
            return 0

        # 精确匹配（旧格式）+ 前缀匹配（fragment 格式）
        ids_to_delete = [
            vid for vid in all_ids
            if vid == profile_key or vid.startswith(prefix)
        ]

        if ids_to_delete:
            self._vector_store.delete(ids_to_delete)
            logger.info(
                "[ProfileEmbeddingStore] Deleted %d vectors for profile_key=%s",
                len(ids_to_delete), profile_key
            )

        return len(ids_to_delete)

    def clear(self) -> None:
        """清空所有向量"""
        self._vector_store.clear()
        logger.info("[ProfileEmbeddingStore] Cleared all vectors")

    # ========================================================================
    # ZDAS-Only Methods (No-op for Open-Core)
    # ========================================================================

    def rebuild_index(self) -> int:
        """
        重建索引（ZDAS-only，open-core 不支持）

        Returns:
            当前索引大小（no-op）
        """
        logger.warning(
            "[Open-Core] rebuild_index is ZDAS-only and not supported in open-core. "
            "Use bcsfuse_internal providers for ZDAS support."
        )
        return self._vector_store.size()

    def purge_deleted_embeddings(self, before_days: int = 0) -> int:
        """
        物理删除已软删除的 embeddings（ZDAS-only，open-core 不支持）

        Args:
            before_days: 删除多少天前的软删除记录（忽略）

        Returns:
            0（no-op）
        """
        logger.warning(
            "[Open-Core] purge_deleted_embeddings is ZDAS-only and not supported in open-core. "
            "Use bcsfuse_internal providers for ZDAS support."
        )
        return 0

    def start_scheduler(self) -> bool:
        """
        启动同步定时任务（ZDAS-only，open-core 不支持）

        Returns:
            False（no-op）
        """
        logger.warning(
            "[Open-Core] start_scheduler is ZDAS-only and not supported in open-core. "
            "Use bcsfuse_internal providers for ZDAS support."
        )
        return False

    def start_full_sync_scheduler(self) -> bool:
        """
        启动同步定时任务（兼容旧方法名，ZDAS-only）

        Returns:
            False（no-op）
        """
        return self.start_scheduler()

    def get_sync_status(self) -> dict:
        """
        获取同步状态（ZDAS-only，open-core 不支持）

        Returns:
            状态字典（表示不可用）
        """
        return {
            "sync_manager": "not_available",
            "vector_store_type": type(self._vector_store).__name__,
            "message": "Sync scheduler is ZDAS-only. Use bcsfuse_internal providers for ZDAS support.",
        }

    def force_full_sync(self) -> dict:
        """
        强制立即执行全量同步（ZDAS-only，open-core 不支持）

        Returns:
            错误结果
        """
        return {
            "success": False,
            "error": "SyncManager not available (ZDAS-only). Use bcsfuse_internal providers for ZDAS support.",
        }

    def force_incremental_sync(self) -> dict:
        """
        强制立即执行增量同步（ZDAS-only，open-core 不支持）

        Returns:
            错误结果
        """
        return {
            "success": False,
            "error": "SyncManager not available (ZDAS-only). Use bcsfuse_internal providers for ZDAS support.",
        }

    def __repr__(self) -> str:
        return f"ProfileEmbeddingStore(dimension={self._dimension}, mode=local, size={self.size()})"