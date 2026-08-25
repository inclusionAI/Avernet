"""BCN 下行协议服务接口定义

定义 BCN 下行协议 (chat.send / chat.inject / chat.history) 的服务接口契约。
"""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from secbaas.community.api.sse import StreamChunk

from ._models import (
    BcnInteractionResolveInput,
    BcnInteractionResolveResult,
    ChatAbortInput,
    ChatAbortResult,
    ChatHistoryInput,
    ChatHistoryResult,
    ChatInjectInput,
    ChatInjectResult,
    ChatSendInput,
    ChatSendResult,
)


@runtime_checkable
class BcnDownlinkService(Protocol):
    """BCN 下行协议服务接口

    Provider 侧实现该接口，处理来自 BCN 的下行请求。
    """

    async def handle_chat_send(self, chat_send_input: ChatSendInput) -> ChatSendResult:
        """处理 chat.send 请求（非流式）

        BCN 请求某个 Bot 对当前会话轮次进行响应。
        Provider 应快速返回 200 OK，异步执行 Bot 逻辑，
        完成后通过 /v1/bot/events 回调 BCN。

        Args:
            chat_send_input: chat.send 请求的领域输入

        Returns:
            ChatSendResult: 处理结果

        Raises:
            BcnProviderIdMismatchError: provider_id 不匹配
            BcnIdempotencyConflictError: 幂等键冲突
            BcnBotNotFoundError: Bot 不存在
        """
        ...

    async def handle_chat_send_stream(
        self,
        chat_send_input: ChatSendInput,
    ) -> AsyncIterator[StreamChunk]:
        """处理 chat.send 请求（流式）

        与 handle_chat_send 不同，此方法同步流式返回 Bot 执行的
        原始 StreamChunk 序列，由调用方（router 层）通过
        StreamConverter 插件转换为 BCN SSE 事件。

        执行流程:
        1. 校验 to_bot.provider_id
        2. 不做幂等：重复 run_id 直接 raise ValueError（→ HTTP 400）
        3. 调用 BotRunner 执行 Bot，获取 StreamChunk 流
        4. 流结束后不再触发 uplink 回调（结果已通过流式返回）

        Args:
            chat_send_input: chat.send 请求的领域输入

        Returns:
            AsyncIterator[StreamChunk]: Bot 执行的原始流式 chunk

        Raises:
            ValueError: 重复 run_id（流式模式不允许幂等）
            BcnBotNotFoundError: Bot 不存在
        """
        ...

    async def handle_chat_inject(
        self, chat_inject_input: ChatInjectInput
    ) -> ChatInjectResult:
        """处理 chat.inject 请求

        BCN 向 Bot 注入一条消息，Provider 需要把它纳入 session state，
        但不触发推理，也不需要回调 /v1/bot/events。

        Args:
            chat_inject_input: chat.inject 请求的领域输入

        Returns:
            ChatInjectResult: 处理结果

        Raises:
            BcnProviderIdMismatchError: provider_id 不匹配
            BcnIdempotencyConflictError: 幂等键冲突
            BcnBotNotFoundError: Bot 不存在
        """
        ...

    async def handle_chat_history(
        self, chat_history_input: ChatHistoryInput
    ) -> ChatHistoryResult:
        """处理 chat.history 请求

        BCN 向 Provider 查询指定 Bot 在某个会话中的聊天历史。
        Provider 必须在 HTTP 响应中同步返回消息列表。

        Args:
            chat_history_input: chat.history 请求的领域输入

        Returns:
            ChatHistoryResult: 聊天历史结果

        Raises:
            BcnProviderIdMismatchError: provider_id 不匹配
            BcnSessionNotFoundError: 会话不存在
            BcnInvalidRequestError: 请求参数错误
        """
        ...

    async def handle_interaction_resolve(
        self, resolve_input: BcnInteractionResolveInput
    ) -> BcnInteractionResolveResult:
        """Persist a BCN interaction answer for the Engine websocket owner."""
        ...

    async def handle_chat_abort(
        self, chat_abort_input: ChatAbortInput
    ) -> ChatAbortResult:
        """处理 chat.abort 请求

        BCN 请求中止某 session 下运行中的 run。Provider 应：
        1. 按 session_id 定位 RUNNING/PENDING 的 run；
        2. 取消本机正在执行的 task、标记 run 终态（FAILED）、终结队列工作项；
        3. 出站通知 engine（best-effort）；
        4. 幂等：run 已终态时抛 ``BcnRunTerminatedError`` (410)，重复 abort 稳定 410；
           session 无任何 run 记录时返回 ``{aborted: False, aborted_run_ids: []}``。

        Args:
            chat_abort_input: chat.abort 请求的领域输入

        Returns:
            ChatAbortResult: 是否取消及被取消的 run_id 列表

        Raises:
            BcnRunTerminatedError: session 下 run 已为终态
        """
        ...
