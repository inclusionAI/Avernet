"""
MySQL Worker Audit Log Store (production-schema aligned)

Aligns with the internal production DB schema:
    bcsfuse_worker_audit_logs (
        id VARCHAR(128) PRIMARY KEY,
        worker_id VARCHAR(128) NOT NULL,
        action VARCHAR(64) NOT NULL,
        old_value JSON,
        new_value JSON,
        source_type VARCHAR(32) NOT NULL DEFAULT 'api',
        source_ref VARCHAR(255),
        performed_by VARCHAR(128),
        performed_at TIMESTAMP NOT NULL,
        gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_bcsfuse_worker_audit_logs_worker_id (worker_id),
        INDEX idx_bcsfuse_worker_audit_logs_action (action),
        INDEX idx_bcsfuse_worker_audit_logs_performed_at (performed_at)
    )
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction

logger = logging.getLogger(__name__)


class MySQLWorkerAuditLogStore:
    """MySQL Worker Audit Log Store for production OSS deployments."""

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
        else:
            self._pool = connection_pool

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
                        CREATE TABLE IF NOT EXISTS bcsfuse_worker_audit_logs (
                            id VARCHAR(128) PRIMARY KEY,
                            worker_id VARCHAR(128) NOT NULL,
                            action VARCHAR(64) NOT NULL,
                            old_value JSON,
                            new_value JSON,
                            source_type VARCHAR(32) NOT NULL DEFAULT 'api',
                            source_ref VARCHAR(255),
                            performed_by VARCHAR(128),
                            performed_at TIMESTAMP NOT NULL,
                            gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            INDEX idx_bcsfuse_worker_audit_logs_worker_id (worker_id),
                            INDEX idx_bcsfuse_worker_audit_logs_action (action),
                            INDEX idx_bcsfuse_worker_audit_logs_performed_at (performed_at)
                        )
                    """)
                finally:
                    cursor.close()
                conn.commit()
                self._schema_initialized = True
                logger.info("[MySQLWorkerAuditLogStore] Schema initialized successfully")
            finally:
                conn.close()

    def _row_to_audit_log(self, row: dict) -> WorkerAuditLog:
        """Convert database row to WorkerAuditLog."""
        return WorkerAuditLog(
            id=row["id"],
            worker_id=row["worker_id"],
            action=WorkerAuditAction(row["action"]),
            old_value=row["old_value"] if isinstance(row["old_value"], str) else json.dumps(row["old_value"]) if row["old_value"] else None,
            new_value=row["new_value"] if isinstance(row["new_value"], str) else json.dumps(row["new_value"]) if row["new_value"] else None,
            source_type=row["source_type"],
            source_ref=row["source_ref"],
            performed_by=row["performed_by"],
            performed_at=row["performed_at"],
        )

    def append_log(self, audit_log: WorkerAuditLog) -> None:
        """Append an audit log."""
        now = datetime.utcnow()
        performed_at = audit_log.performed_at or now

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO bcsfuse_worker_audit_logs (
                        id, worker_id, action, old_value, new_value,
                        source_type, source_ref, performed_by, performed_at,
                        gmt_create, gmt_modify
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    audit_log.id,
                    audit_log.worker_id,
                    audit_log.action.value,
                    audit_log.old_value,
                    audit_log.new_value,
                    audit_log.source_type.value,
                    audit_log.source_ref,
                    audit_log.performed_by,
                    performed_at,
                    now,
                    now,
                ))
                conn.commit()
            finally:
                cursor.close()
        finally:
            conn.close()

    def list_logs(
        self,
        worker_id: Optional[str] = None,
        actions: Optional[List[WorkerAuditAction]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[WorkerAuditLog]:
        """List audit logs with optional filters."""
        sql = "SELECT * FROM bcsfuse_worker_audit_logs WHERE 1=1"
        params = []

        if worker_id:
            sql += " AND worker_id = %s"
            params.append(worker_id)

        if actions:
            placeholders = ", ".join(["%s"] * len(actions))
            sql += f" AND action IN ({placeholders})"
            params.extend([a.value for a in actions])

        sql += " ORDER BY performed_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                return [self._row_to_audit_log(row) for row in rows]
            finally:
                cursor.close()
        finally:
            conn.close()

    def get_latest_log(self, worker_id: str) -> Optional[WorkerAuditLog]:
        """Get latest audit log for worker."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM bcsfuse_worker_audit_logs WHERE worker_id = %s ORDER BY performed_at DESC LIMIT 1",
                    (worker_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_audit_log(row)
            finally:
                cursor.close()
        finally:
            conn.close()

    # =========================================================
    # Legacy dict-compatible API
    # =========================================================

    def log(self, event_type: str, event_data: dict, timestamp: Optional[datetime] = None) -> bool:
        """Log an audit event (legacy dict-compatible API)."""
        from src.domain.models.worker_source_info import WorkerSourceType

        audit_log = WorkerAuditLog(
            id=event_data.get("id") or f"audit_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{event_data.get('worker_id', 'unknown')}",
            worker_id=event_data.get("worker_id", ""),
            action=WorkerAuditAction(event_type) if event_type in [a.value for a in WorkerAuditAction] else WorkerAuditAction.UPDATED,
            old_value=json.dumps(event_data.get("old_value")) if event_data.get("old_value") is not None else None,
            new_value=json.dumps(event_data.get("new_value")) if event_data.get("new_value") is not None else None,
            source_type=WorkerSourceType(event_data.get("source_type", "api")),
            source_ref=event_data.get("source_ref"),
            performed_by=event_data.get("performed_by"),
            performed_at=timestamp or datetime.utcnow(),
        )
        self.append_log(audit_log)
        return True

    def query(
        self,
        start_time: datetime,
        end_time: datetime,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Query audit logs by time range (legacy dict-compatible API)."""
        sql = "SELECT * FROM bcsfuse_worker_audit_logs WHERE performed_at >= %s AND performed_at <= %s"
        params = [start_time, end_time]

        if event_type:
            sql += " AND action = %s"
            params.append(event_type)

        sql += " ORDER BY performed_at DESC LIMIT %s"
        params.append(limit)

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "worker_id": row["worker_id"],
                        "event_type": row["action"],
                        "event_data": self._build_event_data(row),
                        "timestamp": row["performed_at"].isoformat() if row["performed_at"] else None,
                    }
                    for row in rows
                ]
            finally:
                cursor.close()
        finally:
            conn.close()

    def get_by_worker(self, worker_id: str, limit: int = 100) -> List[dict]:
        """Get audit logs for worker (legacy dict-compatible API)."""
        logs = self.list_logs(worker_id=worker_id, limit=limit)
        return [
            {
                "id": log.id,
                "worker_id": log.worker_id,
                "event_type": log.action.value,
                "event_data": self._build_event_data_from_log(log),
                "timestamp": log.performed_at.isoformat() if log.performed_at else None,
            }
            for log in logs
        ]

    def _build_event_data(self, row: dict) -> dict:
        """Build event_data dict from row."""
        event_data = {
            "worker_id": row["worker_id"],
            "action": row["action"],
            "old_value": row["old_value"] if isinstance(row["old_value"], dict) else self._parse_json(row["old_value"]),
            "new_value": row["new_value"] if isinstance(row["new_value"], dict) else self._parse_json(row["new_value"]),
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "performed_by": row["performed_by"],
        }
        return {k: v for k, v in event_data.items() if v is not None}

    def _build_event_data_from_log(self, log: WorkerAuditLog) -> dict:
        """Build event_data dict from WorkerAuditLog."""
        event_data = {
            "worker_id": log.worker_id,
            "action": log.action.value,
            "old_value": self._parse_json(log.old_value),
            "new_value": self._parse_json(log.new_value),
            "source_type": log.source_type.value,
            "source_ref": log.source_ref,
            "performed_by": log.performed_by,
        }
        return {k: v for k, v in event_data.items() if v is not None}

    @staticmethod
    def _parse_json(value) -> dict:
        """Parse JSON string to dict."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}

    def close(self) -> None:
        """Close connection pool if we own it."""
        if self._pool:
            self._pool.close()


__all__ = ["MySQLWorkerAuditLogStore"]
