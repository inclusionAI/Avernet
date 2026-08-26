"""
MySQL Vector Persistence Backend (aligned with internal production schema)

Table: bcsfuse_vector_embeddings
Columns:
    `id` VARCHAR(255) PRIMARY KEY,
    `vector` LONGBLOB NOT NULL,
    `payload` JSON NOT NULL DEFAULT '{}',
    `version` INT NOT NULL DEFAULT 1,
    `is_deleted` TINYINT DEFAULT 0,
    `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP

Indexes:
    idx_gmt_modify (gmt_modify)
    idx_is_deleted (is_deleted)
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import threading
from datetime import datetime
from typing import Optional

import mysql.connector
from mysql.connector import Error

from src.domain.models.vector_point import VectorPoint
from src.domain.services.vector_persistence_backend import VectorPersistenceBackend
from src.infra.public.observability.storage_logging import (
    log_storage_event,
    log_storage_error,
    mask_host,
    mask_user,
)

logger = logging.getLogger(__name__)

TABLE_NAME = "bcsfuse_vector_embeddings"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    `id` VARCHAR(255) PRIMARY KEY,
    `vector` LONGBLOB NOT NULL,
    payload JSON DEFAULT NULL,
    `version` INT NOT NULL DEFAULT 1,
    `is_deleted` TINYINT DEFAULT 0,
    `gmt_create` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `gmt_modify` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_gmt_modify (gmt_modify),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


class MySQLVectorPersistenceBackend(VectorPersistenceBackend):
    """MySQL durable backend for vector embeddings."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        collection_name: str = "default",
        vector_dimension: int = 4096,
        distance_metric: str = "Cosine",
    ):
        self.host = host or os.getenv("MYSQL_HOST", "localhost")
        self.port = port or int(os.getenv("MYSQL_PORT", "3306"))
        self.user = user or os.getenv("MYSQL_USER", "")
        self.password = password or os.getenv("MYSQL_PASSWORD", "")
        self.database = database or os.getenv("MYSQL_DATABASE", "bcsfuse")
        self.collection_name = collection_name
        self.vector_dimension = vector_dimension
        self.distance_metric = distance_metric

        self._conn: Optional[mysql.connector.MySQLConnection] = None
        self._schema_initialized = False
        self._lock = threading.Lock()

    def _ensure_connection(self) -> None:
        if self._conn is not None and self._conn.is_connected():
            return

        try:
            self._conn = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=False,
            )
        except Error as e:
            raise RuntimeError(
                f"Failed to connect to MySQL at {mask_host(self.host)}:{self.port}/{self.database}: {e}"
            ) from e

        self._ensure_schema(self._conn)

    def _ensure_schema(self, conn) -> None:
        if self._schema_initialized:
            return

        with self._lock:
            if self._schema_initialized:
                return

            cursor = conn.cursor()
            try:
                cursor.execute(CREATE_TABLE_SQL)
                conn.commit()
                self._schema_initialized = True
                logger.info("[MySQLVectorBackend] Schema initialized: %s", TABLE_NAME)
            finally:
                cursor.close()

    @staticmethod
    def _serialize_vector(vector: list[float]) -> bytes:
        return pickle.dumps(vector, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def _deserialize_vector(data: bytes) -> list[float]:
        return pickle.loads(data)

    def save(self, point: VectorPoint) -> None:
        self.save_batch([point])

    def save_batch(self, points: list[VectorPoint]) -> None:
        if not points:
            return

        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            for point in points:
                vector_blob = self._serialize_vector(point.vector)
                payload_json = json.dumps(point.payload if point.payload is not None else {})

                # Determine if row exists first (avoids ON DUPLICATE KEY UPDATE dialect differences)
                cursor.execute(f"SELECT 1 FROM {TABLE_NAME} WHERE `id` = %s", (point.id,))
                exists = cursor.fetchone() is not None

                if exists:
                    cursor.execute(f"""
                        UPDATE {TABLE_NAME}
                        SET `vector` = %s,
                            payload = %s,
                            version = version + 1,
                            is_deleted = 0,
                            gmt_modify = NOW()
                        WHERE `id` = %s
                    """, (vector_blob, payload_json, point.id))
                else:
                    cursor.execute(f"""
                        INSERT INTO {TABLE_NAME} (`id`, `vector`, `payload`, `version`, `is_deleted`, `gmt_create`, `gmt_modify`)
                        VALUES (%s, %s, %s, 1, 0, NOW(), NOW())
                    """, (point.id, vector_blob, payload_json))

            self._conn.commit()
            logger.debug("[MySQLVectorBackend] Saved %d vectors", len(points))
        except Error as e:
            self._conn.rollback()
            raise RuntimeError(f"Failed to save batch to MySQL: {e}") from e
        finally:
            cursor.close()

    def load_all(self) -> list[VectorPoint]:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"""
                SELECT `id`, `vector`, `payload`
                FROM {TABLE_NAME}
                WHERE `is_deleted` = 0
            """)

            points = []
            for row in cursor.fetchall():
                try:
                    vector = self._deserialize_vector(row[1])
                    payload = json.loads(row[2]) if row[2] else {}
                    points.append(VectorPoint(id=row[0], vector=vector, payload=payload))
                except Exception as e:
                    logger.warning("[MySQLVectorBackend] Failed to load vector %s: %s", row[0], e)

            logger.debug("[MySQLVectorBackend] Loaded %d vectors", len(points))
            return points
        except Error as e:
            raise RuntimeError(f"Failed to load vectors from MySQL: {e}") from e
        finally:
            cursor.close()

    def load_changes_since(self, last_sync_time: float) -> list[VectorPoint]:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"""
                SELECT `id`, `vector`, `payload`
                FROM {TABLE_NAME}
                WHERE `gmt_modify` >= FROM_UNIXTIME(%s)
            """, (last_sync_time,))

            points = []
            for row in cursor.fetchall():
                try:
                    vector = self._deserialize_vector(row[1])
                    payload = json.loads(row[2]) if row[2] else {}
                    points.append(VectorPoint(id=row[0], vector=vector, payload=payload))
                except Exception as e:
                    logger.warning("[MySQLVectorBackend] Failed to load vector %s: %s", row[0], e)

            return points
        except Error as e:
            raise RuntimeError(f"Failed to load changes from MySQL: {e}") from e
        finally:
            cursor.close()

    def delete(self, id: str) -> bool:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"""
                UPDATE {TABLE_NAME} SET `is_deleted` = 1, gmt_modify = NOW() WHERE `id` = %s
            """, (id,))
            self._conn.commit()
            return cursor.rowcount > 0
        except Error as e:
            self._conn.rollback()
            raise RuntimeError(f"Failed to delete vector from MySQL: {e}") from e
        finally:
            cursor.close()

    def delete_batch(self, ids: list[str]) -> int:
        if not ids:
            return 0

        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            placeholders = ", ".join(["%s"] * len(ids))
            cursor.execute(f"""
                UPDATE {TABLE_NAME} SET `is_deleted` = 1, gmt_modify = NOW() WHERE `id` IN ({placeholders})
            """, tuple(ids))
            self._conn.commit()
            return cursor.rowcount
        except Error as e:
            self._conn.rollback()
            raise RuntimeError(f"Failed to delete batch from MySQL: {e}") from e
        finally:
            cursor.close()

    def exists(self, id: str) -> bool:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"SELECT 1 FROM {TABLE_NAME} WHERE `id` = %s LIMIT 1", (id,))
            return cursor.fetchone() is not None
        except Error as e:
            raise RuntimeError(f"Failed to check existence: {e}") from e
        finally:
            cursor.close()

    def count(self) -> int:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE `is_deleted` = 0")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Error as e:
            raise RuntimeError(f"Failed to count vectors: {e}") from e
        finally:
            cursor.close()

    def get_last_modified_time(self) -> float:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"SELECT UNIX_TIMESTAMP(MAX(`gmt_modify`)) FROM {TABLE_NAME}")
            row = cursor.fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
        except Error as e:
            raise RuntimeError(f"Failed to get last modified time: {e}") from e
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn is not None and self._conn.is_connected():
            self._conn.close()
            self._conn = None


__all__ = ["MySQLVectorPersistenceBackend"]
