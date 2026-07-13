"""
GroupContextService

G9: 群组上下文服务

获取群组会话历史，调用 LLM 生成会话摘要和改写问题。

注意：本服务不使用缓存，因为群组对话是动态变化的，
      每次请求都需要获取最新的会话上下文。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import httpx

from src.domain.models.profile_fusion import GroupConversationSummary
from src.infra.context import get_current_cookie
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType, Complexity, CostSensitivity

if TYPE_CHECKING:
    from src.application.services.llm_gateway_service import LLMGatewayService

logger = logging.getLogger(__name__)

# HTTP 超时配置
_HTTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class GroupMessage:
    """群组消息"""
    sender: str      # 发送者标识
    content: str     # 消息内容
    role: str        # 角色: user 或 assistant
    timestamp: int = 0  # 毫秒级时间戳


class GroupContextService:
    """
    群组上下文服务

    职责：
    1. 从 BCN API 获取群组会话历史
    2. 调用 LLM 生成会话上下文摘要
    3. 调用 LLM 改写问题（补充上下文）

    Attributes:
        _llm_gateway: LLM Gateway 服务
        _bcn_base_url: BCN API 基础 URL
        _context_limit: 获取的最近消息数量
    """

    # 有效角色过滤
    _VALID_ROLES: frozenset[str] = frozenset({"user", "assistant"})
    # 消息内容最大长度
    _MAX_CONTENT_LENGTH: int = 500

    def __init__(
        self,
        llm_gateway: "LLMGatewayService",
        bcn_base_url: str = "",
        context_limit: int = 10,
    ):
        """
        初始化服务

        Args:
            llm_gateway: LLM Gateway 服务
            bcn_base_url: BCN API 基础 URL
            context_limit: 获取的最近消息数量（默认 10 条）
        """
        self._llm_gateway = llm_gateway
        self._bcn_base_url = bcn_base_url.rstrip("/")
        self._context_limit = context_limit

    async def summarize(
        self,
        question: str,
        group_id: str,
    ) -> GroupConversationSummary:
        """
        基于群组会话历史，生成问题改写和会话摘要

        Args:
            question: 原始问题
            group_id: 群组 ID

        Returns:
            GroupConversationSummary: 包含改写问题和会话摘要的结果
        """
        start_time = time.time()
        logger.info(
            "[GroupContext] ========== GroupContext 会话总结开始 =========="
        )
        logger.info(
            "[GroupContext] 请求参数: group_id=%s, question='%s...'",
            group_id,
            question[:50] if len(question) > 50 else question
        )

        # Step 1: 获取群组最近消息
        fetch_start = time.time()
        messages = await self._fetch_group_messages(group_id)
        fetch_elapsed = time.time() - fetch_start

        # Step 1 结果日志
        if not messages:
            total_elapsed = time.time() - start_time
            logger.warning(
                "[GroupContext] ========== GroupContext 完成: 无会话历史 =========="
            )
            logger.warning(
                "[GroupContext] 有效记录: 0 条"
            )
            logger.warning(
                "[GroupContext] 请求耗时: BCN=%.3fs, 总计=%.3fs",
                fetch_elapsed,
                total_elapsed
            )
            logger.warning(
                "[GroupContext] =============================="
            )
            return GroupConversationSummary(
                rewritten_question=question,
                original_question=question,
                context_summary="",
                context_messages_count=0,
                success=True,
                error_message="no_context_messages",
            )

        logger.info(
            "[GroupContext] Step1(BCN请求)完成: 获取到 %d 条消息, 耗时=%.3fs",
            len(messages),
            fetch_elapsed
        )

        # Step 2: 构建上下文文本
        context_text = self._format_messages(messages)
        logger.info(
            "[GroupContext] Step2(格式化)完成: context_text长度=%d chars",
            len(context_text)
        )

        # Step 3: 调用 LLM - 提取与问题相关的上下文补充
        llm_start = time.time()
        _, summary = self._extract_relevant_context(question, context_text)
        llm_elapsed = time.time() - llm_start

        logger.info(
            "[GroupContext] Step3(LLM调用)完成: summary_len=%d, 耗时=%.3fs",
            len(summary) if summary else 0,
            llm_elapsed
        )

        # 总结性日志
        total_elapsed = time.time() - start_time
        logger.info(
            "[GroupContext] ========== GroupContext 完成: 已生成会话摘要 =========="
        )
        logger.info(
            "[GroupContext] 有效记录: %d 条会话消息",
            len(messages)
        )
        logger.info(
            "[GroupContext] 问题保持不变，补充上下文: %d chars",
            len(summary) if summary else 0
        )
        logger.info(
            "[GroupContext] 请求耗时: BCN=%.3fs, LLM=%.3fs, 总计=%.3fs",
            fetch_elapsed,
            llm_elapsed,
            total_elapsed
        )
        logger.info(
            "[GroupContext] =============================="
        )

        return GroupConversationSummary(
            rewritten_question=question,  # 保持原始问题不变
            original_question=question,
            context_summary=summary if summary else "",
            context_messages_count=len(messages),
            success=True,
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

        # 从上下文获取 Cookie（用于 BCN API 认证）
        http_cookie = get_current_cookie(fallback_to_env=False)
        if not http_cookie:
            logger.error("[GroupContext] Cookie 为空: contextvar 中无 cookie，请检查请求是否携带 cookie 或 context 是否正确传递")
            return []
        logger.info(f"[GroupContext] cookie={'yes' if http_cookie else 'no'}({len(http_cookie)})")

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                headers = {}
                if http_cookie:
                    headers["Cookie"] = http_cookie

                response = await client.get(
                    url,
                    params={"limit": self._context_limit},
                    headers=headers if headers else None,
                )

                if response.status_code != 200:
                    logger.error(
                        "[GroupContext] BCN API 失败: url=%s, status=%d, headers=%s, limit=%d",
                        url,
                        response.status_code,
                        json.dumps(headers if headers else {}),
                        self._context_limit
                    )
                    return []

                data = response.json()

                # 检查是否是业务错误响应（如 USER_NOT_LOGIN, PERMISSION_DENIED 等）
                if isinstance(data, dict) and "buserviceErrorCode" in data:
                    error_code = data.get("buserviceErrorCode", "UNKNOWN")
                    error_msg = data.get("buserviceErrorMsg", "")
                    # 截断过长的错误消息
                    if len(error_msg) > 100:
                        error_msg = error_msg[:100] + "..."
                    logger.warning(
                        "[GroupContext] BCN API 业务错误: code=%s, msg=%s, url=%s",
                        error_code,
                        error_msg,
                        url
                    )
                    return []

                return self._parse_messages(data, url)

        except httpx.TimeoutException:
            logger.error(
                "[GroupContext] BCN API 超时: url=%s, timeout=%ds, headers=%s",
                url,
                _HTTP_TIMEOUT_SECONDS,
                json.dumps(headers if headers else {})
            )
            return []
        except Exception as e:
            logger.error(
                "[GroupContext] BCN API 异常: url=%s, error=%s, headers=%s",
                url,
                str(e),
                json.dumps(headers if headers else {})
            )
            return []

    def _parse_messages(self, data: dict | list, url: str = "") -> list[GroupMessage]:
        """
        解析 BCN API 返回的消息数据

        BCN API 返回格式:
        直接数组: [{"id": ..., "sender": ..., "content": ..., "bot_name": ..., "role": ..., "timestamp": ...}, ...]

        字段说明:
        - content: 对话内容（主要字段）
        - bot_name: bot 的名字（可空，用于 assistant 消息显示）
        - sender: 发送者标识（如 "20260402_jwnxe2lv:168640"）
        - role: 角色（user/assistant/tool_result）

        过滤规则:
        - 只保留 role 为 "user" 或 "assistant" 的消息
        - 跳过 content 为空的消息
        - 超长内容截断至 _MAX_CONTENT_LENGTH

        Args:
            data: BCN API 返回的数据
            url: API URL（用于日志）

        Returns:
            解析后的消息列表
        """
        # 提取消息列表
        raw_messages: list[dict]
        if isinstance(data, list):
            # 直接数组格式（BCN 实际返回格式）
            raw_messages = data
        elif isinstance(data, dict) and "messages" in data:
            raw_messages = data["messages"]
        elif isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            raw_messages = data["data"]
        else:
            logger.warning(
                "[GroupContext] 无法解析 BCN API 返回格式: type=%s, keys=%s, url=%s",
                type(data).__name__,
                list(data.keys()) if isinstance(data, dict) else "N/A",
                url
            )
            logger.debug("[GroupContext] 返回数据预览: %s", str(data)[:500])
            return []

        messages: list[GroupMessage] = []

        for item in raw_messages:
            # 提取字段
            role = item.get("role", "user")
            content = item.get("content", "")
            bot_name = item.get("bot_name", "")
            sender = item.get("sender", "unknown")
            timestamp = item.get("timestamp", 0)  # 毫秒级时间戳

            # 过滤无效角色（只保留 user 和 assistant）
            if role not in self._VALID_ROLES:
                continue

            # 过滤空内容
            if not content or not content.strip():
                continue

            # 截断超长内容
            if len(content) > self._MAX_CONTENT_LENGTH:
                content = content[:self._MAX_CONTENT_LENGTH] + "..."

            # 确定显示名称：
            # - user 角色：使用 user
            # - assistant 角色：优先使用 bot_name，否则简化 sender
            if role == "user":
                display_name = "user"
            else:
                # assistant 角色
                if bot_name and bot_name.strip():
                    display_name = bot_name.strip()
                else:
                    # 简化 sender（sender 可能是很长的 bot ID）
                    # 格式如 "20260402_jwnxe2lv:168640"，简化为 "bot"
                    display_name = "bot"

            messages.append(GroupMessage(
                sender=display_name,
                content=content.strip(),
                role=role,
                timestamp=timestamp,
            ))

        # 智能截断：基于总内容长度控制
        # 目标：保留最近的对话，总内容长度控制在合理范围
        messages = self._truncate_messages_by_length(messages)

        return messages

    def _truncate_messages_by_length(self, messages: list[GroupMessage]) -> list[GroupMessage]:
        """
        智能截断消息列表

        策略：
        1. 如果消息数量 <= context_limit，直接返回
        2. 否则，优先保留最近的消息，总内容长度控制在合理范围

        Args:
            messages: 消息列表

        Returns:
            截断后的消息列表
        """
        if len(messages) <= self._context_limit:
            return messages

        # 计算总内容长度
        total_length = sum(len(msg.content) for msg in messages)
        max_total_length = self._context_limit * self._MAX_CONTENT_LENGTH  # 约束总长度

        # 如果总长度不大，直接取最近的 context_limit 条
        if total_length <= max_total_length:
            return messages[-self._context_limit:]

        # 否则，从最近的开始累积，直到达到总长度限制
        result: list[GroupMessage] = []
        current_length = 0

        for msg in reversed(messages):
            msg_length = len(msg.content)
            if current_length + msg_length > max_total_length and result:
                break
            result.insert(0, msg)
            current_length += msg_length

            # 同时限制消息数量
            if len(result) >= self._context_limit:
                break

        return result

    def _format_messages(self, messages: list[GroupMessage]) -> str:
        """
        将消息列表格式化为文本（带时间序号和时间戳）

        消息按时间正序排列，序号越大表示越新的消息。

        Args:
            messages: 消息列表（按时间正序）

        Returns:
            格式化的文本
        """
        from datetime import datetime

        lines: list[str] = []
        total = len(messages)
        for i, msg in enumerate(messages, 1):
            # 序号：显示为倒序，让 LLM 更容易识别最新消息
            # 例如 10 条消息，第一条显示 [1/10]，最后一条显示 [10/10]

            # 时间戳格式化
            time_str = ""
            if msg.timestamp > 0:
                try:
                    # 毫秒时间戳转换为秒
                    dt = datetime.fromtimestamp(msg.timestamp / 1000)
                    time_str = dt.strftime("%H:%M:%S")
                except (ValueError, OSError):
                    time_str = ""

            if time_str:
                lines.append(f"[{i}/{total}] {time_str} {msg.sender}: {msg.content}")
            else:
                lines.append(f"[{i}/{total}] {msg.sender}: {msg.content}")

        return "\n".join(lines)

    def _extract_relevant_context(
        self, question: str, context_text: str
    ) -> tuple[str, str]:
        """
        调用 LLM 提取与问题相关的会话上下文补充信息

        Args:
            question: 原始问题
            context_text: 上下文文本

        Returns:
            tuple[str, str]: (原始问题, 上下文补充信息)
            失败时返回 (原问题, 空字符串)
        """
        system_prompt = """请详细分析会话记录，充分提取与原始问题相关的背景信息。

