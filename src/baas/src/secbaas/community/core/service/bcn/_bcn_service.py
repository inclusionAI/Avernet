"""BCN 下行协议服务默认实现

实现 BcnDownlinkService Protocol，处理来自 BCN 的下行请求:
  - chat.send: 请求 Bot 对当前会话轮次进行响应
  - chat.inject: 向 Bot 注入消息（不触发推理）
  - chat.history: 查询聊天历史
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from secbaas.community.api.bcn import (
    BcnDownlinkService,
    BcnInteractionResolveInput,
    BcnInteractionResolveResult,
    ChatHistoryInput,
    ChatHistoryResult,
    ChatInjectInput,
    ChatInjectResult,
    ChatSendInput,
    ChatSendResult,
    DownlinkMessage,
)
from secbaas.community.api.bot_interaction import (
    BotInteractionService,
    InteractionResolution,
)
from secbaas.community.api.bot_runtime import BotChatContext, BotRunner
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.api_gateway import APIKeyRepository
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.core.service.bcn.uplink import BcnUplinkCallback, UplinkClient
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


def _extract_message_text(message: DownlinkMessage) -> str:
    """将 DownlinkMessage 转换为纯文本字符串

    Args:
        message: 下行消息结构

    Returns:
        纯文本字符串
    """
    content = message.content
    if isinstance(content, str):
        return content
    # content 是 list[ContentBlock] 时，拼接所有 text 类型块
    parts: list[str] = []
    for block in content:
        if block.type == "text" and block.text is not None:
            parts.append(block.text)
    return "\n".join(parts)


def _normalize_interaction_resolution(
    resolve_input: BcnInteractionResolveInput,
) -> InteractionResolution:
    """Convert BCN fields to the Engine-neutral durable resolution."""
    if resolve_input.kind != "ask_user":
        decision = resolve_input.decision
        if decision is None or not decision.strip():
            raise ValueError(
                f"{resolve_input.kind} interaction resolve requires decision"
            )
        return InteractionResolution(
            kind=resolve_input.kind,
            decision=decision,
        )

    action = resolve_input.action
    if action not in {"submit", "cancel"}:
        raise ValueError("ask_user interaction resolve requires action")
    if action == "cancel":
        return InteractionResolution(kind="ask_user", decision="cancel")

    source_answers = resolve_input.answers
    if not source_answers:
        raise ValueError("ask_user submit requires answers")

    summaries: list[str] = []
    values: dict[str, str] = {}
    answers: dict[str, str] = {}
    selected_options: list[tuple[str, ...]] = []
    for question_id, source_answer in source_answers.items():
        if (
            not question_id.strip()
            or not source_answer.header.strip()
            or not source_answer.question.strip()
        ):
            raise ValueError("ask_user answer identity must be non-empty")
        joined_values = "，".join(source_answer.values)
        summaries.append(f"{source_answer.header}: {joined_values}")
        if source_answer.header in values:
            logger.warning(
                "Interaction resolution warning: interaction_id=%s "
                "field_path=answers.header error_type=duplicate_answer_header",
                resolve_input.interaction_id,
            )
        values[source_answer.header] = joined_values
        answers[source_answer.question] = joined_values
        selected_options.append(tuple(source_answer.values))

    summary = "；".join(summaries)
    return InteractionResolution(
        kind="ask_user",
        decision="submit",
        answer=summary,
        message=summary,
        values=values,
        answers=answers,
        selected_options=tuple(selected_options),
    )


class DefaultBcnDownlinkService(BcnDownlinkService):
    """BCN 下行协议服务默认实现

    处理 BCN -> Provider 的下行请求，包括:
    - chat.send: 路由 -> 写 session state -> 异步执行 Bot
    - chat.inject: 路由 -> 写 session state（不触发推理）
    - chat.history: 定位会话 -> 分页 -> 返回消息列表
    """

    def __init__(
        self,
        bot_runner: BotRunner,
        api_key_repository: APIKeyRepository,
        bcn_api_key_prefix: str,
        uplink_client: UplinkClient,
        run_repository: BotRunRepository,
        interaction_service: BotInteractionService,
    ):
        self._bot_runner = bot_runner
        self._api_key_repository = api_key_repository
        self._bcn_api_key_prefix = bcn_api_key_prefix
        self._uplink_client = uplink_client
        self._run_repository = run_repository
        self._interaction_service = interaction_service
        self._uplink_callback = BcnUplinkCallback(uplink_client, run_repository)

    async def handle_interaction_resolve(
        self, resolve_input: BcnInteractionResolveInput
    ) -> BcnInteractionResolveResult:
        """Normalize and durably queue one BCS interaction resolution."""
        resolution = _normalize_interaction_resolution(resolve_input)
        self._interaction_service.resolve(
            baas_interaction_id=resolve_input.interaction_id,
            resolution=resolution,
            request_envelope=resolve_input.request_envelope,
            idempotency_key=resolve_input.idempotency_key,
        )
        return BcnInteractionResolveResult(ok=True)

    async def handle_chat_send(self, chat_send_input: ChatSendInput) -> ChatSendResult:
        """处理 chat.send 请求

        Provider 收到后应:
        1. 校验 to_bot.provider_id 与自身 Provider ID 一致
        2. 按 id (run_id) 幂等去重
        3. 按 to_bot.provider_bot_ref 路由到内部 Bot
        4. 将 message 写入 (provider_bot_ref, session_id) 的 session state
        5. 快速返回 200 OK
        6. 异步执行 Bot 逻辑，完成后回调 /bot/events
        """
        logger.info(
            "[chat.send] run_id=%s session_id=%s provider_id=%s "
            "provider_bot_ref=%s bcn_group_id=%s timeout_ms=%s",
            chat_send_input.run_id,
            chat_send_input.session_id,
            chat_send_input.to_bot.provider_id,
            chat_send_input.to_bot.provider_bot_ref,
            chat_send_input.bcn_group_id,
            chat_send_input.timeout_ms,
        )

        ignore_result = False
        if (
            chat_send_input.extensions
            and chat_send_input.extensions.get("caller_wait_mode") == "detached"
        ):
            ignore_result = True
            logger.info(
                "[chat.send] run_id=%s session_id=%s provider_id=%s, ignore result",
                chat_send_input.run_id,
                chat_send_input.session_id,
                chat_send_input.to_bot.provider_id,
            )

        # 构造 context、metadata、消息
        context = self._build_chat_context()
        metadata = self._build_bcn_metadata(
            chat_send_input.session_id,
            chat_send_input.bcn_group_id,
            chat_send_input.to_bot.tags,
            ignore_result,
            "chat",
        )
        metadata["timeout"] = chat_send_input.timeout_ms / 1000
        message_text = _extract_message_text(chat_send_input.message)
        attachments = chat_send_input.attachments

        async def _async_deliver() -> None:
            try:
                message_id, _session_id = await self._bot_runner.deliver_message(
                    bot_id=chat_send_input.to_bot.provider_bot_ref,
                    message=message_text,
                    context=context,
                    metadata=metadata,
                    message_id=chat_send_input.run_id,
                    callback="bcn_uplink",
                    attachments=attachments,
                )
                logger.info(
                    "[chat.send] Message delivered: run_id=%s message_id=%s session_id=%s",
                    chat_send_input.run_id,
                    message_id,
                    _session_id,
                )
            except Exception as e:
                logger.exception(
                    "[chat.send] deliver_message failed: run_id=%s err=%s",
                    chat_send_input.run_id,
                    e,
                )
                self._run_repository.update_error(
                    run_id=chat_send_input.run_id,
                    error="Message delivery failed",
                )
                await self._uplink_callback(chat_send_input.run_id)

        asyncio.create_task(_async_deliver())

        logger.info(
            "[chat.send] async message delivered: run_id=%s", chat_send_input.run_id
        )

        return ChatSendResult(ok=True)

    async def handle_chat_send_stream(
        self,
        chat_send_input: ChatSendInput,
    ) -> AsyncIterator[StreamChunk]:
        """处理 chat.send 流式请求

        调用 deliver_message_stream，返回 AsyncIterator[StreamChunk]。
        重复 run_id 会 raise ValueError（由 router 层捕获转为 HTTP 400）。
        """
        logger.info(
            "[chat.send.stream] run_id=%s session_id=%s provider_bot_ref=%s",
            chat_send_input.run_id,
            chat_send_input.session_id,
            chat_send_input.to_bot.provider_bot_ref,
        )

        context = self._build_chat_context()
        metadata = self._build_bcn_metadata(
            chat_send_input.session_id,
            chat_send_input.bcn_group_id,
            chat_send_input.to_bot.tags,
            request_type="chat",
        )
        metadata["timeout"] = chat_send_input.timeout_ms / 1000
        metadata["stream"] = "true"
        message_text = _extract_message_text(chat_send_input.message)
        attachments = chat_send_input.attachments

        (
            _run_id,
            _session_id,
            stream_iter,
        ) = await self._bot_runner.deliver_message_stream(
            bot_id=chat_send_input.to_bot.provider_bot_ref,
            message=message_text,
            context=context,
            metadata=metadata,
            message_id=chat_send_input.run_id,
            attachments=attachments,
        )

        return stream_iter

    async def handle_chat_inject(
        self, chat_inject_input: ChatInjectInput
    ) -> ChatInjectResult:
        """处理 chat.inject 请求

        Provider 收到后必须:
        - 按 id 幂等去重
        - 校验 to_bot.provider_id 与自身 Provider ID 一致
        - 按 to_bot.provider_bot_ref 路由到内部 Bot
        - 将消息写入 (provider_bot_ref, session_id) 的 session state
        - 不触发 Bot 推理
        - 返回 200 {"ok": true}
        """
        logger.info(
            "[chat.inject] id=%s session_id=%s provider_id=%s "
            "provider_bot_ref=%s bcn_group_id=%s",
            chat_inject_input.id,
            chat_inject_input.session_id,
            chat_inject_input.to_bot.provider_id,
            chat_inject_input.to_bot.provider_bot_ref,
            chat_inject_input.bcn_group_id,
        )

        # 构造 context、metadata、消息
        context = self._build_chat_context()
        metadata = self._build_bcn_metadata(
            chat_inject_input.session_id,
            chat_inject_input.bcn_group_id,
            chat_inject_input.to_bot.tags,
            request_type="inject",
        )
        message_text = _extract_message_text(chat_inject_input.message)
        attachments = chat_inject_input.attachments

        try:
            message_id, _session_id = await self._bot_runner.inject_message(
                bot_id=chat_inject_input.to_bot.provider_bot_ref,
                message=message_text,
                context=context,
                metadata=metadata,
                message_id=chat_inject_input.id,
                attachments=attachments,
            )
        except Exception as e:
            logger.error(
                "[chat.inject] inject_message failed before acknowledgement: id=%s err=%s",
                chat_inject_input.id,
                e,
            )
            return ChatInjectResult(ok=False)

        logger.info(
            "[chat.inject] persisted: id=%s message_id=%s session_id=%s",
            chat_inject_input.id,
            message_id,
            _session_id,
        )
        return ChatInjectResult(ok=True)

    async def handle_chat_history(
        self, chat_history_input: ChatHistoryInput
    ) -> ChatHistoryResult:
        """处理 chat.history 请求

        Provider 收到后应:
        1. 校验 to_bot.provider_id 与自身 Provider ID 一致
        2. 按 (provider_bot_ref, session_id) 定位会话历史
        3. 按 limit / before / after 过滤和分页
        4. 同步返回消息列表
        """
        logger.info(
            "[chat.history] id=%s session_id=%s provider_id=%s "
            "provider_bot_ref=%s limit=%s before=%s after=%s",
            chat_history_input.id,
            chat_history_input.session_id,
            chat_history_input.to_bot.provider_id,
            chat_history_input.to_bot.provider_bot_ref,
            chat_history_input.limit,
            chat_history_input.before,
            chat_history_input.after,
        )

        # 获取消息
        try:
            context = self._build_chat_context()
            metadata = self._build_bcn_metadata(
                chat_history_input.session_id,
                chat_history_input.bcn_group_id,
                chat_history_input.to_bot.tags,
            )
            raw_messages = await self._bot_runner.get_messages(
                bot_id=chat_history_input.to_bot.provider_bot_ref,
                session_id=chat_history_input.session_id,
                context=context,
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(
                "[chat.history] Failed to get messages: session_id=%s error=%s",
                chat_history_input.session_id,
                e,
            )
            return ChatHistoryResult(
                ok=False,
                session_id=chat_history_input.session_id,
                messages=[],
                has_more=False,
            )

        # 将 MessageInfo 列表转换为 HistoryMessage 列表
        from datetime import datetime

        from secbaas.community.api.bcn import HistoryMessage

        def _parse_timestamp(created_at: str | None) -> int:
            """将 ISO 8601 格式的 created_at 转换为 Unix 时间戳（毫秒）"""
            if created_at is None:
                return 0
            try:
                dt = datetime.fromisoformat(created_at)
                ts_sec = int(dt.timestamp())
                ts_ms = ts_sec * 1000 + dt.microsecond // 1000
                return ts_ms
            except (ValueError, OSError):
                return 0

        messages: list[HistoryMessage] = []
        for msg in raw_messages:
            messages.append(
                HistoryMessage(
                    role=msg.role,
                    content=msg.content,
                    timestamp=_parse_timestamp(msg.created_at),
                    id=msg.id or f"{chat_history_input.session_id}-{msg.id}",
                )
            )

        return ChatHistoryResult(
            ok=True,
            session_id=chat_history_input.session_id,
            messages=messages,
            has_more=False,
        )

    def _build_chat_context(self) -> BotChatContext:
        """构造 BCN 场景的 BotChatContext

        使用 BCN 固定 api-key，不需要用户侧 IAM Token。
        """
        api_key_record = self._api_key_repository.get_by_prefix(
            self._bcn_api_key_prefix
        )
        if not api_key_record:
            raise ValueError("api key not found")
        return BotChatContext.from_api_key(
            api_key_prefix=api_key_record.api_key_prefix,
            app_id=api_key_record.app_id,
            app_type=api_key_record.app_type or "UNKNOWN",
            tenant=api_key_record.tenant or "",
        )

    @staticmethod
    def _build_bcn_metadata(
        session_id: str,
        bcn_group_id: str,
        tags: list[str] | None = None,
        ignore_result: bool = False,
        request_type: str = "",
    ) -> dict[str, Any]:
        """构造 BCN 场景的 metadata

        Args:
            session_id: 会话 ID
            bcn_group_id: BCN group ID
            tags: Bot 标签列表，用于提取 lifecycle_stage

        Returns:
            metadata 字典
        """
        # 从 tags 中提取 lifecycle_stage（仅接受 online / verify / draft）
        valid_stages = {"online", "verify", "draft"}
        # bcn特殊逻辑，设定lifecycle_stage=all，会按照 online -> verify -> draft 的顺序找bot
        lifecycle_stage = "all"
        if tags:
            for tag in tags:
                if tag in valid_stages:
                    lifecycle_stage = tag
                    break

        return {
            "session_id": session_id,
            "bcn_group_id": bcn_group_id,
            "ignore_result": "true" if ignore_result else "false",
            "bot_options": {"lifecycle_stage": lifecycle_stage, "from_bcn": "true"},
            "request_type": request_type,
        }
