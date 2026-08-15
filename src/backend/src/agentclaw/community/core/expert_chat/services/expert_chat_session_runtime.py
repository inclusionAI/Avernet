"""Runtime connection and Adapter session operations for expert chat."""

from __future__ import annotations

import traceback
from typing import Any, Dict, Optional
from urllib.parse import quote

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_baas_engine_bucket,
    resolve_provisioning,
)
from agentclaw.community.core.expert_chat.errors import (
    BotNotPublishedError,
    ChatPermissionError,
    ConnectionError,
    SessionCreateError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
)

logger = get_logger()

DEFAULT_EXPERT_CHAT_SESSION_TITLE = "新会话"


class ExpertChatSessionRuntimeMixin:
    """Connection authorization and Adapter session lifecycle operations."""

    def _get_connection(
        self, bot: Dict[str, Any], user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取 Bot 的连接信息

        分流:
        - service bot:caller 链路(``list_chat_bots`` / ``get_chat_session``)已通过
          ``baas_service.get_bind_id(SUCCESS)`` 把发布单 ``ext.binding.online`` 的
          binding_id 塞到 ``bot["binding_id"]``。直接走 ``resolve_for_binding``。
          **不能走 by-bot 入口** — 它会反查 ``ac_bots.binding_id``,那是 DRAFT
          binding,与 SUCCESS binding 数据上是两条不同记录。
        - personal bot:``bot.get("binding_id")`` 为 None,走 ``resolve_for_bot``
          (按 (bot_id, owner_id) 反查 ``ac_bots.binding_id``)。
          owner_id 显式从 ``bot["owner_id"]`` 取,**不再用 user_id 当 owner_id**:
          public bot 被他人调用 / collaborator 调用时,user_id 是 caller 工号,
          binding 仍在 owner 名下,用 caller id 必查不到。

        权限上移:caller 显式调 ``_check_chat_access``,失败 ``ChatPermissionError``,
        resolver 不被调(早失败语义)。

        Args:
            bot: Bot 信息字典(必须含 ``bot_id`` / ``owner_id`` / ``public``;
                service bot 链路下还含 ``binding_id``)
            user_id: 用户ID（caller 身份,权限校验 + builder device_affinity 用）

        Returns:
            连接信息，含 url、headers、use_proxy、engine_type 等字段

        Raises:
            ChatPermissionError: 非 owner / 非 public / 非 collaborator
            BotNotPublishedError: Bot 未绑定设备(resolver 抛 DeviceNotBoundError 翻译而来)
            ConnectionError: 底层连接失败
        """
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

        bot_id = bot["bot_id"]
        owner_id = bot.get("owner_id")
        binding_id = bot.get("binding_id")

        # Defense in depth: every path resolving a runtime connection must
        # enforce the same owner/public/collaborator authorization boundary.
        self._check_chat_access(bot, user_id)

        if not binding_id:
            raise ConnectionError(
                "(binding_id 未提供)服务未发布",
                error_code="5002",
            )
        logger.info(
            f"[ExpertChatService] Resolving device context: bot={bot_id}, "
            f"owner={owner_id}, user={user_id}, binding_id={binding_id}"
        )
        try:
            ctx = self._resolver.resolve_for_binding(binding_id, user_id, bot_id=bot_id)
        except DeviceNotBoundError as error:
            logger.warning(
                "[ExpertChatService] Bot has no active binding: %s: %s",
                bot_id,
                error,
            )
            raise BotNotPublishedError(f"Bot未绑定设备: {bot_id}")
        except Exception as error:
            error_msg = str(error)
            logger.error(
                "[ExpertChatService] Failed to resolve device context for bot=%s: %s: %s",
                bot_id,
                type(error).__name__,
                error,
            )
            logger.error(
                "[ExpertChatService] Resolver error traceback: %s",
                traceback.format_exc(),
            )
            raise ConnectionError(
                "无法连接到Bot服务",
                error_code="5001",
                original_error=error_msg,
            )

        conn = ctx.conn_info
        logger.info(
            "[ExpertChatService] Resolved device context: bot=%s binding_id=%s "
            "provider=%s conn_engine=%s active_engine=%s template_type=%s "
            "runtime_engine=%s",
            bot_id,
            binding_id,
            ctx.provider,
            conn.get("engine_type"),
            bot.get("active_engine"),
            bot.get("template_type"),
            (bot.get("template_config") or {}).get("active_runtime_engine_type"),
        )
        if conn.get("use_proxy"):
            logger.info(
                "[ExpertChatService] Got proxy connection: bot=%s, provider=%s, "
                "sandbox_id=%s",
                bot_id,
                ctx.provider,
                conn.get("sandbox_id"),
            )
        else:
            logger.info(
                "[ExpertChatService] Got direct connection: bot=%s, target=%s",
                bot_id,
                conn.get("target"),
            )
        return conn

    def _check_chat_access(self, bot: Dict[str, Any], user_id: Optional[str]) -> None:
        """Apply the existing owner/public/collaborator chat access policy.

        Access is granted to the owner, callers of public bots, and members
        registered as collaborators. Collaborator lookup failures fail closed.
        """
        bot_id = bot["bot_id"]
        owner_id = bot.get("owner_id")

        if owner_id == user_id:
            return
        if bot.get("public") == "1":
            return

        try:
            result = self._collaborator_service.check_collaborator_permission(
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=user_id,
                required_level=PermissionLevel.MEMBER,
            )
        except Exception as error:
            logger.warning(
                "[ExpertChatService] collaborator check failed for bot=%s, user=%s: %s",
                bot_id,
                user_id,
                error,
            )
            raise ChatPermissionError(
                f"User {user_id} has no chat access to bot {bot_id} "
                "(collaborator check failed)"
            )

        if result.get("has_permission", False):
            return

        logger.warning(
            "[ExpertChatService] ChatPermissionError: user=%s not allowed to chat with bot=%s",
            user_id,
            bot_id,
        )
        raise ChatPermissionError(f"User {user_id} has no chat access to bot {bot_id}")

    def _resolve_chat_engine_type(
        self, bot: Dict[str, Any], conn: Dict[str, Any]
    ) -> str:
        """Resolve the effective chat engine from authoritative Bot metadata."""
        template_config = bot.get("template_config") or {}
        if not isinstance(template_config, dict):
            template_config = {}
        raw_engine_type = (
            bot.get("active_engine")
            or template_config.get("active_runtime_engine_type")
            or conn.get("engine_type")
            or "openclaw"
        )
        return resolve_baas_engine_bucket(
            engine_type=raw_engine_type,
            template_type=bot.get("template_type") or "",
        )

    def _resolve_chat_engine_strategy(
        self, bot: Dict[str, Any], conn: Dict[str, Any]
    ):
        """Resolve the normalized chat engine and its registered strategy."""
        engine_type = self._resolve_chat_engine_type(bot, conn)
        template_config = bot.get("template_config")
        if not isinstance(template_config, dict):
            template_config = None
        return resolve_provisioning(
            bot_id=str(bot.get("bot_id") or ""),
            owner_id=str(bot.get("owner_id") or ""),
            bot_type=str(bot.get("bot_type") or ""),
            active_engine=engine_type,
            template_type=bot.get("template_type") or None,
            template_config=template_config,
        )

    async def _create_session(
        self,
        bot: Dict[str, Any],
        user_id: str,
        connection: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a session through the current runtime strategy."""
        conn = connection or self._get_connection(bot, user_id)
        ctx, strategy = self._resolve_chat_engine_strategy(bot, conn)
        conn["engine_type"] = ctx.active_engine

        if not strategy.uses_adapter_chat_session_lifecycle(ctx):
            session_key = strategy.build_local_chat_session_key(ctx, user_id=user_id)
            logger.info(
                "[ExpertChatService] Relay-managed chat session: bot=%s, user=%s, "
                "engine=%s, session=%s",
                bot.get("bot_id"),
                user_id,
                ctx.active_engine,
                session_key,
            )
            return session_key

        return await self._create_openclaw_session(conn, bot, user_id)

    async def _create_openclaw_session(
        self, conn: Dict[str, Any], bot: Dict[str, Any], user_id: str
    ) -> str:
        """Create a session through the active Adapter and return its full ID."""
        payload = {
            # Keep empty sessions consistent with the list placeholder across refreshes.
            "title": DEFAULT_EXPERT_CHAT_SESSION_TITLE,
            "user_id": user_id,
            "agent_id": bot["bot_id"],
            "engine": conn.get("engine_type", "openclaw"),
        }
        logger.info(
            "[ExpertChatService] Creating OpenClaw session via transport: "
            "user_id=%s, bot_id=%s, use_proxy=%s",
            user_id,
            bot["bot_id"],
            conn.get("use_proxy", False),
        )

        try:
            data = await self._transport.invoke(
                conn,
                "POST",
                "/api/sessions",
                body=payload,
            )
            logger.info("[ExpertChatService] Adapter POST response %s", data)
            raw_session_key = data.get("data", {}).get("id") or data.get("id")
            if not raw_session_key:
                logger.error(
                    "[ExpertChatService] No session key in adapter response: %s",
                    data,
                )
                raise Exception(f"Invalid response from adapter: {data}")
        except Exception as error:
            self._raise_session_create_error(error, "POST /api/sessions")

        logger.info(
            "[ExpertChatService] Looking for prefixed session ID for: %s",
            raw_session_key,
        )
        try:
            list_data = await self._transport.invoke(conn, "GET", "/api/sessions")
            sessions = list_data.get("data", [])
            if not isinstance(sessions, list):
                sessions = []
            logger.info(
                "[ExpertChatService] Got %s sessions from list",
                len(sessions),
            )
            for session in sessions:
                session_id = session.get("id", "")
                if (
                    session_id.lower().endswith(raw_session_key.lower())
                    or raw_session_key.lower() in session_id.lower()
                ):
                    logger.info(
                        "[ExpertChatService] Found prefixed session ID: %s (raw: %s)",
                        session_id,
                        raw_session_key,
                    )
                    return session_id
            logger.warning(
                "[ExpertChatService] No matching prefixed session found for: %s",
                raw_session_key,
            )
        except Exception as error:
            logger.warning(
                "[ExpertChatService] Failed to get prefixed session ID, using raw: %s",
                error,
            )

        logger.info(
            "[ExpertChatService] Created OpenClaw session via adapter: %s",
            raw_session_key,
        )
        return raw_session_key

    def _raise_session_create_error(self, error: Exception, operation: str) -> None:
        logger.error(
            "[ExpertChatService] Unexpected error when %s: %s: %s",
            operation,
            type(error).__name__,
            error,
        )
        logger.error(
            "[ExpertChatService] Unexpected error traceback: %s",
            traceback.format_exc(),
        )
        error_msg = str(error)
        if "Connection refused" in error_msg or "Cannot connect to host" in error_msg:
            raise SessionCreateError(
                "Bot服务暂不可用，请稍后重试",
                error_code="50201",
                original_error=error_msg,
            )
        if "Failed to connect" in error_msg:
            raise SessionCreateError(
                "连接Bot服务失败",
                error_code="5002",
                original_error=error_msg,
            )
        if "Adapter returned" in error_msg:
            if "404" in error_msg:
                raise SessionCreateError(
                    "Bot服务暂不可用，请稍后重试",
                    error_code="40402",
                    original_error=error_msg,
                )
            if (
                "500" in error_msg
                or "502" in error_msg
                or "503" in error_msg
                or "gateway" in error_msg.lower()
            ):
                raise SessionCreateError(
                    "Bot服务暂不可用，请稍后重试",
                    error_code="50201",
                    original_error=error_msg,
                )
        raise SessionCreateError(
            f"创建 Session 失败: {error_msg[:100]}",
            error_code="5003",
            original_error=error_msg,
        )

    async def _check_session_exists(
        self,
        bot: Dict[str, Any],
        session_key: str,
        user_id: Optional[str] = None,
        connection: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check whether a session still exists in the active Adapter."""
        conn = connection or self._get_connection(bot, user_id)
        ctx, strategy = self._resolve_chat_engine_strategy(bot, conn)
        conn["engine_type"] = ctx.active_engine

        if not strategy.uses_adapter_chat_session_lifecycle(ctx):
            logger.info(
                "[ExpertChatService] Relay-managed chat session check: skipping "
                "adapter pre-check for session=%s, engine=%s",
                session_key,
                ctx.active_engine,
            )
            return True

        try:
            encoded_session_key = quote(session_key, safe="")
            await self._transport.invoke(
                conn, "GET", f"/api/sessions/{encoded_session_key}"
            )
            return True
        except Exception as error:
            if self._is_adapter_not_found(error):
                logger.warning(
                    "[ExpertChatService] Session not found in adapter: %s",
                    session_key,
                )
                return False
            logger.warning(
                "[ExpertChatService] Error checking session %s via transport: %s: %s",
                session_key,
                type(error).__name__,
                error,
            )
            return True

    async def _delete_adapter_session(
        self,
        bot: Dict[str, Any],
        session_key: str,
        user_id: Optional[str] = None,
        connection: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Delete a session through the active Adapter."""
        # Resolve from Bot metadata before opening a connection. Historical
        # caller-instance bindings can report a fallback engine that must not
        # override the Bot's authoritative relay strategy.
        ctx, strategy = self._resolve_chat_engine_strategy(bot, connection or {})
        if not strategy.uses_adapter_chat_session_lifecycle(ctx):
            logger.info(
                "[ExpertChatService] Skipping adapter session deletion for "
                "relay-managed engine: bot=%s, session=%s, engine=%s",
                bot.get("bot_id"),
                session_key,
                ctx.active_engine,
            )
            return

        conn = connection or self._get_connection(bot, user_id)
        ctx, strategy = self._resolve_chat_engine_strategy(bot, conn)
        conn["engine_type"] = ctx.active_engine
        if not strategy.uses_adapter_chat_session_lifecycle(ctx):
            return

        logger.info(
            "[ExpertChatService] Deleting adapter session via transport: session=%s",
            session_key,
        )
        try:
            encoded_session_key = quote(session_key, safe="")
            await self._transport.invoke(
                conn, "DELETE", f"/api/sessions/{encoded_session_key}"
            )
            logger.info(
                "[ExpertChatService] Successfully deleted adapter session: %s",
                session_key,
            )
        except Exception as error:
            if self._is_adapter_not_found(error):
                if self._is_ambiguous_adapter_delete_not_found(error):
                    # The generic Engine DELETE endpoint uses this 404 for both
                    # a missing session and an operational deletion failure.
                    # Preserve ownership so the latter remains retryable.
                    logger.error(
                        "[ExpertChatService] Adapter returned ambiguous delete "
                        "result; preserving ownership: session=%s",
                        session_key,
                    )
                    raise
                logger.warning(
                    "[ExpertChatService] Session not found in adapter: %s",
                    session_key,
                )
                return
            logger.error(
                "[ExpertChatService] Unexpected error when DELETE session via "
                "transport: %s: %s",
                type(error).__name__,
                error,
            )
            logger.error(
                "[ExpertChatService] Delete session traceback: %s",
                traceback.format_exc(),
            )
            raise

    @staticmethod
    def _is_adapter_not_found(error: Exception) -> bool:
        if isinstance(error, DeviceAdapterEndpointNotFoundError):
            return True
        if isinstance(error, DeviceAdapterHTTPStatusError):
            return error.status_code == 404
        error_msg = str(error)
        return "Adapter returned" in error_msg and "404" in error_msg

    @staticmethod
    def _is_ambiguous_adapter_delete_not_found(error: Exception) -> bool:
        """Identify the Engine 404 that also represents runtime delete failure."""
        return "Session not found or delete failed" in str(error)