## 任务说明

- **不要改写原始问题**，保持问题原样
- 充分提取与会话中与**原始问题相关**的所有背景信息和细节
- 尽可能详细、完整地保留原始信息，不要过度精简

## 输出要求

### context_summary（上下文补充）
详细提取与原始问题相关的所有背景信息，包括但不限于：
- 用户在会话中提到的**具体需求细节**（与问题相关）
- **相关的约束条件、限制要求或偏好**
- **历史决策点、讨论结论或已确认的信息**
- **关键的时间、地点、人物、条件等要素**
- **相关的技术选型、架构方案或实现细节**
- **任何可能影响问题理解和解决的相关背景**

输出原则：
- **尽可能详细**，保留原始信息的完整性
- 多角度、多层次地提取相关背景
- 如果信息量大，优先保留最新的、最相关的信息

如果没有与原始问题相关的信息，输出："(无背景补充)"

注意：
- 只提取与**原始问题相关**的信息
- 目的是提供丰富的背景帮助理解问题，不是改变问题本身
- **必须始终有输出**，不能返回空字符串

## 输出格式

```json
{
  "context_summary": "详细的与原始问题相关的会话背景信息..."
}
```"""

        user_prompt = f"""【原始问题】

{question}

【会话记录】（按时间正序，序号越大越新，最后一条是最新的）

