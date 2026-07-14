"""
SQLite Fused Profile Store

FusedProfile 的 SQLite 存储实现。

用于本地开发和单实例部署，支持 :memory: 模式用于测试。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from src.domain.models.profile_fusion import FusedProfileRecord
from src.domain.models.profile_fusion import ConversationTurn
from src.infra.repositories.fused_profile_repository import FusedProfileRepository
from src.domain.exceptions import DuplicateFusionException, FusionNotFoundException
from src.utils import get_fusion_env

logger = logging.getLogger(__name__)


# SQL 语句常量
CREATE_FUSION_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_fusion_session (
    fusion_id TEXT PRIMARY KEY,
    fusion_mode TEXT NOT NULL,
    group_id TEXT,
    driver_bot_id TEXT,
    question TEXT,
    participant_ids TEXT NOT NULL,
    participant_profile_snapshot TEXT,
    fuse_detail TEXT,
    conversation_recent TEXT,
    conversation_stats TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    fuse_message TEXT,
    env TEXT NOT NULL DEFAULT 'prod',
    created_by TEXT,
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fusion_session_env ON bcsfuse_fusion_session(env);
CREATE INDEX IF NOT EXISTS idx_fusion_session_group_id ON bcsfuse_fusion_session(group_id);
CREATE INDEX IF NOT EXISTS idx_fusion_session_fusion_mode ON bcsfuse_fusion_session(fusion_mode);
CREATE INDEX IF NOT EXISTS idx_fusion_session_status ON bcsfuse_fusion_session(status);
CREATE INDEX IF NOT EXISTS idx_fusion_session_gmt_create ON bcsfuse_fusion_session(gmt_create);
"""


