"""
InMemory Fused Profile Store

融合结果的内存存储实现。

用于：
- 测试
- 快速验证业务逻辑
- 本地开发

注意：无持久化，进程重启后数据丢失。
"""

from __future__ import annotations

from typing import Optional

from src.domain.models.profile_fusion import FusedProfileRecord
from src.domain.models.profile_fusion import ConversationTurn
from src.domain.exceptions import FusionNotFoundException
from src.infra.repositories.fused_profile_repository import FusedProfileRepository
from src.utils import get_fusion_env


class InMemoryFusedProfileStore(FusedProfileRepository):
    """
    融合结果内存存储

    功能：
    - 融合结果的增删改查
    - 对话轮次的追加和查询
    - 执行状态的更新
    """

    # 最大对话轮次限制
    MAX_RECENT_MESSAGES = 100

    def __init__(self):
        """初始化空存储"""
        self._storage: dict[str, FusedProfileRecord] = {}

    def save(self, record: FusedProfileRecord) -> str:
        """保存融合结果"""
        self._storage[record.fusion_id] = record
        return record.fusion_id

    def update(self, record: FusedProfileRecord) -> str:
        """更新已存在的融合结果"""
        if record.fusion_id not in self._storage:
            raise FusionNotFoundException(record.fusion_id)
        self._storage[record.fusion_id] = record
        return record.fusion_id

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

    def find_by_key(self, fusion_id: str) -> Optional[FusedProfileRecord]:
        """根据 fusion_id 查询融合结果（fusion_id 全局唯一，无需 env 过滤）"""
        return self._storage.get(fusion_id)

    def find_by_participant(
        self,
        participant_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """查询某个专家参与的融合"""
        env = get_fusion_env()
        results = []
        for record in self._storage.values():
            # 过滤环境
            if record.env != env:
                continue

            # 检查参与者是否在列表中
            participant_ids = record.get_participant_ids_list()
            if participant_id not in participant_ids:
                continue

            # 检查融合模式过滤
            if fusion_mode and record.fusion_mode != fusion_mode:
                continue

            results.append(record)

        # 按创建时间倒序排列
        results.sort(key=lambda r: r.gmt_create or "", reverse=True)
        return results[:limit]

    def find_by_group(
        self,
        group_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """查询某个群组的融合记录"""
        env = get_fusion_env()
        results = []
        for record in self._storage.values():
            # 过滤环境
            if record.env != env:
                continue

            # 检查群组 ID
            if record.group_id != group_id:
                continue

            # 检查融合模式过滤
            if fusion_mode and record.fusion_mode != fusion_mode:
                continue

            results.append(record)

        # 按创建时间倒序排列
        results.sort(key=lambda r: r.gmt_create or "", reverse=True)
        return results[:limit]

    def append_turn(
        self,
        fusion_id: str,
        turn: ConversationTurn,
    ) -> None:
        """追加对话轮次，更新统计（倒序存储，最新在前）"""
        record = self._storage.get(fusion_id)
        if not record:
            return

        # 确保对话列表存在
        if record.conversation_recent is None:
            record.conversation_recent = []

        # 确保统计信息存在并归一化
        if record.conversation_stats is None:
            record.conversation_stats = {"turns": 0, "avg_response_ms": 0, "avg_question_token": 0, "avg_response_token": 0}
        else:
            # 归一化旧数据格式
            record.conversation_stats = self._normalize_conversation_stats(record.conversation_stats)

        # 设置轮次序号
        stats = record.conversation_stats
        turn.turn_index = stats.get("turns", 0) + 1

        # 插入到开头（倒序存储，最新在前）
        record.conversation_recent.insert(0, turn.to_dict())

        # 滑动窗口：超过限制时剔除末尾最旧的
        while len(record.conversation_recent) > self.MAX_RECENT_MESSAGES:
            record.conversation_recent.pop()

        # 更新统计
        stats["turns"] = turn.turn_index

        # 更新平均响应时间
        if turn.answer_response_ms is not None:
            old_avg = stats.get("avg_response_ms", 0)
            old_turns = stats["turns"] - 1
            if old_turns > 0:
                new_avg = (old_avg * old_turns + turn.answer_response_ms) / stats["turns"]
            else:
                new_avg = turn.answer_response_ms
            stats["avg_response_ms"] = round(new_avg, 2)

        # 更新平均 token 统计
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

    def get_conversation(
        self,
        fusion_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> Optional[dict]:
        """获取对话（支持分页）"""
        record = self._storage.get(fusion_id)
        if not record:
            return None

        conversation = record.conversation_recent or []
        stats = self._normalize_conversation_stats(record.conversation_stats)

        # 计算实际存储的起始轮次
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
        record = self._storage.get(fusion_id)
        if not record:
            return

        record.status = status
        if fuse_message is not None:
            record.fuse_message = fuse_message

    def exists(self, fusion_id: str) -> bool:
        """检查记录是否存在（fusion_id 全局唯一，无需 env 过滤）"""
        return fusion_id in self._storage

    def clear(self) -> None:
        """清空所有数据（测试用）"""
        self._storage.clear()

    def count(self, fusion_mode: Optional[str] = None) -> int:
        """返回存储的记录数量（测试用）"""
        env = get_fusion_env()
        records = [r for r in self._storage.values() if r.env == env]
        if fusion_mode:
            records = [r for r in records if r.fusion_mode == fusion_mode]
        return len(records)


__all__ = ["InMemoryFusedProfileStore"]