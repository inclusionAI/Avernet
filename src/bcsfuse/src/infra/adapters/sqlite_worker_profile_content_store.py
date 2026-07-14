"""
SQLite Worker Profile Content Store

Profile API MVP - SQLite 实现的 Profile 内容存储

持久化 API 注册的 Profile 内容，支持：
- 按 worker_id / profile_id 查询
- 活跃 profile 绑定
- 版本控制
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from src.domain.models.worker_profile_content import (
    ProfileContentType,
    SkillSet,
    WorkerProfileContent,
    WorkerProfileContentList,
)
from src.infra.adapters.sqlite_schema import init_schema


# Profile contents 表 Schema（与线上 MySQL 字段名一致）
CREATE_WORKER_PROFILE_CONTENTS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_worker_profile_contents (
    id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    profile_id TEXT NOT NULL DEFAULT 'default',
    display_name TEXT,
    soul_md TEXT,
    agents_md TEXT,
    tools_md TEXT,
    boot_md TEXT,
    heartbeat_md TEXT,
    contents TEXT,    -- JSON Map: 扩展内容 {"profile": "语义画像", "capabilities": ["标签"], ...}
    skill_sets TEXT,  -- JSON array
    metadata TEXT,    -- JSON object
    content_type TEXT NOT NULL DEFAULT 'api',
    is_active INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL,
    UNIQUE(worker_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_bcsfuse_worker_profile_contents_worker_id
    ON bcsfuse_worker_profile_contents(worker_id);
CREATE INDEX IF NOT EXISTS idx_bcsfuse_worker_profile_contents_active
    ON bcsfuse_worker_profile_contents(worker_id, is_active);
"""


