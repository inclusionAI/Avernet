"""
ExpertChat Service — 用户与专家Bot对话业务逻辑层

职责：
- 用户与公开市场专家Bot的对话管理
- 添加/移除专家Bot到对话列表
- 管理会话(Session)
- 获取设备连接信息

依赖注入：
- ExpertChatRepository: 数据持久化
- BotInfoProvider: Bot信息查询
- DeviceConnectionProvider: 设备连接服务
"""
from __future__ import annotations

import traceback
from typing import Dict, Any, List, Optional, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError,
    BotNotActiveError,
    BotNotPublishedError,
    ChatPermissionError,
    SessionCreateError,
    ConnectionError,
)
from agentclaw.community.core.expert_chat.repository import ExpertChatRepository
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport

logger = get_logger()


# ============ Protocol Definitions ============

@runtime_checkable
class DeviceConnectionProvider(Protocol):
    """Protocol for device connection provider.

    Decoupled from old device service to avoid circular dependencies.
    """
    def get_device_connection_v2(
        self, binding_id: str, user_id: str, nick_name: str
    ) -> Dict[str, Any]:
        """获取设备连接信息

        Args:
            binding_id: 设备绑定ID
            user_id: 用户ID
            nick_name: 用户昵称

        Returns:
            连接信息字典，包含 url, headers, use_proxy 等
        """
        ...


@runtime_checkable
class BotInfoProvider(Protocol):
    """Protocol for bot info provider."""

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        """通过bot_id和owner_id获取Bot信息

        Args:
            bot_id: Bot ID
            owner_id: Bot所有者ID

        Returns:
            Bot信息字典，包含 bot_name, status, binding_id 等
        """
        ...


# ============ Service Implementation ============

