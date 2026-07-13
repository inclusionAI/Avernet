"""
MySQL Worker Runtime State Store (Connection Pool Version)

MySQL implementation for production OSS deployments.

R12-Pool-4 Fix:
- Replaced shared self._connection with MySQLConnectionPoolProvider
- Each method gets its own connection from pool
- Connection returned to pool after use (conn.close())
- Thread-safe by design (pool handles connection distribution)
- No more Fatal Python error from concurrent MySQL connector access
"""
import os
import logging
import threading
from typing import Optional, TYPE_CHECKING
import mysql.connector

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

logger = logging.getLogger(__name__)


class MySQLWorkerRuntimeStateStore:
    """MySQL Worker Runtime State Store for OSS (Connection Pool Version).

    Suitable for production deployments with MySQL database.

    Thread Safety:
        - Uses connection pool for thread-safe access
        - Each method borrows connection from pool
        - Connection returned to pool after use
        - No shared connection state
    """

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        **kwargs
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            **kwargs: Fallback MySQL connection parameters (host, port, user, password, database).
        """
        if connection_pool is None:
            # Fallback: create pool internally
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            self._pool = MySQLConnectionPoolProvider(
                host=kwargs.get("host") or os.getenv("MYSQL_HOST", "localhost"),
                port=kwargs.get("port") or int(os.getenv("MYSQL_PORT", "3306")),
                user=kwargs.get("user") or os.getenv("MYSQL_USER", "root"),
                password=kwargs.get("password") or os.getenv("MYSQL_PASSWORD", ""),
                database=kwargs.get("database") or os.getenv("MYSQL_DATABASE", "bcsfuse"),
            )
            logger.info(
                "[MySQLWorkerRuntimeStateStore] Created internal connection pool (fallback mode)"
            )
        else:
            self._pool = connection_pool
            logger.info(
                "[MySQLWorkerRuntimeStateStore] Using injected connection pool"
            )

        self._schema_initialized = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self, conn) -> None:
        """Ensure database schema exists."""
        if self._schema_initialized:
            return

        with self._schema_lock:
            if self._schema_initialized:
                return

            cursor = conn.cursor()

            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS worker_runtime_state (
                        worker_id VARCHAR(255) PRIMARY KEY,
                        state VARCHAR(50),
                        heartbeat_at TIMESTAMP NULL,
                        metadata JSON,
                        updated_by VARCHAR(255),
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)

                self._schema_initialized = True
                logger.info(
                    "[MySQLWorkerRuntimeStateStore] Schema initialized successfully"
                )

            finally:
                cursor.close()

    def get_runtime_state(self, worker_id: str) -> Optional[dict]:
        """Get runtime state for worker (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT * FROM worker_runtime_state WHERE worker_id = %s",
                    (worker_id,)
                )
                row = cursor.fetchone()

                logger.debug(
                    f"[MySQLWorkerRuntimeStateStore] get_runtime_state() completed: "
                    f"worker_id={worker_id}, found={row is not None}"
                )

                return row

            finally:
                cursor.close()

        finally:
            conn.close()

    def set_runtime_state(self, worker_id: str, runtime_state, updated_by: Optional[str] = None) -> bool:
        """Set runtime state for worker (thread-safe with connection pool).

        Args:
            worker_id: Worker ID
            runtime_state: Can be WorkerRuntimeState enum or dict with 'state', 'heartbeat_at', 'metadata'
            updated_by: Who updated this state
        """
        # Handle both enum and dict
        from src.domain.models.worker_runtime_state import WorkerRuntimeState

        if isinstance(runtime_state, WorkerRuntimeState):
            # Enum: convert to dict
            state_value = runtime_state.value
            heartbeat_at = None
            metadata = {}
        elif isinstance(runtime_state, dict):
            # Dict: extract values
            state_value = runtime_state.get("state")
            if hasattr(state_value, "value"):
                state_value = state_value.value
            heartbeat_at = runtime_state.get("heartbeat_at")
            metadata = runtime_state.get("metadata", {})
        else:
            raise ValueError(f"runtime_state must be WorkerRuntimeState enum or dict, got {type(runtime_state)}")

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                import json
                metadata_json = json.dumps(metadata) if metadata is not None else "{}"

                cursor.execute("""
                    INSERT INTO worker_runtime_state (worker_id, state, heartbeat_at, metadata, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE state=%s, heartbeat_at=%s, metadata=%s, updated_by=%s
                """, (
                    worker_id,
                    state_value,
                    heartbeat_at,
                    metadata_json,
                    updated_by,
                    state_value,
                    heartbeat_at,
                    metadata_json,
                    updated_by,
                ))

                logger.debug(
                    f"[MySQLWorkerRuntimeStateStore] set_runtime_state() completed: worker_id={worker_id}"
                )

                return True

            finally:
                cursor.close()

        finally:
            conn.close()

    def batch_get_runtime_states(self, worker_ids: list) -> dict:
        """Batch get runtime states (thread-safe with connection pool)."""
        if not worker_ids:
            return {}

        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                placeholders = ",".join(["%s"] * len(worker_ids))
                cursor.execute(
                    f"SELECT * FROM worker_runtime_state WHERE worker_id IN ({placeholders})",
                    worker_ids
                )
                rows = cursor.fetchall()

                logger.debug(
                    f"[MySQLWorkerRuntimeStateStore] batch_get_runtime_states() completed: "
                    f"requested={len(worker_ids)}, found={len(rows)}"
                )

                return {row["worker_id"]: row for row in rows}

            finally:
                cursor.close()

        finally:
            conn.close()

    def count_by_state(self, runtime_state: str) -> int:
        """Count workers by runtime state (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM worker_runtime_state WHERE state = %s",
                    (runtime_state,)
                )
                count = cursor.fetchone()[0]

                logger.debug(
                    f"[MySQLWorkerRuntimeStateStore] count_by_state() completed: "
                    f"state={runtime_state}, count={count}"
                )

                return count

            finally:
                cursor.close()

        finally:
            conn.close()

    def close(self) -> None:
        """Close connection pool (for application shutdown)."""
        if self._pool:
            self._pool.close()
            logger.info("[MySQLWorkerRuntimeStateStore] Connection pool closed")


__all__ = ["MySQLWorkerRuntimeStateStore"]