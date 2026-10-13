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
from functools import wraps
from typing import Optional

import mysql.connector
from mysql.connector import Error

from src.domain.models.vector_point import VectorPoint
from src.domain.services.vector_persistence_backend import (
    VectorChangeSet,
    VectorPersistenceBackend,
)
from src.infra.public.observability.storage_logging import (
    log_storage_error,
    log_storage_event,
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


def _synchronized_connection(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


def _synchronized_pages(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        pages = method(self, *args, **kwargs)
        while True:
            with self._lock:
                try:
                    page = next(pages)
                except StopIteration:
                    return
            yield page

    return wrapped


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
        self._lock = threading.RLock()

    @_synchronized_connection
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
            log_storage_error(
                logger,
                "mysql_vector_connection_failure",
                component="mysql_vector_persistence_backend",
                operation="connect",
                validation_phase="connection",
                backend="mysql",
                target_resource=TABLE_NAME,
                error=e,
                host=mask_host(self.host),
                user=mask_user(self.user),
            )
            raise RuntimeError(
                f"Failed to connect to MySQL at {mask_host(self.host)}:{self.port}/{self.database}: {e}"
            ) from e

        log_storage_event(
            logger,
            logging.INFO,
            "mysql_vector_connection_success",
            component="mysql_vector_persistence_backend",
            operation="connect",
            validation_phase="connection",
            backend="mysql",
            target_resource=TABLE_NAME,
            host=mask_host(self.host),
            user=mask_user(self.user),
        )
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

    def _finish_read(self, cursor) -> None:
        """Close the cursor and release the current repeatable-read snapshot."""
        try:
            cursor.close()
        finally:
            # mysql-connector starts a transaction for SELECT when autocommit is
            # disabled. Keeping it open makes subsequent incremental reads miss
            # writes committed by another service instance.
            self._conn.rollback()

    def save(self, point: VectorPoint) -> None:
        self.save_batch([point])

    @_synchronized_connection
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

    @_synchronized_connection
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
                    raise RuntimeError(f"Failed to decode persisted vector {row[0]}") from e

            logger.debug("[MySQLVectorBackend] Loaded %d vectors", len(points))
            return points
        except Error as e:
            raise RuntimeError(f"Failed to load vectors from MySQL: {e}") from e
        finally:
            self._finish_read(cursor)

    @_synchronized_connection
    def _active_upper_id(self) -> str | None:
        self._ensure_connection()
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                f"SELECT MAX(`id`) FROM {TABLE_NAME} WHERE `is_deleted` = 0",
                (),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Error as e:
            raise RuntimeError(f"Failed to read MySQL rebuild upper ID: {e}") from e
        finally:
            self._finish_read(cursor)

    @_synchronized_pages
    def iter_batches(self, batch_size: int):
        """Page active rows by primary key so a rebuild never buffers all vectors."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        upper_id = self._active_upper_id()
        if upper_id is None:
            return
        last_id = None
        while True:
            self._ensure_connection()
            cursor = self._conn.cursor()
            try:
                if last_id is None:
                    cursor.execute(
                        f"SELECT `id`, `vector`, `payload` FROM {TABLE_NAME} "
                        "WHERE `is_deleted` = 0 AND `id` <= %s "
                        "ORDER BY `id` LIMIT %s",
                        (upper_id, batch_size),
                    )
                else:
                    cursor.execute(
                        f"SELECT `id`, `vector`, `payload` FROM {TABLE_NAME} "
                        "WHERE `is_deleted` = 0 AND `id` > %s AND `id` <= %s "
                        "ORDER BY `id` LIMIT %s",
                        (last_id, upper_id, batch_size),
                    )
                rows = cursor.fetchall()
                points = []
                for row in rows:
                    try:
                        points.append(VectorPoint(
                            id=row[0],
                            vector=self._deserialize_vector(row[1]),
                            payload=json.loads(row[2]) if row[2] else {},
                        ))
                    except Exception as e:
                        raise RuntimeError(f"Failed to decode persisted vector {row[0]}") from e
                page_length = len(rows)
                if rows:
                    last_id = rows[-1][0]
                del rows
            except Error as e:
                raise RuntimeError(f"Failed to load vector page from MySQL: {e}") from e
            finally:
                self._finish_read(cursor)

            if not points:
                return
            yield points
            if page_length < batch_size:
                return

    @_synchronized_connection
    def load_changes_since(self, last_sync_time: float) -> VectorChangeSet:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(f"""
                SELECT `id`, `vector`, `payload`, `is_deleted`,
                       UNIX_TIMESTAMP(`gmt_modify`)
                FROM {TABLE_NAME}
                WHERE `gmt_modify` >= FROM_UNIXTIME(%s)
                ORDER BY `gmt_modify` ASC, `id` ASC
            """, (max(0.0, last_sync_time - 1.0),))

            upserts = []
            deleted_ids = []
            checkpoint = last_sync_time
            for row in cursor.fetchall():
                checkpoint = max(checkpoint, float(row[4] or 0.0))
                if bool(row[3]):
                    deleted_ids.append(row[0])
                    continue
                try:
                    vector = self._deserialize_vector(row[1])
                    payload = json.loads(row[2]) if row[2] else {}
                    upserts.append(VectorPoint(id=row[0], vector=vector, payload=payload))
                except Exception as e:
                    raise RuntimeError(f"Failed to decode persisted vector {row[0]}") from e

            return VectorChangeSet(
                upserts=upserts,
                deleted_ids=deleted_ids,
                checkpoint=checkpoint,
            )
        except Error as e:
            raise RuntimeError(f"Failed to load changes from MySQL: {e}") from e
        finally:
            self._finish_read(cursor)

    @_synchronized_connection
    def _change_watermark(self) -> tuple[float, str] | None:
        self._ensure_connection()
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                f"SELECT UNIX_TIMESTAMP(`gmt_modify`), `id` FROM {TABLE_NAME} "
                "ORDER BY `gmt_modify` DESC, `id` DESC LIMIT 1",
                (),
            )
            row = cursor.fetchone()
            return (float(row[0]), row[1]) if row else None
        except Error as e:
            raise RuntimeError(f"Failed to read MySQL change watermark: {e}") from e
        finally:
            self._finish_read(cursor)

    @_synchronized_pages
    def iter_change_batches_since(self, checkpoint: float, batch_size: int):
        """Page durable changes by (modification time, ID) without buffering all."""
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        floor = max(0.0, checkpoint - 1.0)
        upper_key = self._change_watermark()
        if upper_key is None:
            return
        last_key = None
        while True:
            self._ensure_connection()
            cursor = self._conn.cursor()
            try:
                sql = (
                    f"SELECT `id`, `vector`, `payload`, `is_deleted`, "
                    f"UNIX_TIMESTAMP(`gmt_modify`) FROM {TABLE_NAME} "
                    "WHERE `gmt_modify` >= FROM_UNIXTIME(%s) "
                )
                if last_key is None:
                    params = (floor,)
                else:
                    sql += (
                        "AND (`gmt_modify` > FROM_UNIXTIME(%s) OR "
                        "(`gmt_modify` = FROM_UNIXTIME(%s) AND `id` > %s)) "
                    )
                    params = (floor, last_key[0], last_key[0], last_key[1])
                sql += (
                    "AND (`gmt_modify` < FROM_UNIXTIME(%s) OR "
                    "(`gmt_modify` = FROM_UNIXTIME(%s) AND `id` <= %s)) "
                )
                params += (upper_key[0], upper_key[0], upper_key[1], batch_size)
                cursor.execute(sql + "ORDER BY `gmt_modify`, `id` LIMIT %s", params)
                rows = cursor.fetchall()
                upserts = []
                deleted_ids = []
                next_checkpoint = checkpoint
                for row in rows:
                    next_checkpoint = max(next_checkpoint, float(row[4] or 0.0))
                    if bool(row[3]):
                        deleted_ids.append(row[0])
                        continue
                    try:
                        upserts.append(VectorPoint(
                            id=row[0],
                            vector=self._deserialize_vector(row[1]),
                            payload=json.loads(row[2]) if row[2] else {},
                        ))
                    except Exception as e:
                        raise RuntimeError(f"Failed to decode persisted vector {row[0]}") from e
                page_length = len(rows)
                if rows:
                    last_key = (float(rows[-1][4] or 0.0), rows[-1][0])
                del rows
            except Error as e:
                raise RuntimeError(f"Failed to load vector changes from MySQL: {e}") from e
            finally:
                self._finish_read(cursor)

            if page_length == 0:
                return
            yield VectorChangeSet(
                upserts=upserts,
                deleted_ids=deleted_ids,
                checkpoint=next_checkpoint,
            )
            if page_length < batch_size:
                return

    @_synchronized_connection
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

    @_synchronized_connection
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

    @_synchronized_connection
    def exists(self, id: str) -> bool:
        self._ensure_connection()
        cursor = self._conn.cursor()

        try:
            cursor.execute(
                f"""
                SELECT 1 FROM {TABLE_NAME}
                WHERE `id` = %s AND `is_deleted` = 0 LIMIT 1
                """,
                (id,),
            )
            return cursor.fetchone() is not None
        except Error as e:
            raise RuntimeError(f"Failed to check existence: {e}") from e
        finally:
            self._finish_read(cursor)

    @_synchronized_connection
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
            self._finish_read(cursor)

    @_synchronized_connection
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
            self._finish_read(cursor)

    @_synchronized_connection
    def close(self) -> None:
        if self._conn is not None and self._conn.is_connected():
            self._conn.close()
            self._conn = None


__all__ = ["MySQLVectorPersistenceBackend"]
