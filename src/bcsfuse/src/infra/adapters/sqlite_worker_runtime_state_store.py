"""
SQLite Worker Runtime State Store

Worker 运行态的 SQLite 存储实现。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.infra.adapters.sqlite_schema import init_schema


class SQLiteWorkerRuntimeStateStore:
    """
    Worker Runtime State SQLite 存储

    Stage 1 Phase 2 实现：
    - 独立表存储运行态（高频更新）
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

    def get_runtime_state(self, worker_id: str) -> Optional[WorkerRuntimeState]:
        """
        获取运行态

        Args:
            worker_id: Worker ID

        Returns:
            WorkerRuntimeState 或 None
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT runtime_state FROM bcsfuse_worker_runtime_states WHERE worker_id = ?",
            (worker_id,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return WorkerRuntimeState(row["runtime_state"])

    def set_runtime_state(
        self,
        worker_id: str,
        runtime_state,
        updated_by: Optional[str] = None,
    ) -> bool:
        """
        设置运行态

        Args:
            worker_id: Worker ID
            runtime_state: WorkerRuntimeState enum or dict with 'state' key
            updated_by: 更新来源

        Returns:
            是否更新成功
        """
        # Handle both enum and dict (parity with MySQLWorkerRuntimeStateStore)
        if isinstance(runtime_state, WorkerRuntimeState):
            state_value = runtime_state.value
        elif isinstance(runtime_state, dict):
            state_value = runtime_state.get("state")
            if hasattr(state_value, "value"):
                state_value = state_value.value
        else:
            raise ValueError(
                f"runtime_state must be WorkerRuntimeState enum or dict, got {type(runtime_state)}"
            )

        now = datetime.utcnow().isoformat()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO bcsfuse_worker_runtime_states (worker_id, runtime_state, gmt_modify, gmt_create, updated_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                runtime_state = excluded.runtime_state,
                gmt_modify = excluded.gmt_modify,
                updated_by = excluded.updated_by
            """,
            (worker_id, state_value, now, now, updated_by),
        )
        self._conn.commit()

        return cursor.rowcount > 0

    def batch_get_runtime_states(
        self,
        worker_ids: list[str],
    ) -> dict[str, WorkerRuntimeState]:
        """
        批量获取运行态

        Args:
            worker_ids: Worker ID 列表

        Returns:
            dict[worker_id, WorkerRuntimeState]
        """
        if not worker_ids:
            return {}

        placeholders = ", ".join(["?" for _ in worker_ids])
        cursor = self._conn.cursor()
        cursor.execute(
            f"SELECT worker_id, runtime_state FROM bcsfuse_worker_runtime_states WHERE worker_id IN ({placeholders})",
            worker_ids,
        )
        rows = cursor.fetchall()

        return {
            row["worker_id"]: WorkerRuntimeState(row["runtime_state"])
            for row in rows
        }

    def count_by_state(self, runtime_state: WorkerRuntimeState) -> int:
        """
        按状态统计数量

        Args:
            runtime_state: 运行态

        Returns:
            数量
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM bcsfuse_worker_runtime_states WHERE runtime_state = ?",
            (runtime_state.value,),
        )
        return cursor.fetchone()[0]

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


__all__ = ["SQLiteWorkerRuntimeStateStore"]