class ExpertChatService:
    """用户与专家Bot对话业务逻辑"""

    @inject
    def __init__(
        self,
        repository: ExpertChatRepository,
        bot_repo: BotRepository,
        device_provider: DeviceService,
        baas_service: BaasService,
        resolver: DeviceContextResolver,
        collaborator_service: CollaboratorService,
        transport: DeviceAdapterTransport,
    ):
        """Initialise.

        ``BotRepository`` and ``DeviceService`` structurally satisfy
        the ``BotInfoProvider`` and ``DeviceConnectionProvider``
        Protocols respectively; we type the params with the concrete
        classes so the injector can resolve them via the bound
        singletons.

        ``BaasService`` is injected here so service-bot ``bind_id``
        lookups don't have to reach back through ``get_app_injector``.

        Task 2.5 — 新增两个依赖:
        ``DeviceContextResolver``: 全仓唯一 provider 解析点,替代
        ``DeviceConnectionProvider.get_device_connection_v2`` 拿连接。
        ``CollaboratorService``: 权限上移后显式调,旧 v2 内部副作用搬出。
        ``DeviceService`` 仍保留作为 ``device_provider`` (其他业务路径暂未迁)。
        """
        self._repo = repository
        self._bot_repo: BotInfoProvider = bot_repo
        self._device_provider: DeviceConnectionProvider = device_provider
        self._baas_service = baas_service
        self._resolver = resolver
        self._collaborator_service = collaborator_service
        self._transport = transport

    # ============ Public Methods ============

    def add_chat_bot(self, user_id: str, bot_id: str, owner_id: str) -> Dict[str, Any]:
        """添加专家Bot到用户对话列表

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            创建的记录

        Raises:
            BotNotFoundError: Bot 不存在
            BotNotActiveError: Bot 状态不是 ACTIVE
            BotNotPublishedError: Bot 未绑定设备
        """
        # 1. 校验 bot 是否存在且 ACTIVE（通过 bot_id + owner_id 唯一定位）
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            logger.warning(f"[ExpertChatService] Bot not found: {bot_id}, owner={owner_id}")
            raise BotNotFoundError(f"Bot不存在: {bot_id}")

        if bot.get("status") != "ACTIVE":
            logger.warning(f"[ExpertChatService] Bot not active: {bot_id}, status={bot.get('status')}")
            raise BotNotActiveError(f"Bot未激活: {bot_id}")

        # 2. 校验 Bot 是否已绑定设备
        binding_id = bot.get("binding_id")
        if not binding_id:
            logger.warning(f"[ExpertChatService] Bot not bound to device: {bot_id}")
            raise BotNotPublishedError(f"Bot未绑定设备: {bot_id}")

        # 3. 插入记录（存储 bot_id 和 owner_id）
        result = self._repo.add_chat_bot(user_id, bot_id, owner_id)

        logger.info(
            "[ExpertChatService] Added chat bot: user=%s, bot=%s, owner=%s, binding_id=%s",
            user_id,
            bot_id,
            owner_id,
            binding_id,
        )
        return result

    def list_chat_bots(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户对话列表中的专家Bot（实时查 ac_bots 获取 name，同时检查绑定状态）"""
        # 1. 获取用户的 bot 列表（包含 bot_id 和 owner_id）
        bot_entries = self._repo.list_chat_bots(user_id)

        if not bot_entries:
            return []

        # 2. 通过 bot_id + owner_id 查询每个 bot 的最新信息
        result = []
        for entry in bot_entries:
            bot_id = entry["bot_id"]
            owner_id = entry["owner_id"]

            # 使用 get_by_id_and_owner 唯一定位 bot
            bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
            if not bot:
                logger.warning(f"[ExpertChatService] Bot not found in list: {bot_id}, owner={owner_id}")
                continue

            # 检查是否有 binding_id（即是否绑定了设备）
            # 根据 bot_type 决定获取 bind_id 的方式
            bot_type = bot.get("bot_type", "personal")
            if bot_type == "service":
                # 服务型 Bot：通过 BaasService 获取 bind_id
                from agentclaw.community.core.service_bot.repository.models import PublishStatus
                binding_id = self._baas_service.get_bind_id(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    bot_type=bot_type,
                    publish_status=PublishStatus.SUCCESS.value,
                )
            else:
                # 个人 Bot：从 bot 数据中获取 binding_id
                binding_id = bot.get("binding_id")
            binding_available = bool(binding_id)

            result.append({
                "bot_id": bot_id,
                "owner_id": owner_id,
                "bot_name": bot.get("bot_name") or bot_id,
                "owner_name": bot.get("owner_name", "未知"),
                "status": bot.get("status", "UNKNOWN"),
                # 设备绑定相关字段
                "binding_available": binding_available,
                "binding_id": binding_id,
                # Bot 扩展信息
                "ext": bot.get("ext"),
            })

        logger.info(f"[ExpertChatService] Listed {len(result)} chat bots for user={user_id}")
        return result

    async def remove_chat_bot(self, user_id: str, bot_id: str, owner_id: str) -> bool:
        """从对话列表移除专家Bot并清理 session

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功
        """
        # 1. 尝试删除 session（包括 Adapter 和本地）
        try:
            await self.delete_chat_session(user_id, bot_id, owner_id)
        except BotNotFoundError:
            # Bot 不存在，继续移除
            pass
        except Exception as e:
            logger.warning(f"[ExpertChatService] Failed to delete session during remove: {e}")

        # 2. 移除记录
        result = self._repo.remove_chat_bot(user_id, bot_id, owner_id)
        logger.info(f"[ExpertChatService] Removed chat bot: user={user_id}, bot={bot_id}, owner={owner_id}")
        return result

    async def get_chat_session(self, user_id: str, bot_id: str, owner_id: str) -> Dict[str, Any]:
        """获取/创建与专家Bot的 chat session

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            {
                "session_key": "session:xxx",
                "is_new": True/False,
                "connection": {
                    "type": "websocket",
                    "target": "ip:port",
                    "token": "xxx",
                    "engine_type": "openclaw"
                }
            }

        Raises:
            BotNotFoundError: Bot 不存在
            BotNotActiveError: Bot 状态不是 ACTIVE
        """
        # 1. 校验 bot 是否存在且属于该 owner（通过 bot_id + owner_id 唯一定位）
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot不存在: {bot_id}")

        if bot.get("status") != "ACTIVE":
            raise BotNotActiveError(f"Bot未激活: {bot_id}")

        # 根据 bot_type 获取 binding_id
        # 服务型 Bot：通过 BaasService 获取发布成功的发布单
        bot_type = bot.get("bot_type", "personal")
        if bot_type == "service":
            from agentclaw.community.core.service_bot.repository.models import PublishStatus
            binding_id = self._baas_service.get_bind_id(
                bot_id=bot_id,
                owner_id=owner_id,
                bot_type=bot_type,
                publish_status=PublishStatus.SUCCESS.value,
            )
            # 将 binding_id 设置到 bot 对象中，供后续 _get_connection 使用
            bot["binding_id"] = binding_id

        # 2. 校验 bot 是否在用户的对话列表中
        chat_bots = self._repo.list_chat_bots(user_id)
        bot_in_list = any(
            entry["bot_id"] == bot_id and entry["owner_id"] == owner_id
            for entry in chat_bots
        )
        if not bot_in_list:
            raise BotNotFoundError(f"Bot不在对话列表中: {bot_id}")

        # 3. 查是否已有 session
        session_key = self._repo.get_session(user_id, bot_id, owner_id)

        # 4. 有则校验有效性
        if session_key:
            exists = await self._check_session_exists(bot, session_key, user_id)
            if exists:
                conn = self._get_connection(bot, user_id)
                logger.info(f"[ExpertChatService] Reusing session: user={user_id}, bot={bot_id}, session={session_key}")
                return {
                    "session_key": session_key,
                    "is_new": False,
                    "connection": conn
                }
            else:
                # Session 已失效，删除旧记录
                self._repo.delete_session(user_id, bot_id, owner_id)

        # 5. 没有或无效则创建新 session
        session_key = await self._create_session(bot, user_id)
        self._repo.save_session(user_id, bot_id, owner_id, session_key)

        conn = self._get_connection(bot, user_id)
        logger.info(f"[ExpertChatService] Created new session: user={user_id}, bot={bot_id}, session={session_key}")

        return {
            "session_key": session_key,
            "is_new": True,
            "connection": conn
        }

    async def delete_chat_session(self, user_id: str, bot_id: str, owner_id: str) -> bool:
        """删除用户与专家Bot的 chat session

        Args:
            user_id: 用户 ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功删除
        """
        # 1. 校验 bot 是否存在
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot不存在: {bot_id}")

        # 2. 获取本地 session_key
        session_key = self._repo.get_session(user_id, bot_id, owner_id)
        if not session_key:
            logger.warning(f"[ExpertChatService] No session found for user={user_id}, bot={bot_id}, owner={owner_id}")
            return True  # 本来就没有，也算成功

        # 3. 调用 Adapter 删除 session
        try:
            await self._delete_adapter_session(bot, session_key, user_id)
            logger.info(f"[ExpertChatService] Deleted adapter session: {session_key}")
        except Exception as e:
            logger.error(f"[ExpertChatService] Failed to delete adapter session {session_key}: {e}")
            # Adapter 删除失败也要继续删除本地记录

        # 4. 删除本地 session 映射
        self._repo.delete_session(user_id, bot_id, owner_id)
        logger.info(
            "[ExpertChatService] Deleted local session mapping: user=%s, bot=%s, owner=%s",
            user_id,
            bot_id,
            owner_id,
        )

        return True

    # ============ Private Methods ============

    def _get_connection(self, bot: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
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

        # 1. 权限上移 — 失败时 resolver 不被调(早失败语义)
        self._check_chat_access(bot, user_id)

        # 2. 走 resolver (全仓唯一 provider 解析点,替代 v2)
        if not binding_id:
            raise ConnectionError(
                "binding_id 未提供",
                error_code="5001",
            )
        logger.info(
            f"[ExpertChatService] Resolving device context: bot={bot_id}, "
            f"owner={owner_id}, user={user_id}, binding_id={binding_id}"
        )
        try:
            ctx = self._resolver.resolve_for_binding(binding_id, user_id, bot_id=bot_id)
        except DeviceNotBoundError as e:
            logger.warning(f"[ExpertChatService] Bot has no active binding: {bot_id}: {e}")
            raise BotNotPublishedError(f"Bot未绑定设备: {bot_id}")
        except Exception as e:
            error_msg = str(e)
            logger.error(
                "[ExpertChatService] Failed to resolve device context for bot=%s: %s: %s",
                bot_id,
                type(e).__name__,
                e,
            )
            logger.error(f"[ExpertChatService] Resolver error traceback: {traceback.format_exc()}")
            raise ConnectionError(
                "无法连接到Bot服务",
                error_code="5001",
                original_error=error_msg
            )

        conn = ctx.conn_info

        if conn.get("use_proxy"):
            logger.info(
                "[ExpertChatService] Got ARCA proxy connection: bot=%s, sandbox_id=%s",
                bot_id,
                conn.get("sandbox_id"),
            )
        else:
            logger.info(f"[ExpertChatService] Got direct connection: bot={bot_id}, target={conn.get('target')}")

        return conn

    def _check_chat_access(self, bot: Dict[str, Any], user_id: Optional[str]) -> None:
        """权限上移 — 沿用 device_service.py:804-839 旧 v2 内 4 分支语义。

        改造前: 权限校验藏在 ``get_device_connection`` 副作用里,经由
        ``get_device_connection_v2`` 间接触发,raise ``InvalidDeviceStatusError``。
        改造后: caller 显式调,失败时 resolver 不被调到。

        放行规则(与旧 v2 等价):
        - bot owner 本人 → 放行
        - 非 owner 但 bot.public='1' → 放行
        - 协作者 (PermissionLevel.MEMBER 及以上) → 放行
        - 否则 → raise ChatPermissionError

        Args:
            bot: Bot 信息字典(必须含 ``bot_id`` / ``owner_id`` / ``public``)
            user_id: caller 身份

        Raises:
            ChatPermissionError: 不满足上述任何分支
        """
        bot_id = bot["bot_id"]
        owner_id = bot.get("owner_id")

        # 分支 1: owner 本人
        if owner_id == user_id:
            return

        # 分支 2: 公开 bot
        if bot.get("public") == "1":
            return

        # 分支 3: 协作者
        try:
            result = self._collaborator_service.check_collaborator_permission(
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=user_id,
                required_level=PermissionLevel.MEMBER,
            )
        except Exception as e:
            # 兜底 — collaborator 查询失败按拒绝处理,日志告警
            logger.warning(
                f"[ExpertChatService] check_collaborator_permission failed for "
                f"bot={bot_id}, user={user_id}: {e}"
            )
            raise ChatPermissionError(
                f"User {user_id} has no chat access to bot {bot_id} (collaborator check failed)"
            )

        if result.get("has_permission", False):
            return

        # 分支 4: 拒绝
        logger.warning(
            f"[ExpertChatService] ChatPermissionError: user={user_id} not allowed to chat with bot={bot_id}"
        )
        raise ChatPermissionError(
            f"User {user_id} has no chat access to bot {bot_id}"
        )

    async def _create_session(self, bot: Dict[str, Any], user_id: str) -> str:
        """调用 Adapter 创建 session

        Args:
            bot: Bot 信息字典
            user_id: 用户ID

        Returns:
            session_key
        """
        conn = self._get_connection(bot, user_id)
        engine_type = conn.get("engine_type", "openclaw")

        if engine_type == "aicoding":
            return await self._create_aicoding_session(conn, bot, user_id)
        elif engine_type == "claude_code":
            logger.info(
                "[ExpertChatService] claude_code session: delegating to aicoding session for bot=%s, user=%s",
                bot.get("bot_id"), user_id,
            )
            return await self._create_aicoding_session(conn, bot, user_id)

        return await self._create_openclaw_session(conn, bot, user_id)

    async def _create_aicoding_session(self, conn: Dict[str, Any], bot: Dict[str, Any], user_id: str) -> str:
        """
        AI Coding 引擎：teamclaw-aicoding-relay 按需建 session，本地生成 key，不调 Adapter。
        """
        import uuid
        session_key = f"session:{uuid.uuid4()}:user:{user_id}"
        logger.info(f"[aicoding] local session key generated: {session_key}")
        return session_key

    async def _create_openclaw_session(self, conn: Dict[str, Any], bot: Dict[str, Any], user_id: str) -> str:
        """调用 Adapter 创建 OpenClaw session，返回带前缀的完整 session ID"""
        payload = {
            "title": f"Chat with {bot.get('bot_name', bot['bot_id'])}",
            "user_id": user_id,
            "agent_id": bot["bot_id"],
            "engine": "openclaw",
        }
        use_proxy = conn.get("use_proxy", False)
        logger.info(
            "[ExpertChatService] Creating OpenClaw session via transport: "
            "user_id=%s, bot_id=%s, use_proxy=%s",
            user_id,
            bot["bot_id"],
            use_proxy,
        )

        try:
            data = await self._transport.invoke(
                conn,
                "POST",
                "/api/sessions",
                body=payload,
            )
            logger.info(f"[ExpertChatService] Adapter POST response {data}")
            raw_session_key = data.get("data", {}).get("id") or data.get("id")
            if not raw_session_key:
                logger.error(f"[ExpertChatService] No session key in adapter response: {data}")
                raise Exception(f"Invalid response from adapter: {data}")
        except Exception as e:
            self._raise_session_create_error(e, "POST /api/sessions")

        # 创建成功后，通过 list 获取带前缀的完整 session ID
        logger.info(f"[ExpertChatService] Looking for prefixed session ID for: {raw_session_key}")
        try:
            list_data = await self._transport.invoke(conn, "GET", "/api/sessions")
            sessions = list_data.get("data", [])
            if not isinstance(sessions, list):
                sessions = []
            logger.info(f"[ExpertChatService] Got {len(sessions)} sessions from list")
            for s in sessions:
                session_id = s.get("id", "")
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
            logger.warning(f"[ExpertChatService] No matching prefixed session found for: {raw_session_key}")
        except Exception as e:
            logger.warning(f"[ExpertChatService] Failed to get prefixed session ID, using raw: {e}")

        logger.info(f"[ExpertChatService] Created OpenClaw session via adapter: {raw_session_key}")
        return raw_session_key

    def _raise_session_create_error(self, error: Exception, operation: str) -> None:
        logger.error(
            "[ExpertChatService] Unexpected error when %s: %s: %s",
            operation,
            type(error).__name__,
            error,
        )
        logger.error(f"[ExpertChatService] Unexpected error traceback: {traceback.format_exc()}")
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

    async def _check_session_exists(self, bot: Dict[str, Any], session_key: str, user_id: Optional[str] = None) -> bool:
        """
        调用 Adapter 校验 session 是否还存在。
        AI Coding 引擎：teamclaw-aicoding-relay 按需创建 session，始终返回 True。
        """
        conn = self._get_connection(bot, user_id)

        # AI Coding 引擎：session 由 teamclaw-aicoding-relay 按需创建，无需预检
        if conn.get("engine_type") == "aicoding":
            return True
        # claude_code 引擎：session 按需创建，无需预检
        if conn.get("engine_type") == "claude_code":
            logger.info(
                "[ExpertChatService] claude_code session check: skipping adapter pre-check for session=%s",
                session_key,
            )
            return True

        try:
            await self._transport.invoke(conn, "GET", f"/api/sessions/{session_key}")
            return True
        except Exception as e:
            if self._is_adapter_not_found(e):
                logger.warning(f"[ExpertChatService] Session not found in adapter: {session_key}")
                return False
            # 网络错误或其他状态，保守起见认为存在
            logger.warning(
                "[ExpertChatService] Error checking session %s via transport: %s: %s",
                session_key,
                type(e).__name__,
                e,
            )
            return True

    async def _delete_adapter_session(
        self,
        bot: Dict[str, Any],
        session_key: str,
        user_id: Optional[str] = None,
    ) -> None:
        """调用 Adapter 删除 session"""
        # aicoding session 由 teamclaw-aicoding-relay 按需创建，Adapter 无 /api/sessions 端点，无需删除
        if bot.get("engine_type") == "aicoding":
            logger.info(f"[ExpertChatService] Skipping adapter session deletion for aicoding: {session_key}")
            return
        # claude_code session 同理，按需创建，无需删除
        if bot.get("engine_type") == "claude_code":
            logger.info(
                "[ExpertChatService] Skipping adapter session deletion for claude_code: bot=%s, session=%s",
                bot.get("bot_id"), session_key,
            )
            return

        conn = self._get_connection(bot, user_id)
        if conn.get("engine_type") == "aicoding":
            logger.info(f"[ExpertChatService] Skipping adapter session deletion for aicoding: {session_key}")
            return
        if conn.get("engine_type") == "claude_code":
            logger.info(
                "[ExpertChatService] Skipping adapter session deletion for claude_code: bot=%s, session=%s",
                bot.get("bot_id"),
                session_key,
            )
            return

        logger.info(f"[ExpertChatService] Deleting adapter session via transport: session={session_key}")

        try:
            await self._transport.invoke(conn, "DELETE", f"/api/sessions/{session_key}")
            logger.info(f"[ExpertChatService] Successfully deleted adapter session: {session_key}")
        except Exception as e:
            if self._is_adapter_not_found(e):
                logger.warning(f"[ExpertChatService] Session not found in adapter: {session_key}")
                return
            logger.error(
                "[ExpertChatService] Unexpected error when DELETE session via transport: %s: %s",
                type(e).__name__,
                e,
            )
            logger.error(f"[ExpertChatService] Delete session traceback: {traceback.format_exc()}")
            raise

    @staticmethod
    def _is_adapter_not_found(error: Exception) -> bool:
        error_msg = str(error)
        return "Adapter returned" in error_msg and "404" in error_msg
