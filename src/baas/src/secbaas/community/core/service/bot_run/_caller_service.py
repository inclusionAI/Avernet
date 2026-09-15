from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    BotResponse,
    BotServiceError,
    MessageInfo,
    SessionInfo,
)
from secbaas.community.api.sse import StreamChunk

from ._internal_protocols import BotService


class CallerBotService(BotService):
    """caller 模式 BotService：连接到 caller-connection 指定的 sandbox。

    组合（而非继承）一个内层 ``BotService`` 作为传输实现，caller 专属的
    sandbox 校验只留在本类，达到解耦。
    """

    def __init__(self, inner: BotService) -> None:
        self._inner = inner

    def _require_sandbox(self, binding_info: BotBindingInfo) -> BotBindingInfo:
        """确保 binding 携带 caller 显式指定的 sandbox_id。

        caller 模式的容器由 caller-connection 预先拉起，必须显式带 sandbox_id；
        缺失说明上游没走 caller-connection，直接失败而不是退回 affinity 选设备。
        """
        if not binding_info.sandbox_id:
            raise BotServiceError(
                "CallerBotService requires an explicit sandbox_id in binding_info; "
                "provision it via caller-connection before dispatching."
            )
        return binding_info

    async def create_session(
        self,
        *,
        bot_id: str,
        session_id: str | None = None,
        metadata: dict[str, Any],
        binding_info: BotBindingInfo,
        context: BotChatContext,
        run_id: str | None = None,
    ) -> SessionInfo:
        return await self._inner.create_session(
            bot_id=bot_id,
            session_id=session_id,
            metadata=metadata,
            binding_info=self._require_sandbox(binding_info),
            context=context,
            run_id=run_id,
        )

    async def send_message(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        wait_result: bool = True,
        context: BotChatContext | None = None,
        timeout: float,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
        session_pending: bool = False,
    ) -> BotResponse:
        return await self._inner.send_message(
            session_id=session_id,
            message=message,
            binding_info=self._require_sandbox(binding_info),
            wait_result=wait_result,
            context=context,
            timeout=timeout,
            chat_metadata=chat_metadata,
            attachments=attachments,
            session_pending=session_pending,
        )

    async def send_message_stream(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: float,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
        session_pending: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        async for chunk in self._inner.send_message_stream(
            session_id=session_id,
            message=message,
            binding_info=self._require_sandbox(binding_info),
            context=context,
            timeout=timeout,
            attachments=attachments,
            session_pending=session_pending,
            chat_metadata=chat_metadata,
        ):
            yield chunk

    async def inject_message(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        attachments: list[Any] | None = None,
        session_pending: bool = False,
    ) -> None:
        await self._inner.inject_message(
            session_id=session_id,
            message=message,
            binding_info=self._require_sandbox(binding_info),
            context=context,
            attachments=attachments,
            session_pending=session_pending,
        )

    async def get_session(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> SessionInfo:
        return await self._inner.get_session(
            session_id=session_id,
            binding_info=self._require_sandbox(binding_info),
            context=context,
        )

    async def get_messages(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> list[MessageInfo]:
        return await self._inner.get_messages(
            session_id=session_id,
            binding_info=self._require_sandbox(binding_info),
            context=context,
        )

    async def list_sessions(
        self,
        *,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SessionInfo]:
        return await self._inner.list_sessions(
            binding_info=self._require_sandbox(binding_info),
            context=context,
            limit=limit,
            offset=offset,
        )
