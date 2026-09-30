"""
QuestionRewriteService

基于群组上下文改写用户问题的服务。

当用户在群组环境中提问时，原问题可能缺乏上下文（如代词、省略语等），
本服务通过获取群组最近的消息历史，结合 LLM 改写问题，使其更完整、更利于检索匹配。

流程:
1. 从 BCN API 获取群组最近 N 条消息
2. 将消息历史 + 原问题组合成 prompt 发送给 LLM
3. 返回改写后的问题
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import Complexity, CostSensitivity, LLMTaskSpec, TaskType

logger = logging.getLogger(__name__)

_BCN_BASE_URL = os.getenv("BCN_BASE_URL", "")
_DEFAULT_CONTEXT_LIMIT = 10
_HTTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class GroupMessage:
    """群组消息"""

    sender: str
    content: str


@dataclass(frozen=True)
class RewriteResult:
    """问题改写结果"""

    rewritten_question: str
    original_question: str
    context_messages_count: int


class QuestionRewriteService:
    """
    基于群组上下文的问题改写服务

    Args:
        llm_gateway: LLM Gateway Service 实例
        bcn_base_url: BCN API 基础 URL（configure via BCN_BASE_URL env var）
        context_limit: 获取的最近消息数量（默认 10）
    """

    def __init__(
        self,
        llm_gateway: "LLMGatewayService",
        bcn_base_url: str = _BCN_BASE_URL,
        context_limit: int = _DEFAULT_CONTEXT_LIMIT,
    ) -> None:
        self._gateway = llm_gateway
        self._bcn_base_url = bcn_base_url.rstrip("/")
        self._context_limit = context_limit

    async def rewrite(self, question: str, group_id: str) -> RewriteResult:
        """
        基于群组上下文改写问题

        Args:
            question: 原始问题
            group_id: 群组 ID

        Returns:
            RewriteResult: 改写结果

        Raises:
            QuestionRewriteError: 获取上下文或 LLM 调用失败
        """
        # Step 1: 获取群组最近消息
        messages = await self._fetch_group_messages(group_id)

        if not messages:
            logger.info(
                f"[QuestionRewrite] 群组 {group_id} 无上下文消息, 跳过改写"
            )
            return RewriteResult(
                rewritten_question=question,
                original_question=question,
                context_messages_count=0,
            )

        # Step 2: 构建上下文摘要
        context_text = self._format_messages(messages)

        # Step 3: 调用 LLM 改写问题
        rewritten = self._call_llm_rewrite(question, context_text)

        logger.info(
            f"[QuestionRewrite] 改写完成: "
            f"original='{question[:50]}...' -> rewritten='{rewritten[:50]}...', "
            f"context_count={len(messages)}"
        )

        return RewriteResult(
            rewritten_question=rewritten,
            original_question=question,
            context_messages_count=len(messages),
        )

    async def _fetch_group_messages(self, group_id: str) -> list[GroupMessage]:
        """
        从 BCN API 获取群组最近消息

        Args:
            group_id: 群组 ID

        Returns:
            消息列表（按时间正序）
        """
        url = f"{self._bcn_base_url}/groups/{group_id}/messages"

        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(
                    url,
                    params={"limit": self._context_limit},
                )

                if response.status_code != 200:
                    logger.warning(
                        f"[QuestionRewrite] BCN API 返回非 200 状态: "
                        f"status={response.status_code}, group_id={group_id}"
                    )
                    return []

                data = response.json()
                return self._parse_messages(data)

        except httpx.TimeoutException:
            logger.warning(
                f"[QuestionRewrite] BCN API 超时: group_id={group_id}"
            )
            return []
        except httpx.RequestError as e:
            logger.warning(
                f"[QuestionRewrite] BCN API 请求失败: group_id={group_id}, error={e}"
            )
            return []
        except Exception as e:
            logger.warning(
                f"[QuestionRewrite] 获取群组消息异常: group_id={group_id}, error={e}"
            )
            return []

    # 只保留这两种 role 的消息，tool_result 等角色内容过长且无上下文价值
    _VALID_ROLES: frozenset[str] = frozenset({"user", "assistant"})

    # 单条消息内容最大截断长度，防止 tool_result interleaved 等超长文本撑爆上下文
    _MAX_CONTENT_LENGTH: int = 500

    def _parse_messages(self, data: dict | list) -> list[GroupMessage]:
        """
        解析 BCN API 返回的消息数据

        BCN 消息格式:
        - 平铺列表: [{"id", "sender", "content", "role", "timestamp", ...}, ...]
        - 也兼容包装格式: {"messages": [...]}

        过滤规则:
        - 只保留 role 为 "user" 或 "assistant" 的消息
        - 跳过 content 为空的消息（如只有 tool call 的 assistant 消息）
        - 超长内容截断至 _MAX_CONTENT_LENGTH

        Args:
            data: API 返回的数据

        Returns:
            解析后的消息列表
        """
        messages_raw: list[dict]

        if isinstance(data, list):
            messages_raw = data
        elif isinstance(data, dict):
            messages_raw = data.get("messages", data.get("data", []))
            if not isinstance(messages_raw, list):
                return []
        else:
            return []

        result: list[GroupMessage] = []
        for msg in messages_raw:
            if not isinstance(msg, dict):
                continue

            # 过滤 role: 只保留 user / assistant
            role = msg.get("role", "")
            if role not in self._VALID_ROLES:
                continue

            # 提取 sender
            sender = msg.get("sender", "unknown")
            # 优化 sender 显示: bot ID 过长，简化为 "bot"
            if sender != "user":
                sender = "bot"

            # 提取 content，跳过空内容
            content = msg.get("content", "")
            if not content or not content.strip():
                continue

            # 截断超长内容
            if len(content) > self._MAX_CONTENT_LENGTH:
                content = content[: self._MAX_CONTENT_LENGTH] + "..."

            result.append(
                GroupMessage(
                    sender=str(sender),
                    content=str(content).strip(),
                )
            )

        return result

    def _format_messages(self, messages: list[GroupMessage]) -> str:
        """
        将消息列表格式化为文本

        Args:
            messages: 消息列表

        Returns:
            格式化后的上下文文本
        """
        lines: list[str] = []
        for msg in messages:
            lines.append(f"[{msg.sender}]: {msg.content}")
        return "\n".join(lines)

    def _call_llm_rewrite(self, question: str, context_text: str) -> str:
        """
        调用 LLM 改写问题

        Args:
            question: 原始问题
            context_text: 上下文文本

        Returns:
            改写后的问题
        """
        system_prompt = (
            "你是一个问题改写助手。你的任务是根据群组对话上下文，"
            "将用户当前的问题改写为一个更完整、更具体的问题，"
            "使其脱离上下文也能被独立理解。\n\n"
            "改写规则:\n"
            "1. 将代词(他/它/这个/那个等)替换为具体的指代对象\n"
            "2. 补充省略的背景信息\n"
            "3. 保持原问题的核心意图不变\n"
            "4. 只输出改写后的问题，不要添加额外解释\n"
            "5. 如果原问题已经足够完整，直接输出原问题"
        )

        user_prompt = (
            f"以下是群组最近的对话:\n\n"
            f"{context_text}\n\n"
            f"用户当前的问题: {question}\n\n"
            f"请改写这个问题:"
        )

        task_spec = LLMTaskSpec(
            task_type=TaskType.TASK_UNDERSTANDING,
            complexity=Complexity.LOW,
            need_structured_output=False,
            cost_sensitivity=CostSensitivity.HIGH,
            latency_budget_ms=10000,
        )

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=512,
            metadata=None,
        )

        try:
            response = self._gateway.generate(request)

            if response.errors:
                logger.warning(
                    f"[QuestionRewrite] LLM 返回错误: {response.errors}"
                )
                return question

            rewritten = response.raw_text.strip() if response.raw_text else question

            if not rewritten:
                return question

            return rewritten

        except Exception as e:
            logger.warning(
                f"[QuestionRewrite] LLM 调用失败, 使用原问题: {e}"
            )
            return question


class QuestionRewriteError(Exception):
    """问题改写错误"""


__all__ = [
    "QuestionRewriteService",
    "QuestionRewriteError",
    "RewriteResult",
    "GroupMessage",
]