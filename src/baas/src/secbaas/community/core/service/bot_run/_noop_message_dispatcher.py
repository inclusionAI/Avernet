"""NoopMessageDispatcher — 不执行消息分发

规则 21：每个 Protocol 必须有 Noop 实现。
当 MessageDispatcher 被 DI 需求但消息分发
不需要或未配置时使用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from secbaas.community.api.bot_runtime import BotBindingInfo, BotChatContext
from secbaas.community.api.sse import StreamChunk
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")


class NoopMessageDispatcher:
    """Noop MessageDispatcher 实现

    规则 21：每个 Protocol 必须有 Noop 实现。
    所有分发调用记录 warning 日志但不执行任何操作。
    """

    @property
    def order(self) -> int:
        return 0

    def accepts(self, bot_id: str) -> bool:
        return True

    async def dispatch_send(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        wait_result: bool = True,
        timeout: float,
        bot_id: str = "",
        callback: Any = None,
        chat_metadata: dict[str, str] | None = None,
    ) -> None:
        logger.warning(
            "NoopMessageDispatcher.dispatch_send called: run_id=%s, "
            "message will NOT be sent",
            run_id,
        )

    def dispatch_send_stream(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: float,
        bot_id: str = "",
    ) -> AsyncIterator[StreamChunk]:
        logger.warning(
            "NoopMessageDispatcher.dispatch_send_stream called: run_id=%s, "
            "returning empty iterator",
            run_id,
        )

        async def _empty() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(
                type="error", content="NoopMessageDispatcher: stream not supported"
            )

        return _empty()

    async def dispatch_inject(
        self,
        *,
        bot_service: Any,
        run_id: str,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        bot_id: str = "",
    ) -> None:
        logger.warning(
            "NoopMessageDispatcher.dispatch_inject called: run_id=%s, "
            "message will NOT be injected",
            run_id,
        )
