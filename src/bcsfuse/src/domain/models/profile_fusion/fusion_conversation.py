"""
Fusion Conversation Models - 融合对话相关模型

包含对话轮次和对话统计两个数据类。
根据 fusion-storage-design.md 规范实现。

设计要点：
- ConversationTurn: 单次问答对（Q-A pair），扁平化结构
- ConversationStats: 累计统计信息（轮次、平均响应时间）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.domain.enums.fuse_enums import ConversationTurnStatus


@dataclass
class ConversationTurn:
    """
    对话轮次（问答对结构，扁平化）

    存储一轮完整的对话，包含：
    - 输入字段：turn_index, question, sender 等
    - 输出字段：answer_content, answer_response_ms 等

    对应数据库字段：conversation_recent（JSON 数组中的元素）
    """

    # === 轮次信息 ===
    turn_index: int
    """轮次序号（从1开始）"""

    # === 用户问题（输入） ===
    question: str
    """用户问题"""

    original_question: Optional[str] = None
    """用户原始问题（可能与 question 相同）"""

    rewritten_question: Optional[str] = None
    """LLM 改写后问题"""

    context_summary: Optional[str] = None
    """上下文摘要（GroupContextService 生成）"""

    sender_id: str = ""
    """提问用户 ID"""

    sender_name: str = ""
    """提问用户名称"""

    timestamp: Optional[str] = None
    """用户提问时间戳（ISO 8601）"""

    # === 大模型回复（输出） ===
    answer_content: Optional[str] = None
    """回复内容"""

    answer_response_ms: Optional[int] = None
    """响应时间（毫秒）"""

    answer_timestamp: Optional[str] = None
    """回复时间戳（ISO 8601）"""

    question_token: Optional[int] = None
    """问题 token 数"""

    response_token: Optional[int] = None
    """回答 token 数"""

    # === 状态 ===
    status: str = ConversationTurnStatus.COMPLETED.value
    """对话轮次状态"""

    # === 服务器信息 ===
    server_ip: Optional[str] = None
    """服务器地址"""

    def __post_init__(self):
        """初始化后处理"""
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        """
        转换为字典（用于 JSON 序列化）

        仅包含非空的可选字段，减少存储空间。
        """
        result = {
            "turn_index": self.turn_index,
            "question": self.question,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp,
        }

        # 可选的输入字段
        if self.original_question is not None:
            result["original_question"] = self.original_question
        if self.rewritten_question is not None:
            result["rewritten_question"] = self.rewritten_question
        if self.context_summary is not None:
            result["context_summary"] = self.context_summary

        # 可选的输出字段
        if self.answer_content is not None:
            result["answer_content"] = self.answer_content
        if self.answer_response_ms is not None:
            result["answer_response_ms"] = self.answer_response_ms
        if self.answer_timestamp is not None:
            result["answer_timestamp"] = self.answer_timestamp
        if self.question_token is not None:
            result["question_token"] = self.question_token
        if self.response_token is not None:
            result["response_token"] = self.response_token

        # 可选的服务器信息字段
        if self.server_ip is not None:
            result["server_ip"] = self.server_ip

        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationTurn":
        """从字典创建实例"""
        return cls(
            turn_index=data["turn_index"],
            question=data["question"],
            original_question=data.get("original_question"),
            rewritten_question=data.get("rewritten_question"),
            context_summary=data.get("context_summary"),
            sender_id=data.get("sender_id", ""),
            sender_name=data.get("sender_name", ""),
            timestamp=data.get("timestamp"),
            answer_content=data.get("answer_content"),
            answer_response_ms=data.get("answer_response_ms"),
            answer_timestamp=data.get("answer_timestamp"),
            question_token=data.get("question_token"),
            response_token=data.get("response_token"),
            status=data.get("status", ConversationTurnStatus.COMPLETED.value),
            server_ip=data.get("server_ip"),
        )

    def set_answer(
        self,
        content: str,
        response_ms: int,
        timestamp: Optional[str] = None,
    ) -> None:
        """
        设置回答内容

        Args:
            content: 回复内容
            response_ms: 响应时间（毫秒）
            timestamp: 回复时间戳，默认当前时间
        """
        self.answer_content = content
        self.answer_response_ms = response_ms
        self.answer_timestamp = timestamp or datetime.now().isoformat()
        self.status = ConversationTurnStatus.COMPLETED.value


@dataclass
class ConversationStats:
    """
    对话统计

    存储累计对话轮次、平均响应时间和平均 token 消耗。

    对应数据库字段：conversation_stats（JSON）
    格式：{"turns": 125, "avg_response_ms": 850, "avg_question_token": 50.5, "avg_response_token": 1200.3}
    """

    turns: int = 0
    """累计对话轮次"""

    avg_response_ms: float = 0.0
    """平均响应时间（毫秒）"""

    avg_question_token: float = 0.0
    """平均问题 token 数"""

    avg_response_token: float = 0.0
    """平均回答 token 数"""

    def to_dict(self) -> dict[str, Any]:
        """
        转换为字典（用于 JSON 序列化）

        Returns:
            dict: {"turns": int, "avg_response_ms": float, "avg_question_token": float, "avg_response_token": float}
        """
        return {
            "turns": self.turns,
            "avg_response_ms": self.avg_response_ms,
            "avg_question_token": self.avg_question_token,
            "avg_response_token": self.avg_response_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationStats":
        """
        从字典创建实例

        Args:
            data: {"turns": int, "avg_response_ms": float, "avg_question_token": float, "avg_response_token": float}

        Returns:
            ConversationStats 实例
        """
        if isinstance(data, cls):
            return data

        return cls(
            turns=data.get("turns", 0),
            avg_response_ms=data.get("avg_response_ms", 0.0),
            avg_question_token=data.get("avg_question_token", 0.0),
            avg_response_token=data.get("avg_response_token", 0.0),
        )

    def update_with_new_response(
        self,
        response_ms: int,
        question_token: int | None = None,
        response_token: int | None = None,
    ) -> None:
        """
        追加新响应并更新统计

        公式: new_avg = (old_avg × old_turns + new_value) / new_turns

        Args:
            response_ms: 新响应时间（毫秒）
            question_token: 问题 token 数（可选）
            response_token: 回答 token 数（可选）
        """
        if self.turns == 0:
            self.avg_response_ms = float(response_ms)
            if question_token is not None:
                self.avg_question_token = float(question_token)
            if response_token is not None:
                self.avg_response_token = float(response_token)
        else:
            self.avg_response_ms = (
                (self.avg_response_ms * self.turns + response_ms) / (self.turns + 1)
            )
            if question_token is not None:
                self.avg_question_token = (
                    (self.avg_question_token * self.turns + question_token) / (self.turns + 1)
                )
            if response_token is not None:
                self.avg_response_token = (
                    (self.avg_response_token * self.turns + response_token) / (self.turns + 1)
                )
        self.turns += 1

    def __repr__(self) -> str:
        return (
            f"ConversationStats(turns={self.turns}, avg_response_ms={self.avg_response_ms:.2f}, "
            f"avg_question_token={self.avg_question_token:.1f}, avg_response_token={self.avg_response_token:.1f})"
        )


__all__ = ["ConversationTurn", "ConversationStats"]