"""
MySQL Worker Runtime State Store (production-schema aligned)

Aligns with the internal production DB schema:
    bcsfuse_worker_runtime_states (
        worker_id VARCHAR(128) PRIMARY KEY,
        runtime_state VARCHAR(32) NOT NULL DEFAULT 'offline',
        updated_by VARCHAR(128),
        gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

from src.domain.models.worker_runtime_state import WorkerRuntimeState

logger = logging.getLogger(__name__)


class MySQLWorkerRuntimeStateStore:
    """MySQL Worker Runtime State Store for production OSS deployments.

    Thread Safety:
        - Uses connection pool for thread-safe access
        - Each method borrows connection from pool
        - Connection returned to pool after use
    """

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        **kwargs,
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            **kwargs: Fallback MySQL connection parameters.
        """
        if connection_pool is None:
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            self._pool = MySQLConnectionPoolProvider(
                host=kwargs.get("host") or os.getenv("MYSQL_HOST", "localhost"),
                port=kwargs.get("port") or int(os.getenv("MYSQL_PORT", "3306")),
                user=kwargs.get("user") or os.getenv("MYSQL_USER", ""),
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
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
        if self._schema_initialized:
            return

        with self._schema_lock:
            if self._schema_initialized:
                return

            conn = self._pool.get_connection()
            try:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS bcsfuse_worker_runtime_states (
                            worker_id VARCHAR(128) PRIMARY KEY,
                            runtime_state VARCHAR(32) NOT NULL DEFAULT 'offline',
                            updated_by VARCHAR(128),
                            gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            INDEX idx_bcsfuse_worker_runtime_states_state (runtime_state)
                        )
                    """)
                finally:
                    cursor.close()
                conn.commit()
                self._schema_initialized = True
                logger.info(
                    "[MySQLWorkerRuntimeStateStore] Schema initialized successfully"
                )
            finally:
                conn.close()

    @staticmethod
    def _resolve_state_value(runtime_state) -> str:
        """Resolve runtime_state enum/dict/str to string value."""
        if isinstance(runtime_state, WorkerRuntimeState):
            return runtime_state.value
        if isinstance(runtime_state, dict):
            state = runtime_state.get("state")
            if isinstance(state, WorkerRuntimeState):
                return state.value
            if state is not None:
                return str(state)
            return WorkerRuntimeState.OFFLINE.value
        if runtime_state is None:
            return WorkerRuntimeState.OFFLINE.value
        return str(runtime_state)

    def get_runtime_state(self, worker_id: str) -> Optional[WorkerRuntimeState]:
        """Get runtime state for worker."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT runtime_state FROM bcsfuse_worker_runtime_states WHERE worker_id = %s",
                    (worker_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return WorkerRuntimeState(row["runtime_state"])
            finally:
                cursor.close()
        finally:
            conn.close()

    def set_runtime_state(
        self,
        worker_id: str,
        runtime_state,
        updated_by: Optional[str] = None,
    ) -> bool:
        """Set runtime state for worker.

        Args:
            worker_id: Worker ID
            runtime_state: WorkerRuntimeState enum, dict with 'state', or string
            updated_by: Who updated this state
        """
        state_value = self._resolve_state_value(runtime_state)
        now = datetime.utcnow()

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO bcsfuse_worker_runtime_states
                        (worker_id, runtime_state, updated_by, gmt_create, gmt_modify)
                    VALUES (%s, %s, %s, %s, %s)
                    AS new_rts
                    ON DUPLICATE KEY UPDATE
                        runtime_state = new_rts.runtime_state,
                        updated_by = new_rts.updated_by,
                        gmt_modify = new_rts.gmt_modify
                """, (
                    worker_id,
                    state_value,
                    updated_by,
                    now,
                    now,
                ))
                conn.commit()

                logger.debug(
                    "[MySQLWorkerRuntimeStateStore] set_runtime_state() completed: worker_id=%s",
                    worker_id,
                )
                return True
            finally:
                cursor.close()
        finally:
            conn.close()

    def batch_get_runtime_states(
        self,
        worker_ids: list[str],
    ) -> dict[str, WorkerRuntimeState]:
        """Batch get runtime states."""
        if not worker_ids:
            return {}

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                placeholders = ", ".join(["%s"] * len(worker_ids))
                cursor.execute(
                    f"""
                        SELECT worker_id, runtime_state
                        FROM bcsfuse_worker_runtime_states
                        WHERE worker_id IN ({placeholders})
                    """,
                    worker_ids,
                )
                rows = cursor.fetchall()
                return {
                    row["worker_id"]: WorkerRuntimeState(row["runtime_state"])
                    for row in rows
                }
            finally:
                cursor.close()
        finally:
            conn.close()

    def count_by_state(self, runtime_state) -> int:
        """Count workers by runtime state."""
        state_value = self._resolve_state_value(runtime_state)

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM bcsfuse_worker_runtime_states WHERE runtime_state = %s",
                    (state_value,),
                )
                return cursor.fetchone()[0]
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