class SQLiteWorkerProfileContentStore:
    """
    SQLite 实现的 Worker Profile Content Store

    功能：
    - 保存/获取/删除 Profile
    - 活跃 Profile 管理
    - 版本控制
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        初始化 Store

        Args:
            db_path: 数据库路径，默认内存模式
        """
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # 初始化基础 schema
        init_schema(self._conn)

        # 初始化 profile contents 表
        cursor = self._conn.cursor()
        cursor.executescript(CREATE_WORKER_PROFILE_CONTENTS_TABLE)
        self._conn.commit()

    def _row_to_content(self, row: sqlite3.Row) -> WorkerProfileContent:
        """将数据库行转换为 WorkerProfileContent"""
        # 解析 skill_sets
        skill_sets = []
        if row["skill_sets"]:
            try:
                skill_data = json.loads(row["skill_sets"])
                skill_sets = [SkillSet(**s) for s in skill_data]
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 metadata
        metadata = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 contents（扩展内容 JSON Map）
        # 包含 LLM 分析结果: {"profile": "语义画像", "capabilities": ["标签", ...]}
        contents = {}
        if "contents" in row.keys() and row["contents"]:
            try:
                contents = json.loads(row["contents"])
            except (json.JSONDecodeError, TypeError):
                pass

        return WorkerProfileContent(
            worker_id=row["worker_id"],
            profile_id=row["profile_id"],
            display_name=row["display_name"],
            soul_md=row["soul_md"],
            agents_md=row["agents_md"],
            tools_md=row["tools_md"],
            boot_md=row["boot_md"],
            heartbeat_md=row["heartbeat_md"],
            contents=contents,
            skill_sets=skill_sets,
            metadata=metadata,
            content_type=ProfileContentType(row["content_type"]),
            is_active=bool(row["is_active"]),
            version=row["version"],
            created_at=datetime.fromisoformat(row["gmt_create"]) if row["gmt_create"] else None,
            updated_at=datetime.fromisoformat(row["gmt_modify"]) if row["gmt_modify"] else None,
        )

    def _generate_id(self, worker_id: str, profile_id: str) -> str:
        """生成记录 ID"""
        return f"profile_{worker_id}_{profile_id}"

    def save(self, content: WorkerProfileContent) -> WorkerProfileContent:
        """保存 Profile 内容"""
        now = datetime.utcnow()
        profile_id = content.profile_id or "default"
        record_id = self._generate_id(content.worker_id, profile_id)

        cursor = self._conn.cursor()

        # 检查是否存在
        cursor.execute(
            "SELECT version FROM bcsfuse_worker_profile_contents WHERE worker_id = ? AND profile_id = ?",
            (content.worker_id, profile_id),
        )
        existing = cursor.fetchone()

        if existing:
            # 更新
            new_version = existing["version"] + 1
            cursor.execute(
                """
                UPDATE bcsfuse_worker_profile_contents SET
                    display_name = ?,
                    soul_md = ?,
                    agents_md = ?,
                    tools_md = ?,
                    boot_md = ?,
                    heartbeat_md = ?,
                    contents = ?,
                    skill_sets = ?,
                    metadata = ?,
                    content_type = ?,
                    version = ?,
                    gmt_modify = ?
                WHERE worker_id = ? AND profile_id = ?
                """,
                (
                    content.display_name,
                    content.soul_md,
                    content.agents_md,
                    content.tools_md,
                    content.boot_md,
                    content.heartbeat_md,
                    json.dumps(content.contents),
                    json.dumps([s.model_dump() for s in content.skill_sets]),
                    json.dumps(content.metadata),
                    content.content_type.value,
                    new_version,
                    now.isoformat(),
                    content.worker_id,
                    profile_id,
                ),
            )
            content.version = new_version
        else:
            # 创建
            cursor.execute(
                """
                INSERT INTO bcsfuse_worker_profile_contents (
                    id, worker_id, profile_id, display_name,
                    soul_md, agents_md, tools_md, boot_md, heartbeat_md,
                    contents, skill_sets, metadata,
                    content_type, is_active,
                    version, gmt_create, gmt_modify
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    content.worker_id,
                    profile_id,
                    content.display_name,
                    content.soul_md,
                    content.agents_md,
                    content.tools_md,
                    content.boot_md,
                    content.heartbeat_md,
                    json.dumps(content.contents),
                    json.dumps([s.model_dump() for s in content.skill_sets]),
                    json.dumps(content.metadata),
                    content.content_type.value,
                    1 if content.is_active else 0,
                    1,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            content.version = 1

        self._conn.commit()

        # 返回更新后的内容
        content.created_at = content.created_at or now
        content.updated_at = now

        return content

    def get(self, worker_id: str, profile_id: str) -> Optional[WorkerProfileContent]:
        """获取指定 Profile"""
        profile_id = profile_id or "default"

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_contents WHERE worker_id = ? AND profile_id = ?",
            (worker_id, profile_id),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_content(row)

    def list_by_worker(self, worker_id: str) -> WorkerProfileContentList:
        """列出 Worker 的所有 Profiles"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_contents WHERE worker_id = ? ORDER BY is_active DESC, gmt_modify DESC",
            (worker_id,),
        )
        rows = cursor.fetchall()

        items = [self._row_to_content(row) for row in rows]

        # 找到活跃 profile_id
        active_profile_id = None
        for item in items:
            if item.is_active:
                active_profile_id = item.profile_id
                break

        return WorkerProfileContentList(
            items=items,
            total=len(items),
            active_profile_id=active_profile_id,
        )

    def delete(self, worker_id: str, profile_id: str) -> bool:
        """删除 Profile"""
        profile_id = profile_id or "default"

        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM bcsfuse_worker_profile_contents WHERE worker_id = ? AND profile_id = ?",
            (worker_id, profile_id),
        )
        self._conn.commit()

        return cursor.rowcount > 0

    def activate(self, worker_id: str, profile_id: str) -> Optional[WorkerProfileContent]:
        """设置活跃 Profile"""
        profile_id = profile_id or "default"

        cursor = self._conn.cursor()

        # 先将该 worker 的所有 profile 设为非活跃
        cursor.execute(
            "UPDATE bcsfuse_worker_profile_contents SET is_active = 0 WHERE worker_id = ?",
            (worker_id,),
        )

        # 再将指定 profile 设为活跃
        cursor.execute(
            "UPDATE bcsfuse_worker_profile_contents SET is_active = 1, gmt_modify = ? WHERE worker_id = ? AND profile_id = ?",
            (datetime.utcnow().isoformat(), worker_id, profile_id),
        )

        self._conn.commit()

        if cursor.rowcount == 0:
            return None

        return self.get(worker_id, profile_id)

    def get_active(self, worker_id: str) -> Optional[WorkerProfileContent]:
        """获取活跃 Profile"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_contents WHERE worker_id = ? AND is_active = 1",
            (worker_id,),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_content(row)

    def exists(self, worker_id: str, profile_id: str) -> bool:
        """检查 Profile 是否存在"""
        profile_id = profile_id or "default"

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bcsfuse_worker_profile_contents WHERE worker_id = ? AND profile_id = ?",
            (worker_id, profile_id),
        )
        return cursor.fetchone() is not None

    def count(self, worker_id: Optional[str] = None) -> int:
        """统计 Profile 数量"""
        cursor = self._conn.cursor()

        if worker_id:
            cursor.execute(
                "SELECT COUNT(*) FROM bcsfuse_worker_profile_contents WHERE worker_id = ?",
                (worker_id,),
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM bcsfuse_worker_profile_contents")

        return cursor.fetchone()[0]

    def get_all_active(self) -> list[WorkerProfileContent]:
        """获取所有活跃 Profile"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_worker_profile_contents WHERE is_active = 1"
        )
        rows = cursor.fetchall()

        return [self._row_to_content(row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()


__all__ = ["SQLiteWorkerProfileContentStore"]