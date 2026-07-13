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
from ._bot_run_utils import resolve_user_id
from ._internal_protocols import BotService

if TYPE_CHECKING:
    from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

    from ._engine_adapter_registry import BotEngineAdapterRegistry

logger = get_logger("core-bot-run")

DEFAULT_WS_PATH = "/api/openclaw/ws"
DEFAULT_ADAPTER_PORT = 20003
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_CONNECT_TIMEOUT = 10


def _safe_client_msg(exc: Exception) -> str:
    """返回可安全外抛给客户端的异常消息(剥离 aiohttp 请求 url 等内部信息)。

    aiohttp.ClientResponseError 的 str() 形如 ``"500, message='...', url='https://agentclawproxy-.../api/sessions'"``,
    其中 url 是内部代理地址,不应外泄。这里只取业务 message。
    其他 aiohttp.ClientError 子类(如 ClientConnectorError / InvalidURL)的 str()
    同样可能包含内部 hostname/URL,统一返回通用消息;完整异常由调用方记入日志。
    """
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.message or f"HTTP {exc.status}"
    if isinstance(exc, aiohttp.ClientError):
        return "Connection failed"
    return str(exc)


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
        engine_adapter_registry: BotEngineAdapterRegistry | None = None,
    ) -> None:
        self._config = config
        self._client_pool = client_pool
        self._wss_resolver = wss_resolver
        self._session_service = session_service
        # Registry 只服务 aicoding / hermes / claude_code；openclaw / teclaw 不注册。
        # 引擎差异由 registry 分流:命中 adapter 走 adapter,否则走原始分支。
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
            binding_info: Binding info (resolved by BotBindingResolver).
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
        # Extract tenant from context.extra, fallback to metadata for backward compat
        tenant = None
        invoker = None
        if context is not None:
            invoker = context.api_key_prefix
            if context.tenant:
                tenant = context.tenant

        metadata = metadata or {}
        if not tenant:
            tenant = metadata.get("tenant")

        logger.info(
            "[BaasBotService.create_session] bot_id=%s, session_id=%s, "
            "has_binding_info=%s, tenant=%s, invoker=%s, service_type=%s",
            bot_id,
            session_id,
            binding_info is not None,
            tenant,
            invoker,
            "BaasBotService",
        )
        if binding_info:
            logger.info(
                "[BaasBotService.create_session] binding_info: "
                "device_provider=%s, device_id=%s, binding_id=%s, bot_type=%s",
                binding_info.device_provider,
                binding_info.device_id,
                binding_info.binding_id,
                binding_info.bot_type,
            )

        logger.info(
            "[BaasBotService.create_session] Extracted tenant: "
            "tenant=%r, invoker=%r, metadata_keys=%s",
            tenant,
            invoker,
            list(metadata.keys()) if metadata else [],
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
            engine_type = binding_info.engine_type if binding_info else None
            # consistency key:命中 adapter 走 adapter,否则走原始分支。
            _adapter = self._adapter_for(engine_type)
            if _adapter is not None:
                session_consistency_key = _adapter.session_consistency_key(
                    tc_bot_id=binding_info.bot_id,
                    user_id=user_id,
                    run_id=run_id,
                    session_id=session_id,
                )
            else:
                session_consistency_key = self._create_session_consistency_key(
                    engine_type=engine_type,
                    tc_bot_id=binding_info.bot_id,
                    user_id=user_id,
                    run_id=run_id,
                    session_id=session_id,
                )
            conn_info = await self._resolve_ws_connection(
                bot_id, tenant, engine_type, session_consistency_key
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
                f"Failed to resolve WS connection for bot {bot_id}: {_safe_client_msg(e)}"
            ) from e

        # Step 2: Get or create adapter session
        session_client = self._create_session_client(conn_info, engine_type)

        try:
            async with session_client:
                # adapter session 创建:命中 adapter 走 adapter,否则走原始分支
                # (含 teclaw 语义 + openclaw agent:main: 前缀)。
                _adapter = self._adapter_for(engine_type)
                if _adapter is not None:
                    adapter_session_id, reused = await _adapter.create_adapter_session(
                        session_client=session_client,
                        session_id=session_id,
                        user_id=user_id,
                        metadata=metadata,
                        bot_id=binding_info.bot_id,
                        run_id=run_id,
                    )
                else:
                    (
                        adapter_session_id,
                        reused,
                    ) = await self._get_or_create_adapter_session(
                        session_client=session_client,
                        session_id=session_id,
                        user_id=user_id,
                        metadata=metadata,
                        engine_type=engine_type or "openclaw",
                        bot_id=binding_info.bot_id,
                        run_id=run_id,
                    )
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to get or create adapter session: %s", e)
            raise BotServiceError(
                f"Failed to get or create adapter session for bot {bot_id}: {_safe_client_msg(e)}"
            ) from e

        action = "reused" if reused else "created"
        logger.info(
            "Session %s: session_id=%s, bot_id=%s, target=%s",
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
        if binding_info is not None:
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
        timeout: int | None = None,
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
            binding_info: Binding info for WS connection (contains baas_session_id).
            wait_result: Whether to wait for result.
            context: Optional request context.
            timeout: Optional timeout in seconds. None means no limit.

        Returns:
            BotResponse: The bot's response.

        Raises:
            BotServiceError: If request fails.
        """
        baas_session_id = binding_info.baas_session_id

        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=_safe_client_msg(e))
            raise BotServiceError(
                f"Failed to resolve WS connection: {_safe_client_msg(e)}"
            ) from e

        pool_key = conn_info.target
        headers = {"x-proxypass-token": conn_info.token}

        client = await self._client_pool.get(pool_key, conn_info.ws_url, headers)
        try:
            auth_token = context.build_auth_token() if context else None
            app_id = context.app_id if context else None
            content, state = await client.send_message(
                message=message,
                session_key=session_id,
                wait_result=wait_result,
                timeout=timeout
                if timeout is not None
                else self._config.request_timeout,
                auth_token=auth_token,
                app_id=app_id,
                chat_metadata=chat_metadata,
            )

            if state == "error":  # type: ignore[comparison-overlap]
                self._mark_session_failed(baas_session_id, err_msg=content)
                raise BotServiceError(content)

            self._mark_session_completed(baas_session_id, result={"content": content})
            return BotResponse(content=content)

        except BotServiceError:
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
            self._mark_session_failed(baas_session_id, err_msg=_safe_client_msg(e))
            raise BotServiceError(
                f"Failed to send message: {_safe_client_msg(e)}"
            ) from e

    async def send_message_stream(
        self,
        *,
        session_id: str,
        message: str,
        binding_info: BotBindingInfo,
        context: BotChatContext | None = None,
        timeout: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式发送消息，逐 chunk 产出 StreamChunk。

        与 send_message 相同的连接解析逻辑，但调用
        client.send_message_stream 并返回 AsyncIterator。

        会话状态在流结束后标记完成/失败。
        """
        baas_session_id = binding_info.baas_session_id
        engine_type = binding_info.engine_type

        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=_safe_client_msg(e))
            raise BotServiceError(
                f"Failed to resolve WS connection: {_safe_client_msg(e)}"
            ) from e

        pool_key = conn_info.target
        headers = {"x-proxypass-token": conn_info.token}

        client = await self._client_pool.get(pool_key, conn_info.ws_url, headers)
        auth_token = context.build_auth_token() if context else None
        app_id = context.app_id if context else None

        try:
            async for chunk in client.send_message_stream(
                message=message,
                session_key=session_id,
                timeout=timeout
                if timeout is not None
                else self._config.request_timeout,
                auth_token=auth_token,
                app_id=app_id,
            ):
                yield replace(chunk, engine_type=engine_type)
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
            self._mark_session_failed(baas_session_id, err_msg=_safe_client_msg(e))
            raise BotServiceError(
                f"Failed to send message stream: {_safe_client_msg(e)}"
            ) from e

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
            binding_info: Binding info for WS connection (contains baas_session_id).
            context: 可选的请求上下文（身份认证、调用者信息等）
        """
        baas_session_id = binding_info.baas_session_id

        try:
            conn_info = await self._resolve_ws_connection_for_binding(
                binding_info, session_id, context
            )
        except Exception as e:
            logger.warning("Failed to resolve WS connection: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=_safe_client_msg(e))
            raise BotServiceError(
                f"Failed to resolve WS connection: {_safe_client_msg(e)}"
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
            )
            self._mark_session_completed(
                baas_session_id, result={"content": "inject success"}
            )
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to inject message: %s", e)
            self._mark_session_failed(baas_session_id, err_msg=_safe_client_msg(e))
            raise BotServiceError(
                f"Failed to inject message: {_safe_client_msg(e)}"
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
                f"Failed to resolve WS connection: {_safe_client_msg(e)}"
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
                f"Failed to get messages: {_safe_client_msg(e)}"
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
                f"Failed to resolve WS connection: {_safe_client_msg(e)}"
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
            raise BotServiceError(
                f"Failed to get session: {_safe_client_msg(e)}"
            ) from e
        except SessionNotFoundError:
            raise
        except BotServiceError:
            raise
        except Exception as e:
            logger.warning("Failed to get session: %s", e)
            raise BotServiceError(
                f"Failed to get session: {_safe_client_msg(e)}"
            ) from e

    # ── 私有方法 ─────────────────────────────────────────────────────────────

    def _adapter_for(self, engine_type: str | None) -> BotEngineAdapter | None:
        """返回 engine_type 对应的已注册 adapter，未注册返回 None。

        只有 aicoding / hermes / claude_code 会命中；openclaw / teclaw 恒返回 None
        → 三处接缝走 else 原始分支（字节级不变）。
        """
        reg = self._engine_adapter_registry
        if reg is not None and engine_type and reg.has(engine_type):
            return reg.get(engine_type)
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

        return await self._resolve_ws_connection(
            bot_uuid,
            tenant,
            engine_type=binding_info.engine_type,
            session_consistency_key=session_id,
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
        """wss:// → https:// 并 strip 指定 WS path 后缀。"""
        if ws_url.startswith("wss://"):
            base = ws_url[6:]
        elif ws_url.startswith("ws://"):
            base = ws_url[5:]
        else:
            base = ws_url
        # The target is embedded in the path: /proxypass/{target}/api/openclaw/ws
        if base.endswith(ws_path_suffix):
            base = base[: -len(ws_path_suffix)]
        return f"https://{base}"

    def _create_session_client(
        self, conn_info: WsConnectionInfo, engine_type: str = "openclaw"
    ) -> AsyncSessionClient:
        """Create an AsyncSessionClient from resolved WS connection info.

        Args:
            conn_info: WsConnectionInfo with ws_url, token, target.

        base_url 计算：新引擎（aicoding 等）用 adapter.ws_path() 作 strip 后缀，
        openclaw/teclaw 走原 _build_base_url。
        """
        _adapter = self._adapter_for(engine_type)
        if _adapter is not None:
            base_url = self._strip_ws_url_to_base(conn_info.ws_url, _adapter.ws_path())
        else:
            base_url = self._build_base_url(conn_info, engine_type)
        headers = {"x-proxypass-token": conn_info.token}
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
        engine_type: str = "openclaw",
        bot_id: str = "",
        run_id: str | None = None,
    ) -> tuple[str, bool]:
        """Get an existing adapter session or create a new one.

        Args:
            session_client: The AsyncSessionClient to use.
            session_id: Optional existing session ID to look up.
            user_id: User ID passed to adapter when creating.
            metadata: Session metadata.
            engine_type: Engine type (e.g. "openclaw", "teclaw"), used for routing.
            bot_id: teamclaw bot id / agent id
            run_id: Optional run ID to look up.

        Returns:
            Tuple of (adapter_session_id, is_reused).
        """
        if engine_type == "teclaw":
            return await self._get_or_create_teclaw_session(
                session_client, session_id, user_id, metadata, bot_id
            )

        # 其他 engine 实现
        # TODO 拆分为独立逻辑
        if session_id:
            # 其他 engine 保持现状：有 session_id 就直接复用，不做存在性检查，engine 会自动创建
            logger.info(
                "Adapter session already exists: session_id=%s, reusing",
                session_id,
            )
            return session_id, True
        else:
            adapter_session = await session_client.create_session(
                title=metadata.get("title", None),
                user_id=user_id,
                agent_id=bot_id,
                uuid=run_id,
                model=metadata.get("model", None),
                engine=engine_type,
            )
            adapter_session_id = adapter_session.id
            if engine_type == "openclaw":
                # It is only for openclaw
                if not adapter_session_id.startswith("agent:main:"):
                    adapter_session_id = f"agent:main:{adapter_session_id}"
            logger.info("Adapter session created: session_id=%s", adapter_session_id)
            return adapter_session_id, False

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

    async def _get_or_create_teclaw_session(
        self,
        session_client: AsyncSessionClient,
        session_id: str | None,
        user_id: str,
        metadata: dict[str, Any],
        bot_id: str,
    ) -> tuple[str, bool]:
        """Get an existing teclaw adapter session or create a new one.

        teclaw 需要先判断 sessionKey 是否存在，不存在则创建。
        当 session_id 为 None 时，直接创建新会话。

        Args:
            session_client: The AsyncSessionClient to use.
            session_id: Existing session ID to look up, or None to create new.
            user_id: User ID passed to adapter when creating.
            metadata: Session metadata.
            bot_id: Bot identifier, used as agent_id for creating teclaw sessions.

        Returns:
            Tuple of (adapter_session_id, is_reused).
        """
        if session_id:
            # teclaw 需要先判断 sessionKey 是否存在，不存在则创建
            try:
                await session_client.get_session(session_id, "teclaw")
                logger.info(
                    "Adapter session already exists: session_id=%s, reusing",
                    session_id,
                )
                return session_id, True
            except Exception as e:
                logger.info(
                    "Adapter session not found: session_id=%s, error=%s, creating new",
                    session_id,
                    e,
                )
                # A trick logic, use session_id to create new session, but actually it is uuid
                adapter_session = await session_client.create_session(
                    title=metadata.get("title", None),
                    user_id=user_id,
                    model=metadata.get("model", None),
                    engine="teclaw",
                    agent_id=bot_id,
                    session_id=session_id,
                )
                adapter_session_id = adapter_session.id
                logger.info(
                    "Adapter session created: session_id=%s", adapter_session_id
                )
                return adapter_session_id, False
        else:
            adapter_session = await session_client.create_session(
                title=metadata.get("title", None),
                user_id=user_id,
                model=metadata.get("model", None),
                engine="teclaw",
                agent_id=bot_id,
            )
            adapter_session_id = adapter_session.id
            logger.info("Adapter session created: session_id=%s", adapter_session_id)
            return adapter_session_id, False

    def _create_session_consistency_key(
        self,
        engine_type: str,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
        session_id: str | None = None,
    ) -> str | None:
        """Create consistency key for session routing."""
        if session_id is not None:
            return session_id

        if engine_type == "openclaw":
            # Fixed prefix 'agent:main:'
            return f"agent:main:session:{run_id}:user:{user_id}"
        elif engine_type == "claude_code":
            return f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"
        else:
            # TODO
            return None


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
