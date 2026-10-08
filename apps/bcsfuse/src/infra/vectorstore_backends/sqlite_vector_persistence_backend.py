"""
SQLite Vector Persistence Backend

向量数据的 SQLite 持久化后端实现。

用于本地开发环境，单节点使用。

设计原则：
- 复用现有 SQLite 连接模式（WAL + busy_timeout）
- 表结构与现有 schema 风格一致
- 支持向量数据的 CRUD 和批量操作
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Optional

from src.domain.models.vector_point import VectorPoint
from src.domain.services.vector_persistence_backend import VectorPersistenceBackend

logger = logging.getLogger(__name__)


# 表结构定义（与现有 sqlite_schema.py 风格一致）
CREATE_VECTOR_EMBEDDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_vector_embeddings (
    id TEXT PRIMARY KEY,
    vector BLOB NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bcsfuse_vector_embeddings_gmt_modify
    ON bcsfuse_vector_embeddings(gmt_modify);
"""


class SQLiteVectorPersistenceBackend:
    """
    SQLite 向量持久化后端

    用于本地开发环境，支持：
    - 向量数据的持久化存储
    - 向量的序列化（pickle）
    - 定期同步支持（通过 gmt_modify 判断）

    Attributes:
        db_path: 数据库路径
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        初始化 SQLite 向量持久化后端

        Args:
            db_path: 数据库路径，默认为内存模式
        """
        self._db_path = db_path
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row

        # 启用 WAL 模式提高并发性能（与现有实现一致）
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")

        # 初始化表结构
        self._init_schema()

        logger.info("[SQLiteVectorBackend] Initialized with db_path=%s", db_path)

    def _init_schema(self) -> None:
        """初始化表结构"""
        cursor = self._conn.cursor()
        cursor.executescript(CREATE_VECTOR_EMBEDDINGS_TABLE)
        self._conn.commit()

    def _serialize_vector(self, vector: list[float]) -> bytes:
        """Serialize vector to bytes using numpy (safe, no pickle)."""
        import numpy as np
        return np.array(vector, dtype=np.float32).tobytes()

    def _deserialize_vector(self, data: bytes) -> list[float]:
        """Deserialize bytes to vector.

        Supports both numpy binary format (current) and legacy pickle format
        for backward compatibility with existing database entries.
        """
        import numpy as np

        # Try numpy binary format first (float32 array)
        try:
            arr = np.frombuffer(data, dtype=np.float32)
            return arr.tolist()
        except (ValueError, TypeError):
            pass

        # Legacy: pickle format from older versions
        import pickle
        logger.warning(
            "Deserializing legacy pickle vector data — "
            "re-save to migrate to safe numpy format"
        )
        return pickle.loads(data)

    def save(self, point: VectorPoint) -> None:
        """保存单个向量点"""
        cursor = self._conn.cursor()

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        vector_blob = self._serialize_vector(point.vector)
        payload_json = json.dumps(point.payload)

        # 使用 UPSERT 模式（INSERT OR REPLACE）
        cursor.execute("""
            INSERT OR REPLACE INTO bcsfuse_vector_embeddings
            (id, vector, payload, version, gmt_create, gmt_modify)
            VALUES (?, ?, ?, COALESCE(
                (SELECT version + 1 FROM bcsfuse_vector_embeddings WHERE id = ?), 1
            ), COALESCE(
                (SELECT gmt_create FROM bcsfuse_vector_embeddings WHERE id = ?), ?
            ), ?)
        """, (point.id, vector_blob, payload_json, point.id, point.id, now, now))

        self._conn.commit()
        logger.debug("[SQLiteVectorBackend] Saved vector: id=%s", point.id)

    def save_batch(self, points: list[VectorPoint]) -> None:
        """批量保存向量点"""
        if not points:
            return

        cursor = self._conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        for point in points:
            vector_blob = self._serialize_vector(point.vector)
            payload_json = json.dumps(point.payload)

            cursor.execute("""
                INSERT OR REPLACE INTO bcsfuse_vector_embeddings
                (id, vector, payload, version, gmt_create, gmt_modify)
                VALUES (?, ?, ?, COALESCE(
                    (SELECT version + 1 FROM bcsfuse_vector_embeddings WHERE id = ?), 1
                ), COALESCE(
                    (SELECT gmt_create FROM bcsfuse_vector_embeddings WHERE id = ?), ?
                ), ?)
            """, (point.id, vector_blob, payload_json, point.id, point.id, now, now))

        self._conn.commit()
        logger.info("[SQLiteVectorBackend] Saved %d vectors", len(points))

    def load_all(self) -> list[VectorPoint]:
        """加载所有向量点"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, vector, payload FROM bcsfuse_vector_embeddings")

        points = []
        for row in cursor.fetchall():
            try:
                vector = self._deserialize_vector(row["vector"])
                payload = json.loads(row["payload"])
                points.append(VectorPoint(
                    id=row["id"],
                    vector=vector,
                    payload=payload,
                ))
            except Exception as e:
                logger.warning("[SQLiteVectorBackend] Failed to load vector %s: %s",
                             row["id"], e)

        logger.info("[SQLiteVectorBackend] Loaded %d vectors", len(points))
        return points

    def delete(self, id: str) -> bool:
        """删除单个向量点"""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM bcsfuse_vector_embeddings WHERE id = ?", (id,))
        self._conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("[SQLiteVectorBackend] Deleted vector: id=%s", id)
        return deleted

    def delete_batch(self, ids: list[str]) -> int:
        """批量删除向量点"""
        if not ids:
            return 0

        cursor = self._conn.cursor()
        placeholders = ", ".join(["?" for _ in ids])
        cursor.execute(
            f"DELETE FROM bcsfuse_vector_embeddings WHERE id IN ({placeholders})",
            ids
        )
        self._conn.commit()

        deleted = cursor.rowcount
        logger.info("[SQLiteVectorBackend] Deleted %d vectors", deleted)
        return deleted

    def exists(self, id: str) -> bool:
        """检查向量点是否存在"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bcsfuse_vector_embeddings WHERE id = ? LIMIT 1",
            (id,)
        )
        return cursor.fetchone() is not None

    def count(self) -> int:
        """获取向量点数量"""
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bcsfuse_vector_embeddings")
        return cursor.fetchone()[0]

    def get_last_modified_time(self) -> float:
        """获取最后修改时间戳"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT gmt_modify FROM bcsfuse_vector_embeddings "
            "ORDER BY gmt_modify DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            return 0.0

        try:
            # 解析时间字符串为时间戳
            dt = time.strptime(row["gmt_modify"], "%Y-%m-%d %H:%M:%S")
            return time.mktime(dt)
        except Exception:
            return 0.0

    def get_metadata(self, id: str) -> Optional[dict]:
        """
        获取向量点的 metadata (payload)

        Args:
            id: 向量点 ID

        Returns:
            payload dict 或 None
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT payload FROM bcsfuse_vector_embeddings WHERE id = ?",
            (id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None

        try:
            return json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("[SQLiteVectorBackend] Failed to parse payload for id=%s", id)
            return {}

    def update_payloads_by_worker_id(
        self,
        worker_id: str,
        payload_updates: dict,
    ) -> int:
        """
        Update payload fields for all vectors belonging to a worker.

        Scans all vector rows, matches by payload["worker_id"], merges
        payload_updates into the existing payload JSON, and writes back.

        Args:
            worker_id: Worker ID to match against payload["worker_id"]
            payload_updates: Dict of payload fields to merge

        Returns:
            Number of vectors updated
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, payload FROM bcsfuse_vector_embeddings")

        updated = 0
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for row in cursor.fetchall():
            try:
                payload = json.loads(row["payload"])
                if payload.get("worker_id") == worker_id:
                    payload.update(payload_updates)
                    new_payload_json = json.dumps(payload)
                    update_cursor = self._conn.cursor()
                    update_cursor.execute(
                        "UPDATE bcsfuse_vector_embeddings SET payload = ?, gmt_modify = ? WHERE id = ?",
                        (new_payload_json, now, row["id"]),
                    )
                    updated += 1
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    "[SQLiteVectorBackend] Failed to update payload for id=%s: %s",
                    row["id"], e,
                )

        if updated > 0:
            self._conn.commit()
            logger.info(
                "[SQLiteVectorBackend] Updated %d vector payloads for worker_id=%s, fields=%s",
                updated, worker_id, list(payload_updates.keys()),
            )

        return updated

    def get_all_metadata(self) -> dict[str, dict]:
        """
        获取所有向量点的 metadata

        Returns:
            dict mapping vector ID to metadata
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT id, payload FROM bcsfuse_vector_embeddings")

        metadata_map = {}
        for row in cursor.fetchall():
            try:
                metadata_map[row["id"]] = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "[SQLiteVectorBackend] Failed to parse payload for id=%s",
                    row["id"]
                )
                metadata_map[row["id"]] = {}

        return metadata_map


__all__ = ["SQLiteVectorPersistenceBackend"]