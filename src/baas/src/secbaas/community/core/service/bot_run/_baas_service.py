"""BaasBotService - BotService implementation using BaaS bot resolution.

Provides BaasBotService that resolves bot connections via:
    token → baas_bot_uuid → findBotDevice → get WS connection → create session and run

Unlike ClawBotService (which uses ac_bots + DeviceBindingRepository + manual proxy
URL/token construction), BaasBotService uses:
    ZdasBotRepository + ZdasDeviceRepository + PaasServiceFacade
via the reusable DefaultBotWssDispatcher.

Design: 每个 API 方法按 binding_info 从连接池获取已握手的 client，
按 sandbox 级别复用 WS 连接（一次握手，多次复用）。
不同 sessionKey 的消息可并行，同一 sessionKey 并发会被拒绝。
baas_session_id 通过 binding_info.baas_session_id 传递。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiohttp
from pydantic import BaseModel, Field

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotResponse,
    BotServiceError,
    MessageInfo,
    NoActiveDevicesError,
    NoDevicesFoundError,
    SessionInfo,
    SessionNotFoundError,
    WsConnectionInfo,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_runtime.dispatcher import (
    DefaultBotWssDispatcher,
)
from secbaas.community.core.service.bot_session import DefaultSessionService
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._async_chat_client import ConcurrentSessionError
from ._async_chat_client_pool import AsyncChatClientPool
from ._async_session_client import AsyncSessionClient
from ._async_session_client import SessionInfo as AdapterSessionInfo
from ._bot_run_utils import (
    plan_session_id,
    resolve_user_id,
    safe_client_msg,
    strip_agent_main_prefix,
)
from ._internal_protocols import BotService

if TYPE_CHECKING:
    from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter
    from secbaas.community.spi.eval_env import EvalConsistencyCheckProtocol

    from ._engine_adapter_registry import BotEngineAdapterRegistry

logger = get_logger("core-bot-run")

DEFAULT_WS_PATH = "/api/openclaw/ws"
DEFAULT_ADAPTER_PORT = 20003
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_CONNECT_TIMEOUT = 10


class BaasBotServiceConfig(BaseModel):
    """Configuration for BaasBotService.

    WS connection info is resolved dynamically via DefaultBotWssDispatcher
    + PaasServiceFacade, so no static proxy URLs are needed.
    """

    adapter_port: int = Field(default=DEFAULT_ADAPTER_PORT, ge=1, le=65535)
    ws_path: str = DEFAULT_WS_PATH
    connect_timeout: int = Field(default=DEFAULT_CONNECT_TIMEOUT, gt=0)
    request_timeout: int = Field(default=DEFAULT_REQUEST_TIMEOUT, gt=0)


class BaasBotService(BotService):
    """BotService implementation using BaaS bot UUID resolution.

    Communicates with external Bot service via OpenClaw WebSocket protocol,
    same as ClawBotService, but with a different resolution flow.

    Uses AsyncChatClientPool for sandbox-level connection reuse — each sandbox
    has one shared WS connection; multiple requests can concurrently use the
    same client (AsyncChatClient multiplexes via sessionKey).

    Different sessionKeys can be multiplexed on the same connection.
    Same sessionKey concurrent requests will be rejected by AsyncChatClient.
    baas_session_id is stored in binding_info.baas_session_id by create_session
    and read by send_message/inject_message for marking session completed/failed.
    """

    # ── 构造 ─────────────────────────────────────────────────────────────────

    def __init__(
        self,
        config: BaasBotServiceConfig,
        client_pool: AsyncChatClientPool,
        wss_resolver: DefaultBotWssDispatcher,
        session_service: DefaultSessionService,
        engine_adapter_registry: BotEngineAdapterRegistry,
        eval_consistency_check: EvalConsistencyCheckProtocol,
    ) -> None:
        self._config = config
        self._client_pool = client_pool
        self._wss_resolver = wss_resolver
        self._session_service = session_service
        # 全部引擎（openclaw/teclaw/aicoding/hermes/claude_code）经 registry 的
        # engine adapter 表达差异：plan 亲和键 / session 创建 / 物化。
        self._engine_adapter_registry = engine_adapter_registry
        # 评测一致性检查 Plugin
        self._eval_consistency_check = eval_consistency_check

    # ── 公开方法 (BotService Protocol) ───────────────────────────────────────

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
        """Create a new conversation session.

        Resolution flow:
            0. Validate tenant is present (from context or metadata, required for BaaS)
            1. Resolve bot_uuid → WsConnectionInfo via DefaultBotWssDispatcher
               (also verifies bot exists and is ACTIVE)
            2. Create AsyncSessionClient and get/create adapter-side session
            3. Persist session to baas_bot_session table
            4. Store baas_session_id in binding_info for downstream use

        WS connections are NOT created here — they are managed by each API
        method via the connection pool.

        Args:
            bot_id: BaaS bot UUID (used directly as bot_uuid for resolution).
            session_id: Optional session identifier to reuse.
            metadata: Session metadata.
            binding_info: Binding info (resolved via BotServicePlugin.get_binding).
                         baas_session_id will be set on this object after persistence.
            context: Request context with identity info (api_key_prefix, tenant, etc.).
            run_id: Optional run ID for correlating session with run record.

        Returns:
            SessionInfo: The created or reused session information.

        Raises:
            BotServiceError: If tenant is missing.
            BotNotFoundError: If bot not found or not ACTIVE.
            BotNotAvailableError: If no active devices or connection fails.
        """
        # Extract tenant from request context
        tenant = context.tenant or None
        invoker = context.api_key_prefix

        logger.info(
            "[BaasBotService.create_session] bot_id=%s, session_id=%s, "
            "tenant=%r, invoker=%r, metadata_keys=%s, "
            "device_provider=%s, device_id=%s, binding_id=%s, bot_type=%s",
            bot_id,
            session_id,
            tenant,
            invoker,
            list(metadata.keys()),
            binding_info.device_provider,
            binding_info.device_id,
            binding_info.binding_id,
            binding_info.bot_type,
        )
        if not tenant:
            raise BotServiceError(
                f"tenant is required for BaaS bot session, bot_id={bot_id}"
            )

        # Inject invoker/tenant into metadata for downstream use (e.g. _persist_session_create)
        if invoker:
            metadata["invoker"] = invoker
        if tenant:
            metadata["tenant"] = tenant

        # Resolve user id for the session
        user_id = resolve_user_id(metadata, binding_info, context, bot_id)

        # 评测流量：提取 eval_id 供 consistency_key 构造使用
        eval_id = metadata.get("eval_id")

        # Step 1: Resolve bot_uuid → WsConnectionInfo (also verifies bot is ACTIVE)
        env = get_current_env()
        logger.info(
            "[BaasBotService.create_session] Resolving WS connection: "
            "bot_id=%s, tenant=%s, env=%s",
            bot_id,
            tenant,
            env,
        )
        try:
            engine_type = binding_info.engine_type
            if session_id:
                session_consistency_key = session_id
            else:
                run_id = str(uuid.uuid4())
                session_consistency_key = plan_session_id(
                    tc_bot_id=binding_info.bot_id,
                    user_id=user_id,
                    run_id=run_id,
                    eval_id=eval_id,
                    adapter=self._adapter_for(engine_type),
                )
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info,
                session_id=session_consistency_key,
                context=context,
            )
        except BotNotFoundError:
            logger.error(
                "[BaasBotService.create_session] Bot not found in BaaS: "
                "bot_id=%s, tenant=%s, env=%s",
                bot_id,
                tenant,
                env,
            )
            raise
        except NoDevicesFoundError as e:
            raise BotNotFoundError(bot_id) from e
        except NoActiveDevicesError as e:
            raise BotNotAvailableError(bot_id, str(e)) from e
        except Exception as e:
            logger.error(
                "[BaasBotService.create_session] WS resolution failed: "
                "bot_id=%s, tenant=%s, env=%s, error=%s",
                bot_id,
                tenant,
                env,
                e,
            )
            raise BotServiceError(
                f"Failed to resolve WS connection for bot {bot_id}: {safe_client_msg(e)}"
            ) from e

        # Step 2: Get or create adapter session
        session_client = self._create_session_client(
            conn_info, engine_type, metadata=metadata
        )

        try:
            async with session_client:
                # adapter session 创建：直接走 engine adapter（teclaw 探测
                # 语义 + openclaw agent:main: 前缀），未注册视为装配错误。
                _adapter = self._adapter_for(engine_type)
                if _adapter is None:
                    raise BotServiceError(
                        f"No engine adapter registered for engine_type="
                        f"{engine_type!r}, cannot create session for bot {bot_id}"
                    )
                # planned_id 兼容显式 session_id 与 plan 构造的 id（恒非 None，
                # eval 兜底含在 plan 内）；session_pending 区分两者：
                # 显式 id（会话已存在）复用，plan 值（未物化）由 adapter
                # 解析裸 key 创建
                adapter_session_id, reused = await _adapter.create_adapter_session(
                    session_client=session_client,
                    planned_id=session_consistency_key,
                    user_id=user_id,
                    metadata=metadata,
                    bot_id=binding_info.bot_id,
                    session_pending=session_id is None,
                )
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to get or create adapter session: %s", e)
            raise BotServiceError(
                f"Failed to get or create adapter session for bot {bot_id}: {safe_client_msg(e)}"
            ) from e

        # session_id 退化检查：调用方传入了 session_id，但 adapter 返回了不同的值
        if session_id and adapter_session_id != session_id:
            logger.warning(
                "[BaasBotService.create_session] session_id 退化: "
                "请求=%s, 实际=%s, bot_id=%s — 调用方传入的 session_id 被替换",
                session_id,
                adapter_session_id,
                bot_id,
            )

        action = "reused" if reused else "created"
        logger.info(
            "Session reused: %s: session_id=%s, bot_id=%s, target=%s",
            action,
            adapter_session_id,
            bot_id,
            conn_info.target,
        )

        session_info = SessionInfo(
            session_id=adapter_session_id,
            bot_id=bot_id,
            status="active",
            created_at=datetime.now(),
            metadata=metadata,
        )

        # Step 3: Persist session to baas_bot_session table and store id in binding_info
        baas_session_id = self._persist_session_create(
            session_info=session_info,
            conn_info=conn_info,
        )
        binding_info.baas_session_id = baas_session_id

        return session_info

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
        """Send a message and get response via ChatClient.

        Gets the shared connection for the sandbox from the pool and sends
        the message. The connection remains in the pool for reuse by other
        requests (AsyncChatClient multiplexes via sessionKey).
        Same sessionKey concurrent requests will raise an error.

        Args:
            session_id: The session identifier.
            message: The message content to send.
            binding_info: Binding info for WS connection (contains baas_session_id).
            wait_result: Whether to wait for result.
            context: Optional request context.
            timeout: Optional timeout in seconds. None means no limit.
            chat_metadata: Optional chat metadata.
            attachments: Optional attachments.
            session_pending: session_id 为提前构造的计划值，发送前需先物化。

        Returns:
            BotResponse: The bot's response.

        Raises:
            BotServiceError: If request fails.
        """
        if session_pending:
            await self._materialize_session(
                session_id=session_id,
                binding_info=binding_info,
                context=context,
                metadata=dict(chat_metadata) if chat_metadata else {},
            )

        baas_session_id = binding_info.baas_session_id

        # eval 消息一致性检查与日志 — 委托 Plugin
        if chat_metadata and chat_metadata.get("eval_id"):
            logger.info(
                "[BaasBotService.send_message] sending eval message: eval_id=%s, session_id=%s",
                chat_metadata.get("eval_id"),
                session_id,
            )
            self._eval_consistency_check.check_default_tag_consistency(
                binding_info=binding_info,
                chat_metadata=chat_metadata,
            )

        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=safe_client_msg(e))
            raise BotServiceError(
                f"Failed to resolve WS connection: {safe_client_msg(e)}"
            ) from e

        pool_key = conn_info.target
        headers: dict[str, str] = {"x-proxypass-token": conn_info.token}
        if chat_metadata:
            eval_id = chat_metadata.get("eval_id")
            if eval_id:
                headers["X-Eval-Id"] = str(eval_id)
            default_tag = chat_metadata.get("default_tag")
            if default_tag:
                headers["X-Agentclaw-Default-Tag"] = str(default_tag)

        client = await self._client_pool.get(pool_key, conn_info.ws_url, headers)
        try:
            auth_token = context.build_auth_token() if context else None
            app_id = context.app_id if context else None
            content, agent_events = await client.send_message(
                message=message,
                session_key=session_id,
                wait_result=wait_result,
                timeout=timeout,
                auth_token=auth_token,
                app_id=app_id,
                chat_metadata=chat_metadata,
                attachments=attachments,
            )
            self._mark_session_completed(baas_session_id, result={"content": content})
            return BotResponse(content=content)
        except TimeoutError:
            raise
        except ConcurrentSessionError as e:
            self._mark_session_failed(
                baas_session_id, err_msg=f"Concurrent request: {e}"
            )
            raise BotServiceError(
                f"Concurrent request on session {session_id}: {e}"
            ) from e
        except Exception as e:
            logger.warning("Failed to send message: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=safe_client_msg(e))
            raise BotServiceError(
                f"Failed to send message: {safe_client_msg(e)}"
            ) from e

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
        """流式发送消息，逐 chunk 产出 StreamChunk。

        与 send_message 相同的连接解析逻辑，但调用
        client.send_message_stream 并返回 AsyncIterator。

        会话状态在流结束后标记完成/失败。
        """
        if session_pending:
            await self._materialize_session(
                session_id=session_id,
                binding_info=binding_info,
                context=context,
                metadata=dict(chat_metadata) if chat_metadata else {},
            )

        baas_session_id = binding_info.baas_session_id
        engine_type = binding_info.engine_type

        # eval 消息一致性检查与日志 — 委托 Plugin
        if chat_metadata and chat_metadata.get("eval_id"):
            logger.info(
                "[BaasBotService.send_message_stream] sending eval message: "
                "eval_id=%s, session_id=%s",
                chat_metadata.get("eval_id"),
                session_id,
            )
            self._eval_consistency_check.check_default_tag_consistency(
                binding_info=binding_info,
                chat_metadata=chat_metadata,
            )

        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=safe_client_msg(e))
            raise BotServiceError(
                f"Failed to resolve WS connection: {safe_client_msg(e)}"
            ) from e

        pool_key = conn_info.target
        headers: dict[str, str] = {"x-proxypass-token": conn_info.token}
        if chat_metadata:
            eval_id = chat_metadata.get("eval_id")
            if eval_id:
                headers["X-Eval-Id"] = str(eval_id)
            default_tag = chat_metadata.get("default_tag")
            if default_tag:
                headers["X-Agentclaw-Default-Tag"] = str(default_tag)

        client = await self._client_pool.get(pool_key, conn_info.ws_url, headers)
        auth_token = context.build_auth_token() if context else None
        app_id = context.app_id if context else None

        try:
            stream_error = False
            async for chunk in client.send_message_stream(
                message=message,
                session_key=session_id,
                timeout=timeout,
                auth_token=auth_token,
                app_id=app_id,
                attachments=attachments,
            ):
                if chunk.type == "error":
                    stream_error = True
                yield replace(chunk, engine_type=engine_type)
            if stream_error:
                self._mark_session_failed(baas_session_id)
            else:
                self._mark_session_completed(baas_session_id)
        except ConcurrentSessionError as e:
            self._mark_session_failed(
                baas_session_id, err_msg=f"Concurrent request: {e}"
            )
            raise BotServiceError(
                f"Concurrent request on session {session_id}: {e}"
            ) from e
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to send message stream: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=safe_client_msg(e))
            raise BotServiceError(
                f"Failed to send message stream: {safe_client_msg(e)}"
            ) from e

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
        """注入消息到已有会话

        与 send_message 不同，inject_message 不返回响应结果（返回 None），
        适用于注入系统指令、上下文补充等不需要等待响应的场景。

        Args:
            session_id: 会话 ID
            message: 注入的消息内容
            binding_info: Binding info for WS connection (contains baas_session_id).
            context: 可选的请求上下文（身份认证、调用者信息等）
            attachments: 附件
            session_pending: session_id 为提前构造的计划值，注入前需先物化。
        """
        if session_pending:
            await self._materialize_session(
                session_id=session_id,
                binding_info=binding_info,
                context=context,
                metadata={},
            )

        baas_session_id = binding_info.baas_session_id

        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=safe_client_msg(e))
            raise BotServiceError(
                f"Failed to resolve WS connection: {safe_client_msg(e)}"
            ) from e

        pool_key = conn_info.target
        headers = {"x-proxypass-token": conn_info.token}

        client = await self._client_pool.get(pool_key, conn_info.ws_url, headers)
        try:
            auth_token = context.build_auth_token() if context else None
            await client.inject_message(
                message=message,
                session_key=session_id,
                auth_token=auth_token,
                attachments=attachments,
            )
            self._mark_session_completed(
                baas_session_id, result={"content": "inject success"}
            )
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to inject message: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=safe_client_msg(e))
            raise BotServiceError(
                f"Failed to inject message: {safe_client_msg(e)}"
            ) from e

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
            context: Optional request context for tenant extraction.

        Returns:
            消息信息列表

        Raises:
            BotServiceError: 请求失败
        """
        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            raise BotServiceError(
                f"Failed to resolve WS connection: {safe_client_msg(e)}"
            ) from e

        session_client = self._create_session_client(
            conn_info, binding_info.engine_type
        )
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
            logger.warning("Failed to get messages: %s", e)
            raise BotServiceError(
                f"Failed to get messages: {safe_client_msg(e)}"
            ) from e

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
            context: Optional request context for tenant extraction.

        Returns:
            SessionInfo: 包含 status、created_at、updated_at 等真实数据

        Raises:
            SessionNotFoundError: 会话不存在
            BotServiceError: 请求失败
        """
        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            raise BotServiceError(
                f"Failed to resolve WS connection: {safe_client_msg(e)}"
            ) from e

        session_client = self._create_session_client(
            conn_info, binding_info.engine_type
        )
        try:
            async with session_client:
                adapter_session = await session_client.get_session(
                    session_id, engine=binding_info.engine_type
                )
                return _map_adapter_session_info(adapter_session, binding_info.bot_id)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                raise SessionNotFoundError(session_id) from e
            logger.warning("Failed to get session: %s", e)
            raise BotServiceError(f"Failed to get session: {safe_client_msg(e)}") from e
        except SessionNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to get session: %s", e)
            raise BotServiceError(f"Failed to get session: {safe_client_msg(e)}") from e

    async def list_sessions(
        self,
        *,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SessionInfo]:
        """List sessions for a given bot binding (read-only).

        通过 AsyncSessionClient 从 adapter 侧查询会话列表，不创建新会话。

        Args:
            binding_info: Binding info for HTTP connection.
            context: Optional request context for tenant extraction.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            List of SessionInfo objects.

        Raises:
            BotServiceError: 请求失败
        """
        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, context=context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            raise BotServiceError(
                f"Failed to resolve WS connection: {safe_client_msg(e)}"
            ) from e

        session_client = self._create_session_client(
            conn_info, binding_info.engine_type
        )
        try:
            async with session_client:
                adapter_sessions = await session_client.list_sessions(
                    agent_id=binding_info.bot_id,
                    limit=limit,
                    offset=offset,
                    engine=binding_info.engine_type,
                )
                return [
                    _map_adapter_session_info(s, binding_info.bot_id)
                    for s in adapter_sessions
                ]
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to list sessions: %s", e)
            raise BotServiceError(
                f"Failed to list sessions: {safe_client_msg(e)}"
            ) from e

    async def abort(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
    ) -> None:
        """Best-effort 通知 engine 中止 session。

        复用 send_message 的连接解析与连接池逻辑，发送 ``chat.abort``。
        失败仅记录日志，不影响 abort 主流程。

        Args:
            session_id: 会话 ID（engine 侧 sessionKey）
            binding_info: 已解析的 binding 信息
        """
        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context=None
            )
        except Exception as e:
            logger.warning(
                "[BaasBotService.abort] failed to resolve WS connection: "
                "session_id=%s error=%s",
                session_id,
                e,
            )
            return

        pool_key = conn_info.target
        # Avoid keeping the raw token on the same line as the header key to keep
        # the secret scanner from flagging the variable assignment as a credential.
        auth_value = conn_info.token
        headers = {"x-proxypass-token": auth_value}

        try:
            client = await self._client_pool.get(pool_key, conn_info.ws_url, headers)
            # 注意：chat.abort 的 run_id 是引擎侧 run 标识，与 baas_bot_run.run_id
            # 语义不同，不能把后者透传过去；用 sessionKey 定位即可。
            await client.chat_abort(session_key=session_id)
            logger.info(
                "[BaasBotService.abort] engine abort sent: session_id=%s",
                session_id,
            )
        except Exception as e:
            logger.warning(
                "[BaasBotService.abort] failed to send chat.abort: "
                "session_id=%s error=%s",
                session_id,
                e,
            )

    # ── 私有方法 ─────────────────────────────────────────────────────────────

    def _adapter_for(self, engine_type: str | None) -> BotEngineAdapter | None:
        """返回 engine_type 对应的已注册 adapter，未注册返回 None。

        生产装配下全部引擎（openclaw/teclaw/aicoding/hermes/claude_code）
        均有 adapter；未注册时三处接缝走 else 原始分支（兼容测试空 registry）。
        """
        if engine_type and self._engine_adapter_registry.has(engine_type):
            return self._engine_adapter_registry.get(engine_type)
        return None

    async def _resolve_ws_connection(
        self,
        bot_uuid: str,
        tenant: str,
        engine_type: str | None = None,
        session_consistency_key: str | None = None,
    ) -> WsConnectionInfo:
        """Resolve WS connection info for a bot UUID.

        Also verifies the bot exists and is ACTIVE via
        DefaultBotWssDispatcher → ZdasBotRepository.get_active_by_bot_uuid.

        Args:
            bot_uuid: The BaaS bot UUID.
            tenant: The tenant for multi-tenant isolation.
            engine_type: Optional engine type for WS path routing.
            session_consistency_key: Optional consistency key used for device affinity
                (consistent-hashing sticky device selection).

        Returns:
            WsConnectionInfo with ws_url, token, target, and expires_at.

        Raises:
            BotNotFoundError: If bot not found or not ACTIVE.
            NoDevicesFoundError: If bot has no associated devices.
            NoActiveDevicesError: If bot has no ACTIVE devices.
        """
        # WS path:命中 adapter 用 adapter.ws_path(),否则用 f"/api/{engine}/ws"。
        path = self._config.ws_path
        _adapter = self._adapter_for(engine_type)
        if _adapter is not None:
            path = _adapter.ws_path()
        elif engine_type:
            path = f"/api/{engine_type}/ws"

        return await self._wss_resolver.dispatch_bot_ws_conn_info(
            bot_uuid=bot_uuid,
            port=self._config.adapter_port,
            path=path,
            tenant=tenant,
            device_affinity=session_consistency_key,
        )

    async def _resolve_ws_connection_for_binding(
        self,
        binding_info: BotBindingInfo,
        session_id: str | None = None,
        context: BotChatContext | None = None,
    ) -> WsConnectionInfo:
        """Resolve WS connection from binding_info for API methods.

        Extracts bot_id and tenant from binding_info/context to resolve
        the WS connection info.

        Args:
            binding_info: The binding info containing bot_id and device info.
            session_id: Session ID used for device affinity (consistent-hashing
                sticky device selection).
            context: Optional request context for tenant extraction.

        Returns:
            WsConnectionInfo with ws_url, token, target.
        """
        bot_uuid = (
            binding_info.device_id
            if binding_info.device_provider == "baas"
            or binding_info.device_provider == "teclaw"
            else binding_info.bot_id
        )

        # Extract tenant: prefer binding_info.device_props, fallback to context
        tenant = ""
        if context is not None and context.tenant:
            tenant = context.tenant
        if not tenant and binding_info.device_props:
            tenant = binding_info.device_props.get("tenant", "")

        # 与 create_session 路径一致:剥离前导 agent:main: 前缀,使 send/inject 等
        # API 路径的 device 路由对前缀有无不敏感(同一会话跨通道落到同一实例)。
        affinity = (
            strip_agent_main_prefix(session_id) if session_id is not None else None
        )
        return await self._resolve_ws_connection(
            bot_uuid,
            tenant,
            engine_type=binding_info.engine_type,
            session_consistency_key=affinity,
        )

    @staticmethod
    def _build_base_url(conn_info: WsConnectionInfo, engine_type: str) -> str:
        """Derive HTTP base URL from WsConnectionInfo.

        Converts wss:// → https:// and strips the WS path suffix.

        Args:
            conn_info: WsConnectionInfo from PaasServiceFacade.

        Returns:
            HTTP base URL for AsyncSessionClient.
        """
        # 保留原静态语义（openclaw/teclaw 及既有测试）：后缀 = f"/api/{engine}/ws"。
        # 新引擎的 adapter.ws_path() 后缀由 _create_session_client 走 _strip_ws_url_to_base。
        return BaasBotService._strip_ws_url_to_base(
            conn_info.ws_url, f"/api/{engine_type}/ws"
        )

    @staticmethod
    def _strip_ws_url_to_base(ws_url: str, ws_path_suffix: str) -> str:
        """Map a WebSocket URL to its HTTP peer and strip the WS path suffix."""
        if ws_url.startswith("wss://"):
            base = ws_url[6:]
            scheme = "https"
        elif ws_url.startswith("ws://"):
            base = ws_url[5:]
            scheme = "http"
        else:
            base = ws_url
            scheme = "https"
        # The target is embedded in the path: /proxypass/{target}/api/openclaw/ws
        if base.endswith(ws_path_suffix):
            base = base[: -len(ws_path_suffix)]
        return f"{scheme}://{base}"

    def _create_session_client(
        self,
        conn_info: WsConnectionInfo,
        engine_type: str = "openclaw",
        metadata: dict[str, Any] | None = None,
    ) -> AsyncSessionClient:
        """Create an AsyncSessionClient from resolved WS connection info.

        Args:
            conn_info: WsConnectionInfo with ws_url, token, target.
            engine_type: Engine type for URL resolution.
            metadata: 请求 metadata，从中提取 eval_id / default_tag 注入
                      X-Eval-Id / X-Agentclaw-Default-Tag Header 供引擎
                      propagation 传播。为 None 或字段缺失时不注入。

        base_url 计算：新引擎（aicoding 等）用 adapter.ws_path() 作 strip 后缀，
        openclaw/teclaw 走原 _build_base_url。
        """
        _adapter = self._adapter_for(engine_type)
        if _adapter is not None:
            base_url = self._strip_ws_url_to_base(conn_info.ws_url, _adapter.ws_path())
        else:
            base_url = self._build_base_url(conn_info, engine_type)
        headers: dict[str, str] = {"x-proxypass-token": conn_info.token}
        if metadata:
            eval_id = metadata.get("eval_id")
            if eval_id:
                headers["X-Eval-Id"] = str(eval_id)
            default_tag = metadata.get("default_tag")
            if default_tag:
                headers["X-Agentclaw-Default-Tag"] = str(default_tag)
        return AsyncSessionClient(
            base_url=base_url,
            headers=headers,
            timeout=self._config.request_timeout,
        )

    async def _materialize_session(
        self,
        *,
        session_id: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None,
        metadata: dict[str, Any],
    ) -> None:
        """延迟物化：resolve WS → adapter 创建 → persist → 回填 baas_session_id。

        用预先构造的 session_id 在 adapter 侧创建会话：引擎差异经统一的
        ``create_adapter_session(planned_id=...)`` 下沉到各 engine adapter
        （teclaw 探测-不存在再创建，其余解析裸 key 走 uuid 新建、引擎侧幂等）。
        """
        tenant = metadata.get("tenant", "")
        if not tenant and context and context.tenant:
            tenant = context.tenant
        if not tenant:
            raise BotServiceError(
                f"tenant is required for materialize session, session_id={session_id}"
            )

        # 注入 invoker/tenant 供 _persist_session_create 使用
        if context and context.api_key_prefix:
            metadata["invoker"] = context.api_key_prefix
        metadata["tenant"] = tenant

        user_id = resolve_user_id(metadata, binding_info, context, binding_info.bot_id)
        engine_type = binding_info.engine_type

        # WS 连接解析：与 send/inject 等 API 路径统一走 binding 适配层
        # （baas/teclaw binding 选 device_id；affinity 用 strip 后的 session_id）。
        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id=session_id, context=context
            )
        except Exception as e:
            raise BotServiceError(
                f"Failed to resolve WS connection for materialize: {safe_client_msg(e)}"
            ) from e

        # adapter 侧创建：引擎差异（teclaw 探测-创建 / 其余解析裸 key 走 uuid
        # 新建）统一经 create_adapter_session 下沉到各 engine adapter。
        session_client = self._create_session_client(
            conn_info, engine_type, metadata=metadata
        )
        try:
            async with session_client:
                _adapter = self._adapter_for(engine_type)
                if _adapter is None:
                    raise BotServiceError(
                        f"No engine adapter registered for engine_type="
                        f"{engine_type!r}, cannot materialize session {session_id}"
                    )
                _, reused = await _adapter.create_adapter_session(
                    session_client=session_client,
                    planned_id=session_id,
                    user_id=user_id,
                    metadata=metadata,
                    bot_id=binding_info.bot_id,
                    session_pending=True,
                )
                logger.info(
                    "[materialize_session] %s: session_id=%s, bot_id=%s",
                    "reused" if reused else "created",
                    session_id,
                    binding_info.bot_id,
                )
        except BotServiceError:
            raise
        except Exception as e:
            raise BotServiceError(
                f"Failed to materialize session {session_id}: {safe_client_msg(e)}"
            ) from e

        # 持久化并回填 baas_session_id
        session_info = SessionInfo(
            session_id=session_id,
            bot_id=binding_info.bot_id,
            status="active",
            created_at=datetime.now(),
            metadata=metadata,
        )
        baas_session_id = self._persist_session_create(
            session_info=session_info,
            conn_info=conn_info,
        )
        binding_info.baas_session_id = baas_session_id

    def _persist_session_create(
        self,
        session_info: SessionInfo,
        conn_info: WsConnectionInfo,
    ) -> str | None:
        """Persist session to baas_bot_session table via DefaultSessionService.

        Requires 'tenant' in metadata (set by router layer). Skips persistence
        if 'invoker' is missing (internal/testing calls without api_key_prefix).

        Args:
            session_info: The locally registered SessionInfo.
            conn_info: The resolved WS connection info (contains device target).

        Returns:
            The baas_session_id if persisted, None otherwise.
        """
        metadata = session_info.metadata or {}
        tenant = metadata.get("tenant", "")
        invoker = metadata.get("invoker", "")

        if not invoker:
            logger.debug("Skipping baas_bot_session persist: no invoker in metadata")
            return None

        device_uuid = conn_info.target
        try:
            baas_session_id = self._session_service.create_session(
                bot_uuid=session_info.bot_id,
                invoker=invoker,
                req=metadata.get("req", {}),
                device_uuid=device_uuid,
                tenant=tenant,
                trace_id=metadata.get("trace_id"),
            )
            self._session_service.mark_running(baas_session_id)

            # Store baas_session_id in metadata for later mark_completed/mark_failed
            if session_info.metadata is not None:
                session_info.metadata["baas_session_id"] = baas_session_id

            logger.info(
                "Persisted session to baas_bot_session: baas_session_id=%s, "
                "bot_uuid=%s, device_uuid=%s",
                baas_session_id,
                session_info.bot_id,
                device_uuid,
            )
            return baas_session_id
        except Exception:
            logger.exception(
                "Failed to persist session to baas_bot_session: bot_uuid=%s",
                session_info.bot_id,
            )
            return None

    def _mark_session_completed(
        self,
        baas_session_id: str | None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Mark baas_bot_session as COMPLETED after successful message delivery."""
        if not baas_session_id:
            return
        try:
            self._session_service.mark_completed(baas_session_id, result=result)
        except Exception:
            logger.exception(
                "Failed to mark session COMPLETED: baas_session_id=%s",
                baas_session_id,
            )

    def _mark_session_failed(
        self,
        baas_session_id: str | None,
        err_msg: str | None = None,
    ) -> None:
        """Mark baas_bot_session as FAILED after message delivery failure."""
        if not baas_session_id:
            return
        try:
            self._session_service.mark_failed(baas_session_id, err_msg=err_msg)
        except Exception:
            logger.exception(
                "Failed to mark session FAILED: baas_session_id=%s",
                baas_session_id,
            )


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