{context_text}

====================

**请详细分析上述会话记录，充分提取与原始问题相关的所有背景信息，输出 JSON。**

要求：
1. 不要改写原始问题
2. 尽可能详细地提取相关背景，保留丰富细节
3. 从多个角度提取信息：需求、约束、条件、历史决策等"""

        task_spec = LLMTaskSpec(
            task_type=TaskType.TASK_UNDERSTANDING,
            complexity=Complexity.MEDIUM,
            need_structured_output=True,
            cost_sensitivity=CostSensitivity.HIGH,
            latency_budget_ms=15000,
        )

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=2048,
        )

        try:
            response = self._llm_gateway.generate(request)

            if response.errors:
                logger.warning("[GroupContext] LLM 调用返回错误: %s", response.errors)
                return question, ""

            raw_text = response.raw_text.strip() if response.raw_text else ""
            if not raw_text:
                logger.warning("[GroupContext] LLM 返回空内容")
                return question, ""

            # 解析 JSON 响应
            import json

            # 尝试提取 JSON 块
            json_text = raw_text
            if "```json" in raw_text:
                # 提取 ```json ... ``` 之间的内容
                start = raw_text.find("```json") + 7
                end = raw_text.find("```", start)
                if end > start:
                    json_text = raw_text[start:end].strip()
            elif "```" in raw_text:
                # 提取 ``` ... ``` 之间的内容
                start = raw_text.find("```") + 3
                end = raw_text.find("```", start)
                if end > start:
                    json_text = raw_text[start:end].strip()

            result = json.loads(json_text)
            summary = result.get("context_summary", "") or ""

            logger.info(
                "[GroupContext] LLM 返回成功: summary_len=%d",
                len(summary)
            )

            # 保持原始问题不变，只返回相关的上下文补充
            return question, summary

        except json.JSONDecodeError as e:
            logger.warning("[GroupContext] JSON 解析失败: %s, raw_text=%s", str(e), raw_text[:200])
            return question, ""
        except Exception as e:
            logger.warning("[GroupContext] LLM 调用失败: %s", str(e))
            return question, ""


__all__ = [
    "GroupContextService",
    "GroupConversationSummary",
    "GroupMessage",
]