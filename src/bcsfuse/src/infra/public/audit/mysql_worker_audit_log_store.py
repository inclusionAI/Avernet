"""
MySQL Worker Audit Log Store

MySQL implementation for production OSS deployments.

R12-Pool Fix: Replaced shared self._conn with MySQLConnectionPoolProvider for thread safety.
"""
import os
import json
import threading
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider


class MySQLWorkerAuditLogStore:
    """MySQL Worker Audit Log Store for OSS.

    R12-Pool: Thread-safe implementation using connection pool.
    """

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        **kwargs
    ):
        """Initialize MySQLWorkerAuditLogStore with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            **kwargs: Fallback MySQL connection params if pool not provided.
        """
        if connection_pool is None:
            # Fallback: create internal pool from env or kwargs
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider
            self._pool = MySQLConnectionPoolProvider(
                host=kwargs.get("host") or os.getenv("MYSQL_HOST", "localhost"),
                port=kwargs.get("port") or int(os.getenv("MYSQL_PORT", "3306")),
                user=kwargs.get("user") or os.getenv("MYSQL_USER", "root"),
                password=kwargs.get("password") or os.getenv("MYSQL_PASSWORD", ""),
                database=kwargs.get("database") or os.getenv("MYSQL_DATABASE", "bcsfuse"),
                pool_size=5,
            )
        else:
            self._pool = connection_pool

        self._schema_initialized = False
        self._schema_lock = threading.Lock()
        # Removed: self._conn (shared connection was not thread-safe)

    def _ensure_schema(self, conn):
        """Initialize schema on first use (thread-safe with double-check)."""
        if self._schema_initialized:
            return

        with self._schema_lock:
            if self._schema_initialized:
                return
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS worker_audit_log (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        worker_id VARCHAR(255),
                        event_type VARCHAR(100) NOT NULL,
                        event_data JSON,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_worker_id (worker_id),
                        INDEX idx_event_type (event_type),
                        INDEX idx_timestamp (timestamp)
                    )
                """)
                self._schema_initialized = True
            finally:
                cursor.close()

    def log(self, event_type: str, event_data: dict, timestamp: Optional[datetime] = None) -> bool:
        """Log an audit event.

        R12-Pool: Uses connection pool for thread safety.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO worker_audit_log (worker_id, event_type, event_data, timestamp)
                    VALUES (%s, %s, %s, %s)
                """, (
                    event_data.get("worker_id"),
                    event_type,
                    json.dumps(event_data),
                    timestamp or datetime.utcnow(),
                ))
                return True
            finally:
                cursor.close()
        finally:
            conn.close()

    def query(
        self, start_time: datetime, end_time: datetime,
        event_type: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        """Query audit logs by time range.

        R12-Pool: Uses connection pool for thread safety.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)
            cursor = conn.cursor(dictionary=True)
            try:
                if event_type:
                    cursor.execute("""
                        SELECT * FROM worker_audit_log
                        WHERE timestamp >= %s AND timestamp <= %s AND event_type = %s
                        ORDER BY timestamp DESC
                        LIMIT %s
                    """, (start_time, end_time, event_type, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM worker_audit_log
                        WHERE timestamp >= %s AND timestamp <= %s
                        ORDER BY timestamp DESC
                        LIMIT %s
                    """, (start_time, end_time, limit))

                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "worker_id": row["worker_id"],
                        "event_type": row["event_type"],
                        "event_data": json.loads(row["event_data"]) if row["event_data"] else {},
                        "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                    }
                    for row in rows
                ]
            finally:
                cursor.close()
        finally:
            conn.close()

    def get_by_worker(self, worker_id: str, limit: int = 100) -> List[dict]:
        """Get audit logs for a specific worker.

        R12-Pool: Uses connection pool for thread safety.
        """
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("""
                    SELECT * FROM worker_audit_log
                    WHERE worker_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (worker_id, limit))
                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "worker_id": row["worker_id"],
                        "event_type": row["event_type"],
                        "event_data": json.loads(row["event_data"]) if row["event_data"] else {},
                        "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
                    }
                    for row in rows
                ]
            finally:
                cursor.close()
        finally:
            conn.close()

    def close(self):
        """Close connection pool if we own it."""
        if self._pool:
            self._pool.close()