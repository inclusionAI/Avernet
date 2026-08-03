"""ChatClient-based implementation of BotService Protocol.

Provides ClawBotService that communicates with external Bot WebSocket service
via the OpenClaw v3 protocol, using ChatClient as the underlying client.
Also integrates with AsyncSessionClient for adapter-side session management.

Design: 每个 API 方法按 binding_info 从连接池获取已握手的 client，
按 sandbox_id 复用 WS 连接（一次握手，多次复用）。
不同 sessionKey 的消息可并行，同一 sessionKey 并发会被拒绝。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiohttp
from pydantic import BaseModel, Field

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    BotResponse,
    BotServiceError,
    MessageInfo,
    SessionInfo,
    SessionNotFoundError,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.logger import get_logger
from secbaas.community.spi.secret import SecretStorePlugin

from ._async_chat_client import ConcurrentSessionError
from ._async_chat_client_pool import AsyncChatClientPool
from ._async_session_client import AsyncSessionClient
from ._async_session_client import SessionInfo as AdapterSessionInfo
from ._bot_run_utils import resolve_user_id
from ._internal_protocols import BotService

if TYPE_CHECKING:
    from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

    from ._engine_adapter_registry import BotEngineAdapterRegistry

logger = get_logger("core-bot-run")


class BotServiceConfig(BaseModel):
    """Configuration for Bot WebSocket service."""

    proxy_base_url: str
    proxy_ws_base_url: str
    adapter_port: int = Field(ge=1, le=65535)
    connect_timeout: int = Field(default=10, gt=0)
    request_timeout: int = Field(default=30, gt=0)


class ClawBotService(BotService):
    """ChatClient-based implementation of BotService Protocol.

    Communicates with external Bot service via OpenClaw WebSocket protocol.
    Uses AsyncChatClientPool for sandbox-level connection reuse — each sandbox
    has one shared WS connection; multiple requests can concurrently use the
    same client (AsyncChatClient multiplexes via sessionKey).

    Different sessionKeys can be multiplexed on the same connection.
    Same sessionKey concurrent requests will be rejected by AsyncChatClient.

    binding_info is required (resolved by BotBindingResolver upstream).
    """

    # ── 构造 ─────────────────────────────────────────────────────────────────

    def __init__(
        self,
        config: BotServiceConfig,
        client_pool: AsyncChatClientPool,
        secret_store: SecretStorePlugin,
        engine_adapter_registry: BotEngineAdapterRegistry | None = None,
    ) -> None:
        """Initialize the Bot service.

        Args:
            config: Configuration for the Bot WebSocket service.
            client_pool: Connection pool for sandbox-level WS connection reuse.
            secret_store: Secret store plugin for token generation.
            engine_adapter_registry: Optional registry for engine adapters
                (aicoding / hermes / claude_code). When set and engine_type
                matches a registered adapter, _build_ws_url uses
                adapter.ws_path() instead of the default f"/api/{engine}/ws".
        """
        self._config = config
        self._client_pool = client_pool
        self._secret_store = secret_store
        self._engine_adapter_registry = engine_adapter_registry

    # ── 公开方法 (BotService Protocol) ───────────────────────────────────────

    async def create_session(
        self,
        *,
        bot_id: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        binding_info: BotBindingInfo | None = None,
        context: BotChatContext | None = None,
        run_id: str | None = None,
    ) -> SessionInfo:
        """Create a new conversation session via AsyncSessionClient.

        Only creates the adapter-side session. WS connections are managed
        by each API method via the connection pool (binding_info.sandbox_id).

        binding_info is required — callers MUST resolve binding info first.

        Args:
            bot_id: Bot identifier in format "real_bot_id:entity_id".
            session_id: Optional session identifier to reuse.
            metadata: Optional session metadata.
            binding_info: Cached binding info (required, use BotBindingResolver).
            context: Optional request context.
            run_id: Optional run ID for correlating session with run record.

        Returns:
            SessionInfo: The created or reused session information.

        Raises:
            BotServiceError: If binding_info is not provided.
        """
        logger.info("Create session: %s, %s", bot_id, session_id)

        if binding_info is None:
            raise BotServiceError(
                "ClawBotService requires binding_info to create session. "
                "Use BotBindingResolver to resolve binding info before calling create_session."
            )

        sandbox_id = binding_info.sandbox_id
        real_bot_id = binding_info.bot_id
        entity_id = binding_info.entity_id
        if sandbox_id is None:
            raise BotServiceError(
                "ClawBotService requires sandbox_id in binding_info. "
                "Non-arca devices should use BaasBotService."
            )
        logger.info(
            f"[create_session] Using binding_info: bot_id={real_bot_id}, "
            f"sandbox_id={sandbox_id}, "
            f"device_provider={binding_info.device_provider}"
        )

        # AsyncSessionClient 是 HTTP 无状态短连接，无需池化
        metadata = metadata or {}
        session_client = self._create_session_client(sandbox_id)
        user_id = resolve_user_id(metadata, binding_info, context, entity_id)

        try:
            async with session_client:
                adapter_session_id, reused = await self._get_or_create_adapter_session(
                    session_client=session_client,
                    session_id=session_id,
                    user_id=user_id,
                    metadata=metadata,
                    run_id=run_id,
                )
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning(f"Failed to get or create adapter session: {e}")
            raise BotServiceError(
                f"Failed to get or create adapter session for bot {bot_id}: {e}"
            ) from e

        action = "reused" if reused else "created"
        logger.info(
            f"Session {action}: session_id={adapter_session_id}, "
            f"bot_id={bot_id}, sandbox_id={sandbox_id}"
        )

        return SessionInfo(
            session_id=adapter_session_id,
            bot_id=bot_id,
            status="active",
            created_at=datetime.now(),
            metadata=metadata,
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
    ) -> BotResponse:
        """Send a message and get response via ChatClient.

        Gets the shared connection for the sandbox from the pool and sends
        the message. The connection remains in the pool for reuse by other
        requests (AsyncChatClient multiplexes via sessionKey).
        Same sessionKey concurrent requests will raise an error.

        Args:
            session_id: The session identifier.
            message: The message content to send.
            binding_info: Binding info for WS connection.
            wait_result: Whether to wait for result.
            context: Optional request context.
            timeout: Optional timeout in seconds. None means no limit.

        Returns:
            BotResponse: The bot's response.

        Raises:
            BotServiceError: If binding_info is invalid or request fails.
        """
        sandbox_id = binding_info.sandbox_id
        engine_type = binding_info.engine_type
        if sandbox_id is None:
            raise BotServiceError("ClawBotService requires sandbox_id in binding_info.")
        if engine_type is None:
            raise BotServiceError(
                "ClawBotService requires engine_type in binding_info."
            )

        url = self._build_ws_url(sandbox_id, engine_type)
        headers = self._get_headers(sandbox_id)

        client = await self._client_pool.get(sandbox_id, url, headers)
        try:
            auth_token = context.build_auth_token() if context else None
            app_id = context.app_id if context else None
            content, _agent_events = await client.send_message(
                message=message,
                session_key=session_id,
                wait_result=wait_result,
                timeout=timeout,
                auth_token=auth_token,
                app_id=app_id,
                chat_metadata=chat_metadata,
            )
            return BotResponse(content=content)
        except TimeoutError:
            raise
        except ConcurrentSessionError as e:
            raise BotServiceError(
                f"Concurrent request on session {session_id}: {e}"
            ) from e
        except Exception as e:
            raise BotServiceError(f"Failed to send message: {e}") from e

    async def send_message_stream(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: float,
    ) -> AsyncIterator[StreamChunk]:
        """流式发送消息，逐 chunk 产出 StreamChunk。

        与 send_message 相同的连接逻辑，但调用
        client.send_message_stream 并返回 AsyncIterator。
        """
        sandbox_id = binding_info.sandbox_id
        engine_type = binding_info.engine_type
        if sandbox_id is None:
            raise BotServiceError("ClawBotService requires sandbox_id in binding_info.")
        if engine_type is None:
            raise BotServiceError(
                "ClawBotService requires engine_type in binding_info."
            )

        url = self._build_ws_url(sandbox_id, engine_type)
        headers = self._get_headers(sandbox_id)

        client = await self._client_pool.get(sandbox_id, url, headers)
        auth_token = context.build_auth_token() if context else None
        app_id = context.app_id if context else None

        try:
            async for chunk in client.send_message_stream(
                message=message,
                session_key=session_id,
                timeout=timeout,
                auth_token=auth_token,
                app_id=app_id,
            ):
                yield replace(chunk, engine_type=engine_type)
        except BotServiceError:
            raise
        except ConcurrentSessionError as e:
            raise BotServiceError(
                f"Concurrent request on session {session_id}: {e}"
            ) from e
        except Exception as e:
            raise BotServiceError(f"Failed to send message stream: {e}") from e

    async def inject_message(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> None:
        """注入消息到已有会话

        与 send_message 不同，inject_message 不返回响应结果（返回 None），
        适用于注入系统指令、上下文补充等不需要等待响应的场景。

        Args:
            session_id: 会话 ID
            message: 注入的消息内容
            binding_info: Binding info for WS connection.
            context: 可选的请求上下文（身份认证、调用者信息等）
        """
        sandbox_id = binding_info.sandbox_id
        engine_type = binding_info.engine_type
        if sandbox_id is None:
            raise BotServiceError("ClawBotService requires sandbox_id in binding_info.")

        url = self._build_ws_url(sandbox_id, engine_type or "openclaw")
        headers = self._get_headers(sandbox_id)

        client = await self._client_pool.get(sandbox_id, url, headers)
        try:
            auth_token = context.build_auth_token() if context else None
            await client.inject_message(
                message=message,
                session_key=session_id,
                auth_token=auth_token,
            )
        except BotServiceError:
            raise
        except Exception as e:
            raise BotServiceError(f"Failed to inject message: {e}") from e

    async def abort_run(
        self,
        *,
        session_id: str,
        run_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> None:
        """中止正在进行的对话执行

        向 engine 下发 ``chat.abort``，中止 session_id 上正在进行的推理。
        通过连接池获取已握手的 AsyncChatClient 并调用其 chat_abort，
        不等待响应，仅发送中止指令后立即返回。

        Args:
            session_id: 会话 ID（与 send_message 的 session_id 一致）
            run_id: 运行 ID，透传给 engine 用于定位具体 run
            binding_info: Binding info for WS connection.
            context: 可选的请求上下文（abort 不依赖 auth_token，未使用）
        """
        sandbox_id = binding_info.sandbox_id
        engine_type = binding_info.engine_type
        if sandbox_id is None:
            raise BotServiceError("ClawBotService requires sandbox_id in binding_info.")

        url = self._build_ws_url(sandbox_id, engine_type or "openclaw")
        headers = self._get_headers(sandbox_id)

        client = await self._client_pool.get(sandbox_id, url, headers)
        try:
            await client.chat_abort(session_key=session_id, run_id=run_id)
        except BotServiceError:
            raise
        except Exception as e:
            raise BotServiceError(f"Failed to abort run: {e}") from e

    async def get_messages(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> list[MessageInfo]:
        """获取会话中的消息列表

        通过 AsyncSessionClient 从 adapter 侧查询会话消息。

        Args:
            session_id: 会话 ID
            binding_info: Binding info for HTTP connection.
            context: Optional request context (unused in ClawBotService).

        Returns:
            消息信息列表

        Raises:
            BotServiceError: 请求失败
        """
        sandbox_id = binding_info.sandbox_id
        if sandbox_id is None:
            raise BotServiceError("ClawBotService requires sandbox_id in binding_info.")

        # AsyncSessionClient 是 HTTP 无状态短连接，无需池化
        session_client = self._create_session_client(sandbox_id)
        try:
            async with session_client:
                messages = await session_client.get_messages(session_id)
                return [
                    MessageInfo(
                        id=msg.id,
                        session_id=msg.session_id,
                        role=msg.role,
                        content=msg.content,
                        meta=msg.meta,
                        created_at=msg.created_at,
                        history_meta=msg.history_meta,
                    )
                    for msg in messages
                ]
        except BotServiceError:
            raise
        except Exception as e:
            raise BotServiceError(f"Failed to get messages: {e}") from e

    async def get_session(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
    ) -> SessionInfo:
        """查询会话信息（只读）

        通过 AsyncSessionClient 从 adapter 侧查询会话详情，不创建新会话。

        Args:
            session_id: 会话 ID
            binding_info: Binding info for HTTP connection.
            context: Optional request context (unused in ClawBotService).

        Returns:
            SessionInfo: 包含 status、created_at、updated_at 等真实数据

        Raises:
            SessionNotFoundError: 会话不存在
            BotServiceError: 请求失败
        """
        sandbox_id = binding_info.sandbox_id
        if sandbox_id is None:
            raise BotServiceError("ClawBotService requires sandbox_id in binding_info.")

        session_client = self._create_session_client(sandbox_id)
        try:
            async with session_client:
                adapter_session = await session_client.get_session(session_id)
                return _map_adapter_session_info(adapter_session, binding_info.bot_id)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                raise SessionNotFoundError(session_id) from e
            raise BotServiceError(f"Failed to get session: {e}") from e
        except SessionNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            raise BotServiceError(f"Failed to get session: {e}") from e

    # ── 私有方法 ─────────────────────────────────────────────────────────────

    def _adapter_for(self, engine_type: str | None) -> BotEngineAdapter | None:
        """返回 engine_type 对应的已注册 adapter，未注册返回 None。

        只有 aicoding / hermes / claude_code 会命中；openclaw / teclaw 恒返回 None
        → 走原始分支（字节级不变）。
        """
        reg = self._engine_adapter_registry
        if reg is not None and engine_type and reg.has(engine_type):
            return reg.get(engine_type)
        return None

    def _build_base_url(self, sandbox_id: str) -> str:
        """Build base URL for session connection.

        Args:
            sandbox_id: The sandbox identifier.

        Returns:
            Base URL for session connection.
        """
        return (
            f"{self._config.proxy_base_url}/proxypass/"
            f"{self._get_path_target(sandbox_id)}"
        )

    def _build_ws_url(self, sandbox_id: str, engine_type: str = "openclaw") -> str:
        """Construct WebSocket URL.

        When a registered adapter exists for engine_type, uses adapter.ws_path()
        instead of the default f"/api/{engine_type}/ws".

        Args:
            sandbox_id: The sandbox identifier.
            engine_type: Engine type for WS path routing.

        Returns:
            Full WebSocket URL.
        """
        _adapter = self._adapter_for(engine_type)
        if _adapter is not None:
            ws_path = _adapter.ws_path()
        else:
            ws_path = f"/api/{engine_type}/ws"
        return (
            f"{self._config.proxy_ws_base_url}/proxypass/"
            f"{self._get_path_target(sandbox_id)}"
            f"{ws_path}"
        )

    def _create_session_client(self, sandbox_id: str) -> AsyncSessionClient:
        """Create an AsyncSessionClient for the given sandbox.

        Args:
            sandbox_id: The sandbox identifier.

        Returns:
            Configured AsyncSessionClient instance.
        """
        base_url = self._build_base_url(sandbox_id)
        headers = self._get_headers(sandbox_id)
        return AsyncSessionClient(
            base_url=base_url,
            headers=headers,
            timeout=self._config.request_timeout,
        )

    async def _get_or_create_adapter_session(
        self,
        session_client: AsyncSessionClient,
        session_id: str | None,
        user_id: str,
        metadata: dict[str, Any],
        run_id: str | None = None,
    ) -> tuple[str, bool]:
        """Get an existing adapter session or create a new one.

        Args:
            session_client: The AsyncSessionClient to use.
            session_id: Optional existing session ID to look up.
            user_id: The user ID for session creation.
            metadata: Session metadata.
            run_id: The run ID for session creation.

        Returns:
            Tuple of (adapter_session_id, is_reused).
            is_reused is True if an existing session was found.
        """
        if session_id:
            logger.info(
                f"Adapter session already exists: session_id={session_id}, "
                f"reusing existing session"
            )
            return session_id, True
        else:
            adapter_session = await session_client.create_session(
                title=metadata.get("title", None),
                user_id=user_id,
                model=metadata.get("model", None),
                uuid=run_id,
            )
            adapter_session_id = adapter_session.id
            if not adapter_session_id.startswith("agent:main:"):
                adapter_session_id = f"agent:main:{adapter_session_id}"

            logger.info(f"Adapter session created: session_id={adapter_session_id}")
            return adapter_session_id, False

    def _get_path_target(self, sandbox_id: str) -> str:
        """Get the target path for HTTP requests.

        Args:
            sandbox_id: The sandbox identifier.

        Returns:
            str: The target path for HTTP requests.
        """
        return f"ARCA_{sandbox_id}:{self._config.adapter_port}"

    def _get_headers(self, sandbox_id: str) -> dict[str, Any]:
        """Get headers for HTTP requests.

        Args:
            sandbox_id: The sandbox identifier.

        Returns:
            dict[str, Any]: The headers.
        """
        target = self._get_path_target(sandbox_id)
        token = self._secret_store.generate_proxy_token(target=target)
        return {"x-proxypass-token": token}


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO 8601 datetime string from adapter API response."""
    if not value:
        return None
    try:
        # Handle ISO 8601 with Z suffix (Python < 3.11 fromisoformat)
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def _map_adapter_session_info(
    adapter_session: AdapterSessionInfo,
    bot_id: str,
) -> SessionInfo:
    """Map AsyncSessionClient.SessionInfo (adapter layer) to api-level SessionInfo."""
    return SessionInfo(
        session_id=adapter_session.id,
        bot_id=bot_id,
        status="active",
        created_at=_parse_datetime(adapter_session.created_at) or datetime.now(),
        updated_at=_parse_datetime(adapter_session.updated_at),
    )
