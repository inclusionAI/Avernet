"""
SQLite Worker Audit Log Store

Worker 审计日志的 SQLite 存储实现。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
from src.domain.models.worker_source_info import WorkerSourceType
from src.infra.adapters.sqlite_schema import init_schema


class SQLiteWorkerAuditLogStore:
    """
    Worker Audit Log SQLite 存储

    Stage 1 Phase 2 实现：
    - 追加型日志（只增不改）
    - 支持 :memory: 模式用于测试
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        初始化 SQLite 存储

        Args:
            db_path: 数据库路径，默认为内存模式
        """
        self._db_path = db_path
        # check_same_thread=False 允许在多线程环境中使用
        # timeout=30.0 允许等待30秒获取锁
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        # 启用 WAL 模式提高并发性能
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        init_schema(self._conn)

    def _row_to_audit_log(self, row: sqlite3.Row) -> WorkerAuditLog:
        """
        将数据库行转换为 WorkerAuditLog 模型

        Args:
            row: 数据库行

        Returns:
            WorkerAuditLog 模型
        """
        return WorkerAuditLog(
            id=row["id"],
            worker_id=row["worker_id"],
            action=WorkerAuditAction(row["action"]),
            old_value=row["old_value"],
            new_value=row["new_value"],
            source_type=WorkerSourceType(row["source_type"]),
            source_ref=row["source_ref"],
            performed_by=row["performed_by"],
            performed_at=datetime.fromisoformat(row["performed_at"]),
        )

    def append_log(self, audit_log: WorkerAuditLog) -> None:
        """
        记录审计日志

        Args:
            audit_log: 审计日志
        """
        now = datetime.utcnow().isoformat()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO bcsfuse_worker_audit_logs
            (id, worker_id, action, old_value, new_value, source_type, source_ref, performed_by, performed_at, gmt_create, gmt_modify)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_log.id,
                audit_log.worker_id,
                audit_log.action.value,
                audit_log.old_value,
                audit_log.new_value,
                audit_log.source_type.value,
                audit_log.source_ref,
                audit_log.performed_by,
                audit_log.performed_at.isoformat() if audit_log.performed_at else datetime.utcnow().isoformat(),
                now,
                now,
            ),
        )
        self._conn.commit()

    def list_logs(
        self,
        worker_id: Optional[str] = None,
        actions: Optional[list[WorkerAuditAction]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkerAuditLog]:
        """
        查询审计日志

        Args:
            worker_id: 过滤 Worker ID（可选）
            actions: 过滤动作类型（可选）
            limit: 分页限制
            offset: 分页偏移

        Returns:
            WorkerAuditLog 列表
        """
        sql = "SELECT * FROM bcsfuse_worker_audit_logs WHERE 1=1"
        params = []

        if worker_id:
            sql += " AND worker_id = ?"
            params.append(worker_id)

        if actions:
            placeholders = ", ".join(["?" for _ in actions])
            sql += f" AND action IN ({placeholders})"
            params.extend([a.value for a in actions])

        sql += " ORDER BY performed_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()

        return [self._row_to_audit_log(row) for row in rows]

    def get_latest_log(self, worker_id: str) -> Optional[WorkerAuditLog]:
        """
        获取最新日志

        Args:
            worker_id: Worker ID

        Returns:
            WorkerAuditLog 或 None
        """
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM bcsfuse_worker_audit_logs
            WHERE worker_id = ?
            ORDER BY performed_at DESC
            LIMIT 1
            """,
            (worker_id,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_audit_log(row)

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


__all__ = ["SQLiteWorkerAuditLogStore"]