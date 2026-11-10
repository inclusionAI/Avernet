"""
GroupConversationSummary

G9 群组会话总结结果的数据模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GroupConversationSummary:
    """
    群组会话总结结果

    包含问题改写结果和会话上下文摘要。
    """

    rewritten_question: str
    """改写后的问题，补充了上下文信息"""

    original_question: str
    """原始问题"""

    context_summary: str
    """会话上下文摘要，用于 prompt 增强"""

    key_messages: list[dict[str, str]]
    """
    与原始问题相关的关键群消息。

    每条消息包含 `sender`（发送者/bot 名）和 `content`（核心原话），
    用于在最终 Prompt 中保留"谁说了什么"的原始信息。
    """

    context_messages_count: int
    """会话历史消息数量"""

    success: bool = True
    """是否成功获取并总结会话"""

    error_message: Optional[str] = None
    """错误信息（如果失败）"""


__all__ = [
    "GroupConversationSummary",
]