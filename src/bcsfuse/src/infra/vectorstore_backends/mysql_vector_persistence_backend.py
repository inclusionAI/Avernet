"""
MySQL Vector Persistence Backend

Vector data MySQL persistence backend implementation.

For production OSS deployments with MySQL database.

Design Principles:
- Uses existing MySQL connection patterns (mysql.connector)
- Follows SQLiteVectorPersistenceBackend protocol
- Supports vector data persistence with rebuild capability
- No fallback to SQLite/InMemory
- Lazy connection initialization
- Comprehensive diagnostic logging
"""

from __future__ import annotations

import json
import logging
import os
import time
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
    mask_url,
)

logger = logging.getLogger(__name__)


# MySQL schema definition for vector points
CREATE_VECTOR_POINTS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_vector_points (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    collection_name VARCHAR(255) NOT NULL DEFAULT 'default',
    point_id VARCHAR(255) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    vector_dimension INT NOT NULL,
    distance_metric VARCHAR(50) NOT NULL DEFAULT 'Cosine',
    vector_data LONGBLOB NOT NULL,
    payload_json JSON NOT NULL DEFAULT ('{}'),
    worker_id VARCHAR(255),
    profile_id VARCHAR(255),
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_collection_point (collection_name, point_id),
    UNIQUE KEY uk_collection_external (collection_name, external_id),
    INDEX idx_collection_name (collection_name),
    INDEX idx_external_id (external_id),
    INDEX idx_worker_id (worker_id),
    INDEX idx_profile_id (profile_id),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


class MySQLVectorPersistenceBackend:
    """
    MySQL Vector Persistence Backend

    For production OSS deployments with MySQL database.

    S30C Implementation:
    - Vector data persistence following VectorPersistenceBackend protocol
    - No fallback to other backends
    - Lazy connection initialization
    - Automatic schema creation
    - Comprehensive diagnostic logging
    """

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
        """Initialize MySQL vector persistence backend.

        Args:
            host: MySQL host (default: MYSQL_HOST env var or localhost).
            port: MySQL port (default: MYSQL_PORT env var or 3306).
            user: MySQL user (default: MYSQL_USER env var).
            password: MySQL password (default: MYSQL_PASSWORD env var).
            database: MySQL database (default: MYSQL_DATABASE env var).
            collection_name: Default collection name for vectors.
            vector_dimension: Expected vector dimension.
            distance_metric: Distance metric (Cosine, Euclid, Dot).
        """
        self.host = host or os.getenv("MYSQL_HOST", "localhost")
        self.port = port or int(os.getenv("MYSQL_PORT", "3306"))
        self.user = user or os.getenv("MYSQL_USER", "root")
        self.password = password or os.getenv("MYSQL_PASSWORD", "")
        self.database = database or os.getenv("MYSQL_DATABASE", "bcsfuse")
        self.collection_name = collection_name
        self.vector_dimension = vector_dimension
        self.distance_metric = distance_metric

        self._conn = None
        self._schema_initialized = False
        # Lazy initialization - don't connect until first method call

    def _ensure_connection(self) -> None:
        """Ensure database connection (lazy initialization)."""
        if self._conn is None or not self._conn.is_connected():
            start_time = time.time()
            component = "mysql_vector_persistence_backend"
            target_resource = "bcsfuse_vector_points"

            # Log connection attempt
            log_storage_event(
                logger,
                logging.DEBUG,
                "mysql_vector_backend_init_start",
                component=component,
                operation="init_connection",
                validation_phase="connection",
                backend="mysql",
                target_resource=target_resource,
                host_masked=mask_host(self.host),
                port=self.port,
                database=self.database,
                user_masked=mask_user(self.user),
                collection_name=self.collection_name,
                vector_dimension=self.vector_dimension,
                distance_metric=self.distance_metric,
            )

            try:
                self._conn = mysql.connector.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                    autocommit=False,
                )

                duration_ms = (time.time() - start_time) * 1000

                # Log connection success
                log_storage_event(
                    logger,
                    logging.INFO,
                    "mysql_vector_backend_init_success",
                    component=component,
                    operation="init_connection",
                    validation_phase="connection",
                    backend="mysql",
                    target_resource=target_resource,
                    duration_ms=duration_ms,
                )

                # Initialize schema if not done
                if not self._schema_initialized:
                    self._ensure_schema()
                    self._schema_initialized = True

            except Error as e:
                duration_ms = (time.time() - start_time) * 1000

                # Log connection failure
                log_storage_error(
                    logger,
                    "mysql_vector_backend_init_failure",
                    component=component,
                    operation="init_connection",
                    validation_phase="connection",
                    backend="mysql",
                    target_resource=target_resource,
                    error=e,
                    duration_ms=duration_ms,
                )

                raise RuntimeError(
                    f"Failed to connect to MySQL database at "
                    f"{mask_host(self.host)}:{self.port}/{self.database}: {e}"
                ) from e

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
        if self._conn is None or not self._conn.is_connected():
            return

        start_time = time.time()
        component = "mysql_vector_persistence_backend"

        # Log schema check start
        log_storage_event(
            logger,
            logging.DEBUG,
            "mysql_vector_schema_check_start",
            component=component,
            operation="check_schema",
            validation_phase="schema_init",
            backend="mysql",
            target_resource="bcsfuse_vector_points",
        )

        try:
            cursor = self._conn.cursor()
            cursor.execute(CREATE_VECTOR_POINTS_TABLE)
            self._conn.commit()
            cursor.close()

            duration_ms = (time.time() - start_time) * 1000

            # Log schema check success
            log_storage_event(
                logger,
                logging.INFO,
                "mysql_vector_schema_check_success",
                component=component,
                operation="check_schema",
                validation_phase="schema_init",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                duration_ms=duration_ms,
            )

        except Error as e:
            duration_ms = (time.time() - start_time) * 1000

            # Log schema check failure
            log_storage_error(
                logger,
                "mysql_vector_schema_check_failure",
                component=component,
                operation="check_schema",
                validation_phase="schema_init",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                error=e,
                duration_ms=duration_ms,
            )

            raise RuntimeError(f"Failed to initialize MySQL schema: {e}") from e

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
        import struct

        # Try numpy binary format first (float32 array)
        try:
            arr = np.frombuffer(data, dtype=np.float32)
            return arr.tolist()
        except (ValueError, TypeError):
            pass

        # Legacy: pickle format from older versions — log warning
        import pickle
        logger.warning(
            "Deserializing legacy pickle vector data — "
            "re-save to migrate to safe numpy format"
        )
        return pickle.loads(data)

    def _extract_external_id(self, point_id: str, payload: dict) -> str:
        """Extract external ID from point_id or payload.

        If payload contains '_external_id', use it as external_id.
        Otherwise, assume point_id is the external_id.

        Args:
            point_id: Qdrant point ID (may be UUID).
            payload: Payload dictionary.

        Returns:
            External ID (logical business ID).
        """
        if payload and "_external_id" in payload:
            return payload["_external_id"]
        return point_id

    def _extract_worker_profile_ids(self, payload: dict) -> tuple[Optional[str], Optional[str]]:
        """Extract worker_id and profile_id from payload.

        Args:
            payload: Payload dictionary.

        Returns:
            Tuple of (worker_id, profile_id), either may be None.
        """
        worker_id = payload.get("worker_id") or payload.get("staff_id")
        profile_id = payload.get("profile_id") or payload.get("profile_key")
        return worker_id, profile_id

    def save(self, point: VectorPoint) -> None:
        """Save a single vector point.

        Args:
            point: Vector point to save.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        self._ensure_connection()

        start_time = time.time()
        component = "mysql_vector_persistence_backend"

        external_id = self._extract_external_id(point.id, point.payload)
        worker_id, profile_id = self._extract_worker_profile_ids(point.payload)
        vector_blob = self._serialize_vector(point.vector)
        payload_json = json.dumps(point.payload)

        # Log write start
        log_storage_event(
            logger,
            logging.DEBUG,
            "mysql_vector_write_start",
            component=component,
            operation="save",
            validation_phase="write",
            backend="mysql",
            target_resource="bcsfuse_vector_points",
            collection_name=self.collection_name,
            point_id_type="uuid" if len(point.id) == 36 else "external",
            external_id_present=bool(external_id),
            vector_dimension=len(point.vector),
            payload_filter_keys=list(point.payload.keys()) if point.payload else [],
        )

        try:
            cursor = self._conn.cursor()

            # Use UPSERT pattern (INSERT ... ON DUPLICATE KEY UPDATE)
            cursor.execute("""
                INSERT INTO bcsfuse_vector_points
                (collection_name, point_id, external_id, vector_dimension, distance_metric,
                 vector_data, payload_json, worker_id, profile_id, version, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
                ON DUPLICATE KEY UPDATE
                    external_id = VALUES(external_id),
                    vector_dimension = VALUES(vector_dimension),
                    distance_metric = VALUES(distance_metric),
                    vector_data = VALUES(vector_data),
                    payload_json = VALUES(payload_json),
                    worker_id = VALUES(worker_id),
                    profile_id = VALUES(profile_id),
                    version = version + 1,
                    updated_at = NOW()
            """, (
                self.collection_name, point.id, external_id, len(point.vector),
                self.distance_metric, vector_blob, payload_json, worker_id, profile_id
            ))

            self._conn.commit()
            cursor.close()

            duration_ms = (time.time() - start_time) * 1000

            # Log write success
            log_storage_event(
                logger,
                logging.INFO,
                "mysql_vector_write_success",
                component=component,
                operation="save",
                validation_phase="write",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                collection_name=self.collection_name,
                duration_ms=duration_ms,
                point_id_type="uuid" if len(point.id) == 36 else "external",
                external_id_present=bool(external_id),
            )

        except Error as e:
            duration_ms = (time.time() - start_time) * 1000

            # Log write failure
            log_storage_error(
                logger,
                "mysql_vector_write_failure",
                component=component,
                operation="save",
                validation_phase="write",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                error=e,
                duration_ms=duration_ms,
            )

            raise RuntimeError(f"Failed to save vector to MySQL: {e}") from e

    def save_batch(self, points: list[VectorPoint]) -> None:
        """Save multiple vector points in a batch.

        Args:
            points: List of vector points to save.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        if not points:
            return

        self._ensure_connection()

        start_time = time.time()
        component = "mysql_vector_persistence_backend"

        # Log batch write start
        log_storage_event(
            logger,
            logging.DEBUG,
            "mysql_vector_write_start",
            component=component,
            operation="save_batch",
            validation_phase="write",
            backend="mysql",
            target_resource="bcsfuse_vector_points",
            collection_name=self.collection_name,
            batch_size=len(points),
        )

        try:
            cursor = self._conn.cursor()

            for point in points:
                external_id = self._extract_external_id(point.id, point.payload)
                worker_id, profile_id = self._extract_worker_profile_ids(point.payload)
                vector_blob = self._serialize_vector(point.vector)
                payload_json = json.dumps(point.payload)

                cursor.execute("""
                    INSERT INTO bcsfuse_vector_points
                    (collection_name, point_id, external_id, vector_dimension, distance_metric,
                     vector_data, payload_json, worker_id, profile_id, version, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
                    ON DUPLICATE KEY UPDATE
                        external_id = VALUES(external_id),
                        vector_dimension = VALUES(vector_dimension),
                        distance_metric = VALUES(distance_metric),
                        vector_data = VALUES(vector_data),
                        payload_json = VALUES(payload_json),
                        worker_id = VALUES(worker_id),
                        profile_id = VALUES(profile_id),
                        version = version + 1,
                        updated_at = NOW()
                """, (
                    self.collection_name, point.id, external_id, len(point.vector),
                    self.distance_metric, vector_blob, payload_json, worker_id, profile_id
                ))

            self._conn.commit()
            cursor.close()

            duration_ms = (time.time() - start_time) * 1000

            # Log batch write success
            log_storage_event(
                logger,
                logging.INFO,
                "mysql_vector_write_success",
                component=component,
                operation="save_batch",
                validation_phase="write",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                collection_name=self.collection_name,
                duration_ms=duration_ms,
                batch_size=len(points),
            )

        except Error as e:
            duration_ms = (time.time() - start_time) * 1000

            # Log batch write failure
            log_storage_error(
                logger,
                "mysql_vector_write_failure",
                component=component,
                operation="save_batch",
                validation_phase="write",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                error=e,
                duration_ms=duration_ms,
            )

            raise RuntimeError(f"Failed to save batch vectors to MySQL: {e}") from e

    def load_all(self) -> list[VectorPoint]:
        """Load all vector points for rebuild.

        Returns:
            List of all vector points.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        self._ensure_connection()

        start_time = time.time()
        component = "mysql_vector_persistence_backend"

        # Log load start
        log_storage_event(
            logger,
            logging.DEBUG,
            "mysql_vector_load_all_start",
            component=component,
            operation="load_all",
            validation_phase="read",
            backend="mysql",
            target_resource="bcsfuse_vector_points",
            collection_name=self.collection_name,
        )

        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT point_id, vector_data, payload_json
                FROM bcsfuse_vector_points
                WHERE collection_name = %s
            """, (self.collection_name,))

            points = []
            for row in cursor.fetchall():
                try:
                    vector = self._deserialize_vector(row[1])
                    payload = json.loads(row[2])
                    points.append(VectorPoint(
                        id=row[0],
                        vector=vector,
                        payload=payload,
                    ))
                except Exception as e:
                    logger.warning(
                        "[MySQLVectorBackend] Failed to load vector %s: %s",
                        row[0], e
                    )

            cursor.close()

            duration_ms = (time.time() - start_time) * 1000

            # Log load success
            log_storage_event(
                logger,
                logging.INFO,
                "mysql_vector_load_all_success",
                component=component,
                operation="load_all",
                validation_phase="read",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                collection_name=self.collection_name,
                duration_ms=duration_ms,
                vector_count=len(points),
            )

            return points

        except Error as e:
            duration_ms = (time.time() - start_time) * 1000

            # Log load failure
            log_storage_error(
                logger,
                "mysql_vector_load_all_failure",
                component=component,
                operation="load_all",
                validation_phase="read",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                error=e,
                duration_ms=duration_ms,
            )

            raise RuntimeError(f"Failed to load vectors from MySQL: {e}") from e

    def delete(self, id: str) -> bool:
        """Delete a single vector point.

        Args:
            id: Vector point ID.

        Returns:
            True if deleted, False if not found.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        self._ensure_connection()

        start_time = time.time()
        component = "mysql_vector_persistence_backend"

        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                DELETE FROM bcsfuse_vector_points
                WHERE collection_name = %s AND point_id = %s
            """, (self.collection_name, id))
            self._conn.commit()
            deleted = cursor.rowcount > 0
            cursor.close()

            duration_ms = (time.time() - start_time) * 1000

            if deleted:
                logger.debug(
                    "[MySQLVectorBackend] Deleted vector: id=%s, duration_ms=%.2f",
                    id, duration_ms
                )

            return deleted

        except Error as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "mysql_vector_delete_failure",
                component=component,
                operation="delete",
                validation_phase="write",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                error=e,
                duration_ms=duration_ms,
            )

            raise RuntimeError(f"Failed to delete vector from MySQL: {e}") from e

    def delete_batch(self, ids: list[str]) -> int:
        """Delete multiple vector points.

        Args:
            ids: List of vector point IDs.

        Returns:
            Number of vectors deleted.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        if not ids:
            return 0

        self._ensure_connection()

        start_time = time.time()
        component = "mysql_vector_persistence_backend"

        try:
            cursor = self._conn.cursor()
            placeholders = ", ".join(["%s" for _ in ids])
            cursor.execute(f"""
                DELETE FROM bcsfuse_vector_points
                WHERE collection_name = %s AND point_id IN ({placeholders})
            """, [self.collection_name] + ids)
            self._conn.commit()
            deleted = cursor.rowcount
            cursor.close()

            duration_ms = (time.time() - start_time) * 1000

            logger.info(
                "[MySQLVectorBackend] Deleted %d vectors, duration_ms=%.2f",
                deleted, duration_ms
            )

            return deleted

        except Error as e:
            duration_ms = (time.time() - start_time) * 1000

            log_storage_error(
                logger,
                "mysql_vector_delete_batch_failure",
                component=component,
                operation="delete_batch",
                validation_phase="write",
                backend="mysql",
                target_resource="bcsfuse_vector_points",
                error=e,
                duration_ms=duration_ms,
            )

            raise RuntimeError(f"Failed to delete batch vectors from MySQL: {e}") from e

    def exists(self, id: str) -> bool:
        """Check if a vector point exists.

        Args:
            id: Vector point ID.

        Returns:
            True if exists, False otherwise.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        self._ensure_connection()

        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT 1 FROM bcsfuse_vector_points
                WHERE collection_name = %s AND point_id = %s
                LIMIT 1
            """, (self.collection_name, id))
            exists = cursor.fetchone() is not None
            cursor.close()
            return exists

        except Error as e:
            logger.error("[MySQLVectorBackend] Failed to check existence: %s", e)
            raise RuntimeError(f"Failed to check vector existence in MySQL: {e}") from e

    def count(self) -> int:
        """Get total vector point count.

        Returns:
            Number of vector points.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        self._ensure_connection()

        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM bcsfuse_vector_points
                WHERE collection_name = %s
            """, (self.collection_name,))
            count = cursor.fetchone()[0]
            cursor.close()
            return count

        except Error as e:
            logger.error("[MySQLVectorBackend] Failed to get count: %s", e)
            raise RuntimeError(f"Failed to count vectors in MySQL: {e}") from e

    def get_last_modified_time(self) -> float:
        """Get last modified timestamp.

        Returns:
            Unix timestamp (seconds), or 0 if no data.

        Raises:
            RuntimeError: If MySQL operation fails.
        """
        self._ensure_connection()

        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT updated_at FROM bcsfuse_vector_points
                WHERE collection_name = %s
                ORDER BY updated_at DESC
                LIMIT 1
            """, (self.collection_name,))
            row = cursor.fetchone()
            cursor.close()

            if row is None:
                return 0.0

            # Convert datetime to timestamp
            if isinstance(row[0], datetime):
                return row[0].timestamp()
            return 0.0

        except Error as e:
            logger.error("[MySQLVectorBackend] Failed to get last modified time: %s", e)
            raise RuntimeError(f"Failed to get last modified time from MySQL: {e}") from e

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None and self._conn.is_connected():
            self._conn.close()
            self._conn = None
            logger.info("[MySQLVectorBackend] Connection closed")


__all__ = ["MySQLVectorPersistenceBackend"]