class SQLiteFusedProfileStore(FusedProfileRepository):
    """
    FusedProfile SQLite 存储实现

    功能：
    - 融合结果的增删改查
    - 对话轮次的追加和查询
    - 支持分页和过滤查询
    """

    MAX_RECENT_MESSAGES = 100  # 对话历史滑动窗口

    def __init__(self, db_path: str = ":memory:"):
        """
        初始化 SQLite 存储

        Args:
            db_path: 数据库路径，默认为内存模式
        """
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        # 启用 WAL 模式提高并发性能
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        # 初始化表结构
        self._ensure_schema()
        logger.info("[SQLiteFusedProfileStore] 初始化完成, db_path=%s", db_path)

    def _ensure_schema(self) -> None:
        """确保表结构存在"""
        cursor = self._conn.cursor()
        cursor.executescript(CREATE_FUSION_SESSION_TABLE)
        self._conn.commit()

    def _normalize_conversation_stats(self, stats: dict | None) -> dict:
        """归一化 conversation_stats，确保包含所有字段（兼容旧数据）"""
        if stats is None:
            return {"turns": 0, "avg_response_ms": 0.0, "avg_question_token": 0.0, "avg_response_token": 0.0}
        # 补充缺失的字段
        return {
            "turns": stats.get("turns", 0),
            "avg_response_ms": stats.get("avg_response_ms", 0.0),
            "avg_question_token": stats.get("avg_question_token", 0.0),
            "avg_response_token": stats.get("avg_response_token", 0.0),
        }

    def _row_to_record(self, row: sqlite3.Row) -> FusedProfileRecord:
        """将数据库行转换为 FusedProfileRecord"""
        return FusedProfileRecord(
            fusion_id=row["fusion_id"],
            fusion_mode=row["fusion_mode"],
            group_id=row["group_id"],
            driver_bot_id=row["driver_bot_id"],
            question=row["question"],
            participant_ids=row["participant_ids"],
            participant_profile_snapshot=json.loads(row["participant_profile_snapshot"])
                if row["participant_profile_snapshot"] else None,
            fuse_detail=json.loads(row["fuse_detail"]) if row["fuse_detail"] else None,
            conversation_recent=json.loads(row["conversation_recent"])
                if row["conversation_recent"] else [],
            conversation_stats=self._normalize_conversation_stats(
                json.loads(row["conversation_stats"]) if row["conversation_stats"] else None
            ),
            status=row["status"],
            fuse_message=row["fuse_message"],
            created_by=row["created_by"],
            gmt_create=datetime.fromisoformat(row["gmt_create"]) if row["gmt_create"] else None,
            gmt_modify=datetime.fromisoformat(row["gmt_modify"]) if row["gmt_modify"] else None,
            env=row["env"],
        )

    def _record_to_dict(self, record: FusedProfileRecord) -> dict:
        """将 FusedProfileRecord 转换为数据库字典"""
        now = datetime.utcnow()
        return {
            "fusion_id": record.fusion_id,
            "fusion_mode": record.fusion_mode,
            "group_id": record.group_id,
            "driver_bot_id": record.driver_bot_id,
            "question": record.question,
            "participant_ids": record.participant_ids,
            "participant_profile_snapshot": json.dumps(record.participant_profile_snapshot)
                if record.participant_profile_snapshot else None,
            "fuse_detail": json.dumps(record.fuse_detail) if record.fuse_detail else None,
            "conversation_recent": json.dumps(record.conversation_recent)
                if record.conversation_recent else "[]",
            "conversation_stats": json.dumps(record.conversation_stats)
                if record.conversation_stats else '{"turns": 0, "avg_response_ms": 0, "avg_question_token": 0, "avg_response_token": 0}',
            "status": record.status,
            "fuse_message": record.fuse_message,
            "env": record.env,
            "created_by": record.created_by,
            "gmt_create": (record.gmt_create or now).isoformat(),
            "gmt_modify": now.isoformat(),
        }

    def save(self, record: FusedProfileRecord) -> str:
        """保存融合结果"""
        if self.exists(record.fusion_id):
            raise DuplicateFusionException(record.fusion_id)

        data = self._record_to_dict(record)
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        sql = f"INSERT INTO bcsfuse_fusion_session ({columns}) VALUES ({placeholders})"

        cursor = self._conn.cursor()
        cursor.execute(sql, list(data.values()))
        self._conn.commit()

        logger.debug("[SQLite] Saved fused profile: %s", record.fusion_id)
        return record.fusion_id

    def update(self, record: FusedProfileRecord) -> str:
        """更新已存在的融合结果"""
        if not self.exists(record.fusion_id):
            raise FusionNotFoundException(record.fusion_id)

        data = self._record_to_dict(record)
        # 移除 fusion_id、gmt_create 和 env，保留 gmt_modify 作为更新时间
        data.pop("fusion_id", None)
        data.pop("gmt_create", None)
        data.pop("env", None)

        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        sql = f"UPDATE bcsfuse_fusion_session SET {set_clause} WHERE fusion_id = ?"

        cursor = self._conn.cursor()
        cursor.execute(sql, list(data.values()) + [record.fusion_id])
        self._conn.commit()

        logger.debug("[SQLite] Updated fused profile: %s", record.fusion_id)
        return record.fusion_id

    def find_by_key(self, fusion_id: str) -> Optional[FusedProfileRecord]:
        """根据 fusion_id 查询融合结果（fusion_id 全局唯一，无需 env 过滤）"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM bcsfuse_fusion_session WHERE fusion_id = ?",
            (fusion_id,)
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def find_by_participant(
        self,
        participant_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """查询某个专家参与的融合"""
        env = get_fusion_env()
        cursor = self._conn.cursor()
        sql = "SELECT * FROM bcsfuse_fusion_session WHERE env = ? AND participant_ids LIKE ?"
        params = [env, f"%{participant_id}%"]

        if fusion_mode:
            sql += " AND fusion_mode = ?"
            params.append(fusion_mode)

        sql += " ORDER BY gmt_create DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    def find_by_group(
        self,
        group_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """查询某个群组的融合记录"""
        env = get_fusion_env()
        cursor = self._conn.cursor()
        sql = "SELECT * FROM bcsfuse_fusion_session WHERE env = ? AND group_id = ?"
        params = [env, group_id]

        if fusion_mode:
            sql += " AND fusion_mode = ?"
            params.append(fusion_mode)

        sql += " ORDER BY gmt_create DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    def append_turn(self, fusion_id: str, turn: ConversationTurn) -> None:
        """追加对话轮次，更新统计（倒序存储，最新在前）"""
        record = self.find_by_key(fusion_id)
        if not record:
            raise FusionNotFoundException(fusion_id)

        # 解析现有对话
        conversation = record.conversation_recent or []
        stats = self._normalize_conversation_stats(record.conversation_stats)

        # 设置轮次序号
        turn.turn_index = stats.get("turns", 0) + 1

        # 插入到开头（倒序存储，最新在前）
        conversation.insert(0, turn.to_dict())

        # 滑动窗口：超过限制时剔除末尾最旧的
        while len(conversation) > self.MAX_RECENT_MESSAGES:
            conversation.pop()

        # 更新统计
        stats["turns"] = turn.turn_index
        if turn.answer_response_ms is not None:
            old_avg = stats.get("avg_response_ms", 0)
            old_turns = stats["turns"] - 1
            if old_turns > 0:
                new_avg = (old_avg * old_turns + turn.answer_response_ms) / stats["turns"]
            else:
                new_avg = turn.answer_response_ms
            stats["avg_response_ms"] = round(new_avg, 2)

        if turn.question_token is not None:
            old_avg = stats.get("avg_question_token", 0)
            old_turns = stats["turns"] - 1
            if old_turns > 0:
                new_avg = (old_avg * old_turns + turn.question_token) / stats["turns"]
            else:
                new_avg = turn.question_token
            stats["avg_question_token"] = round(new_avg, 2)

        if turn.response_token is not None:
            old_avg = stats.get("avg_response_token", 0)
            old_turns = stats["turns"] - 1
            if old_turns > 0:
                new_avg = (old_avg * old_turns + turn.response_token) / stats["turns"]
            else:
                new_avg = turn.response_token
            stats["avg_response_token"] = round(new_avg, 2)

        # 更新数据库
        cursor = self._conn.cursor()
        cursor.execute("""
            UPDATE bcsfuse_fusion_session
            SET conversation_recent = ?, conversation_stats = ?, gmt_modify = ?
            WHERE fusion_id = ?
        """, (
            json.dumps(conversation),
            json.dumps(stats),
            datetime.utcnow().isoformat(),
            fusion_id,
        ))
        self._conn.commit()
        logger.debug("[SQLite] Appended turn to fusion: %s, turn_index=%d", fusion_id, turn.turn_index)

    def get_conversation(
        self,
        fusion_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> Optional[dict]:
        """获取对话（支持分页）"""
        record = self.find_by_key(fusion_id)
        if not record:
            return None

        conversation = record.conversation_recent or []
        stats = self._normalize_conversation_stats(record.conversation_stats)
        start_turn = stats.get("turns", 0) - len(conversation) + 1 if conversation else 0

        return {
            "fusion_id": record.fusion_id,
            "turns": conversation[offset:offset + limit],
            "total_turns": stats.get("turns", 0),
            "avg_response_ms": stats.get("avg_response_ms", 0),
            "avg_question_token": stats.get("avg_question_token", 0),
            "avg_response_token": stats.get("avg_response_token", 0),
            "stored_range": {
                "start": start_turn,
                "end": stats.get("turns", 0),
                "count": len(conversation),
            },
        }

    def update_status(
        self,
        fusion_id: str,
        status: str,
        fuse_message: Optional[str] = None,
    ) -> None:
        """更新执行状态"""
        if not self.exists(fusion_id):
            raise FusionNotFoundException(fusion_id)

        cursor = self._conn.cursor()
        if fuse_message is not None:
            cursor.execute("""
                UPDATE bcsfuse_fusion_session
                SET status = ?, fuse_message = ?, gmt_modify = ?
                WHERE fusion_id = ?
            """, (status, fuse_message, datetime.utcnow().isoformat(), fusion_id))
        else:
            cursor.execute("""
                UPDATE bcsfuse_fusion_session
                SET status = ?, gmt_modify = ?
                WHERE fusion_id = ?
            """, (status, datetime.utcnow().isoformat(), fusion_id))
        self._conn.commit()
        logger.info("[SQLite] Updated fusion status: %s -> %s", fusion_id, status)

    def exists(self, fusion_id: str) -> bool:
        """检查记录是否存在（fusion_id 全局唯一，无需 env 过滤）"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM bcsfuse_fusion_session WHERE fusion_id = ?",
            (fusion_id,)
        )
        return cursor.fetchone() is not None

    def count(self, fusion_mode: Optional[str] = None) -> int:
        """统计记录数量"""
        env = get_fusion_env()
        cursor = self._conn.cursor()
        if fusion_mode:
            cursor.execute(
                "SELECT COUNT(*) FROM bcsfuse_fusion_session WHERE env = ? AND fusion_mode = ?",
                (env, fusion_mode)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM bcsfuse_fusion_session WHERE env = ?",
                (env,)
            )
        return cursor.fetchone()[0]

    def clear(self) -> None:
        """清空所有数据（测试用）"""
        env = get_fusion_env()
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM bcsfuse_fusion_session WHERE env = ?", (env,))
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            logger.info("[SQLiteFusedProfileStore] 数据库连接已关闭")


__all__ = ["SQLiteFusedProfileStore"]