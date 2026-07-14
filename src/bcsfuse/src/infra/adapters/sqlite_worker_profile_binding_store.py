"""
SQLite Worker Profile Binding Store

Worker Profile 绑定关系的 SQLite 存储实现。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from src.domain.models.worker_profile_binding import WorkerProfileBinding
from src.domain.models.worker_source_info import WorkerSourceType
from src.infra.adapters.sqlite_schema import init_schema


class SQLiteWorkerProfileBindingStore:
    """
    Worker Profile Binding SQLite 存储

    Stage 1 Phase 2 实现：
    - 一个 worker 只能有一个 active profile（通过唯一索引保证）
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

    def _row_to_binding(self, row: sqlite3.Row) -> WorkerProfileBinding:
        """
        将数据库行转换为 WorkerProfileBinding 模型

        Args:
            row: 数据库行

        Returns:
            WorkerProfileBinding 模型
        """
        return WorkerProfileBinding(
            id=row["id"],
            worker_id=row["worker_id"],
            profile_key=row["profile_key"],
            source_type=WorkerSourceType(row["source_type"]),
            is_active=bool(row["is_active"]),
            bound_at=datetime.fromisoformat(row["bound_at"]) if row["bound_at"] else None,
            unbound_at=datetime.fromisoformat(row["unbound_at"]) if row["unbound_at"] else None,
            updated_at=datetime.fromisoformat(row["gmt_modify"]),
        )

    def bind_profile(
        self,
        worker_id: str,
        profile_key: str,
        source_type: WorkerSourceType,
    ) -> WorkerProfileBinding:
        """
        绑定 Profile 到 Worker

        Stage 1 规则：
        - 如果已有 active binding，先 deactive 旧的

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识
            source_type: 来源类型

        Returns:
            WorkerProfileBinding
        """
        now = datetime.utcnow()
        binding_id = f"binding_{uuid.uuid4().hex[:12]}"

        cursor = self._conn.cursor()

        # 先将所有该 worker 的绑定设为非活跃
        cursor.execute(
            """
            UPDATE bcsfuse_worker_profile_bindings
            SET is_active = 0, unbound_at = ?, gmt_modify = ?
            WHERE worker_id = ? AND is_active = 1
            """,
            (now.isoformat(), now.isoformat(), worker_id),
        )

        # 检查是否已有该绑定
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_bindings WHERE worker_id = ? AND profile_key = ?",
            (worker_id, profile_key),
        )
        existing = cursor.fetchone()

        if existing:
            # 更新现有绑定为活跃
            cursor.execute(
                """
                UPDATE bcsfuse_worker_profile_bindings
                SET is_active = 1, unbound_at = NULL, gmt_modify = ?
                WHERE id = ?
                """,
                (now.isoformat(), existing["id"]),
            )
            self._conn.commit()

            cursor.execute(
                "SELECT * FROM bcsfuse_worker_profile_bindings WHERE id = ?",
                (existing["id"],),
            )
            return self._row_to_binding(cursor.fetchone())

        # 创建新绑定
        cursor.execute(
            """
            INSERT INTO bcsfuse_worker_profile_bindings
            (id, worker_id, profile_key, source_type, is_active, bound_at, gmt_create, gmt_modify)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (binding_id, worker_id, profile_key, source_type.value, now.isoformat(), now.isoformat(), now.isoformat()),
        )
        self._conn.commit()

        return WorkerProfileBinding(
            id=binding_id,
            worker_id=worker_id,
            profile_key=profile_key,
            source_type=source_type,
            is_active=True,
            bound_at=now,
            updated_at=now,
        )

    def unbind_profile(self, worker_id: str, profile_key: str) -> bool:
        """
        解绑 Profile

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识

        Returns:
            是否解绑成功
        """
        now = datetime.utcnow()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            UPDATE bcsfuse_worker_profile_bindings
            SET is_active = 0, unbound_at = ?, gmt_modify = ?
            WHERE worker_id = ? AND profile_key = ?
            """,
            (now.isoformat(), now.isoformat(), worker_id, profile_key),
        )
        self._conn.commit()

        return cursor.rowcount > 0

    def get_active_binding(self, worker_id: str) -> Optional[WorkerProfileBinding]:
        """
        获取活跃绑定

        Stage 1 只返回一个绑定（或 None）

        Args:
            worker_id: Worker ID

        Returns:
            WorkerProfileBinding 或 None
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_bindings WHERE worker_id = ? AND is_active = 1",
            (worker_id,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_binding(row)

    def set_active_profile(
        self,
        worker_id: str,
        profile_key: str,
    ) -> bool:
        """
        设置活跃 Profile

        Stage 1 只支持一个 active，会替换现有的

        Args:
            worker_id: Worker ID
            profile_key: Profile 唯一标识

        Returns:
            是否设置成功
        """
        # 使用 bind_profile 实现
        binding = self.bind_profile(
            worker_id=worker_id,
            profile_key=profile_key,
            source_type=WorkerSourceType.API,  # 默认 API 来源
        )
        return binding.is_active

    def list_bindings_by_worker(self, worker_id: str) -> list[WorkerProfileBinding]:
        """
        列出 Worker 的所有绑定

        Stage 1 只返回一个（或空列表）

        Args:
            worker_id: Worker ID

        Returns:
            WorkerProfileBinding 列表
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_bindings WHERE worker_id = ? ORDER BY gmt_modify DESC",
            (worker_id,),
        )
        rows = cursor.fetchall()

        return [self._row_to_binding(row) for row in rows]

    def get_binding_by_profile_key(self, profile_key: str) -> Optional[WorkerProfileBinding]:
        """
        根据 profile_key 获取绑定

        用于从 participant_id (profile_key) 反查 worker_id。

        Stage 1 只返回一个活跃绑定（或 None）

        Args:
            profile_key: Profile 唯一标识

        Returns:
            WorkerProfileBinding 或 None
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_bindings WHERE profile_key = ? AND is_active = 1",
            (profile_key,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_binding(row)

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


__all__ = ["SQLiteWorkerProfileBindingStore"]