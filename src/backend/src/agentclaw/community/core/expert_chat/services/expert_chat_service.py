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
from datetime import datetime
from typing import Dict, Any, List, Optional, Protocol, runtime_checkable
from urllib.parse import quote

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.caller_identity.models import McpCallType
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
from agentclaw.community.core.repository.protocols.chat import ExpertChatRepository
from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import (
    ExpertChatInstanceService,
)
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_baas_engine_bucket,
    resolve_provisioning,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTransport,
)

logger = get_logger()

_ADAPTER_SESSION_PAGE_SIZE = 500
_EXACT_SESSION_LOOKUP_ENGINES = {"openclaw", "hermes", "claude_code"}
_LEGACY_LOCAL_SESSION_ENGINES = {"aicoding", "claude_code"}
_RELAY_SESSION_CREATE_TIMEOUT_SECONDS = 330.0


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
        instance_service: ExpertChatInstanceService,
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

        ``ExpertChatInstanceService``: Caller 模式下,为每个 caller
        分配独立的 baas 容器实例。
        """
        self._repo = repository
        self._bot_repo: BotInfoProvider = bot_repo
        self._device_provider: DeviceConnectionProvider = device_provider
        self._baas_service = baas_service
        self._resolver = resolver
        self._collaborator_service = collaborator_service
        self._transport = transport
        self._instance_service = instance_service

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
        """从对话列表移除专家Bot并清理全部 session

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID

        Returns:
            是否成功
        """
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        legacy_session_key = self._repo.get_session(user_id, bot_id, owner_id)
        if legacy_session_key:
            self._repo.add_owned_session(
                user_id, bot_id, owner_id, legacy_session_key
            )
        owned_sessions = self._repo.list_owned_sessions(user_id, bot_id, owner_id)

        if bot and owned_sessions:
            connection, need_poll = await self._prepare_chat_connection(
                bot, user_id, owner_id, None
            )
            if need_poll or connection is None:
                raise ConnectionError(
                    "Bot服务正在启动，请稍后重试",
                    error_code="5001",
                )

            remaining_session_keys = [row["session_key"] for row in owned_sessions]
            for row in owned_sessions:
                session_key = row["session_key"]
                await self._delete_adapter_session(
                    bot,
                    session_key,
                    user_id,
                    connection=connection,
                )
                await self._remove_session_favorite(
                    connection, session_key, user_id
                )
                # Persist progress after each confirmed runtime deletion. If a
                # later deletion fails, retrying must not revisit a session that
                # the Engine has already removed.
                self._repo.delete_owned_session(
                    user_id, bot_id, owner_id, session_key
                )
                remaining_session_keys.remove(session_key)
                if legacy_session_key == session_key:
                    if remaining_session_keys:
                        legacy_session_key = remaining_session_keys[0]
                        self._repo.save_session(
                            user_id,
                            bot_id,
                            owner_id,
                            legacy_session_key,
                        )
                    else:
                        self._repo.delete_session(user_id, bot_id, owner_id)
                        legacy_session_key = None
        elif not bot and owned_sessions:
            logger.warning(
                "[ExpertChatService] Bot missing during removal; runtime session "
                "cleanup is unavailable: bot=%s owner=%s sessions=%s",
                bot_id,
                owner_id,
                len(owned_sessions),
            )

        # Runtime cleanup is complete (or impossible because the Bot no longer
        # exists), so the local indexes can now be removed together.
        self._repo.delete_all_owned_sessions(user_id, bot_id, owner_id)
        if legacy_session_key:
            self._repo.delete_session(user_id, bot_id, owner_id)

        result = self._repo.remove_chat_bot(user_id, bot_id, owner_id)
        logger.info(
            f"[ExpertChatService] Removed chat bot: user={user_id}, bot={bot_id}, owner={owner_id}"
        )
        return result

    async def get_chat_session(
        self, user_id: str, bot_id: str, owner_id: str, iam_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取/创建与专家Bot的 chat session

        Authorization order (all checks BEFORE expensive operations):
        1. Bot exists and is ACTIVE
        2. Bot is in user's chat list
        3. User has chat access (owner/public/collaborator)
        4. Then: create/retrieve container or binding

        Args:
            user_id: 用户ID（使用人）
            bot_id: Bot ID
            owner_id: Bot 所有者ID
            iam_token: IAM token（可选）

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
            BotNotFoundError: Bot 不存在或不在对话列表中
            BotNotActiveError: Bot 状态不是 ACTIVE
            ChatPermissionError: 用户无聊天权限
        """
        bot = self._get_authorized_chat_bot(user_id, bot_id, owner_id)
        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if need_poll:
            return {
                "session_key": None,
                "is_new": True,
                "connection": connection,
                "need_poll": True,
            }

        # 5. 查是否已有 session
        session_key = self._repo.get_session(user_id, bot_id, owner_id)

        # 6. 有则校验有效性
        if session_key:
            exists = await self._check_session_exists(
                bot, session_key, user_id, connection=connection
            )
            if exists:
                self._repo.add_owned_session(user_id, bot_id, owner_id, session_key)
                logger.info(
                    f"[ExpertChatService] Reusing session: user={user_id}, bot={bot_id}, session={session_key}"
                )
                return {
                    "session_key": session_key,
                    "is_new": False,
                    "connection": connection,
                }
            else:
                # Session 已失效，删除旧记录
                self._repo.delete_owned_session(
                    user_id, bot_id, owner_id, session_key
                )
                self._repo.delete_session(user_id, bot_id, owner_id)

        # 7. 没有或无效则创建新 session
        session_key = await self._create_session(
            bot, user_id, connection=connection
        )
        self._repo.save_session(user_id, bot_id, owner_id, session_key)
        self._repo.add_owned_session(user_id, bot_id, owner_id, session_key)

        logger.info(
            f"[ExpertChatService] Created new session: user={user_id}, bot={bot_id}, session={session_key}"
        )

        return {
            "session_key": session_key,
            "is_new": True,
            "connection": connection,
        }

    async def list_chat_sessions(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: Optional[str] = None,
        favorite_only: bool = False,
        limit: int = 20,
        offset: int = 0,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List one user's sessions for one expert Bot.

        The Backend-owned index is authoritative for ownership. Adapter data is
        only used to enrich those already-authorized keys with live metadata.
        """
        bot = self._get_authorized_chat_bot(user_id, bot_id, owner_id)
        legacy_session_key = self._repo.get_session(user_id, bot_id, owner_id)
        if legacy_session_key:
            self._repo.add_owned_session(user_id, bot_id, owner_id, legacy_session_key)

        rows = self._repo.list_owned_sessions(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=session_key,
        )
        if not rows:
            return {"total": 0, "items": []}

        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if favorite_only and need_poll:
            return {"total": 0, "items": [], "need_poll": True}

        if favorite_only and connection is not None:
            favorite_items = await self._list_favorite_sessions(
                connection, user_id, bot_id
            )
            rows_by_key = {row["session_key"]: row for row in rows}
            items = []
            for favorite in favorite_items:
                favorite_key = favorite.get("id")
                row = rows_by_key.get(favorite_key)
                if row is None:
                    continue
                item = dict(favorite)
                for key, value in self._placeholder_session(row).items():
                    item.setdefault(key, value)
                items.append(item)
            total = len(items)
            return {
                "total": total,
                "items": items[offset : offset + limit],
            }

        adapter_items = {}
        if not need_poll and connection is not None:
            adapter_items = await self._list_owned_adapter_sessions(
                connection=connection,
                rows=rows,
                user_id=user_id,
            )

        items = []
        engine_type = connection.get("engine_type", "openclaw") if connection else None
        for row in rows:
            item = adapter_items.get(row["session_key"])
            if item is None:
                items.append(self._placeholder_session(row))
                continue
            enriched = dict(item)
            for key, value in self._placeholder_session(row).items():
                enriched.setdefault(key, value)
            if engine_type == "openclaw":
                # OpenClaw currently synthesizes Session updated_at at conversion
                # time. Prefer the real message timestamp, then the stable Backend
                # ownership creation time, so repeated reads cannot reorder rows.
                last_message = enriched.get("last_message")
                last_message_at = (
                    last_message.get("gmt_created")
                    if isinstance(last_message, dict)
                    else None
                )
                stable_modified = last_message_at or row.get("gmt_create")
                if stable_modified:
                    enriched["gmt_modified"] = stable_modified
            items.append(enriched)

        items.sort(key=self._session_sort_key, reverse=True)
        total = len(items)
        return {
            "total": total,
            "items": items[offset : offset + limit],
            **({"need_poll": True} if need_poll else {}),
        }

    async def create_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Always create a new session and make it the legacy default."""
        bot = self._get_authorized_chat_bot(user_id, bot_id, owner_id)
        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if need_poll:
            return {
                "session_key": None,
                "is_new": True,
                "connection": connection,
                "need_poll": True,
            }

        created_session_key = await self._create_session(
            bot,
            user_id,
            connection=connection,
            prefer_adapter_for_relay=True,
        )
        self._repo.add_owned_session(user_id, bot_id, owner_id, created_session_key)
        # Legacy clients resume the latest session created by the new UI.
        self._repo.save_session(user_id, bot_id, owner_id, created_session_key)
        return {
            "session_key": created_session_key,
            "is_new": True,
            "connection": connection,
        }

    async def connect_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
        iam_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return connection data for one session owned by the caller."""
        bot = self._get_authorized_chat_bot(user_id, bot_id, owner_id)
        # 以下为安全注释COSEC：会话归属必须在建立容器连接前由登录用户维度校验。
        owned = self._repo.get_owned_session(user_id, bot_id, owner_id, session_key)
        if not owned:
            raise BotNotFoundError("Session不存在或不属于当前用户")

        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, iam_token
        )
        if not need_poll:
            self._repo.save_session(user_id, bot_id, owner_id, session_key)
        return {
            "session_key": session_key,
            "is_new": False,
            "connection": connection,
            **({"need_poll": True} if need_poll else {}),
        }

    async def delete_owned_chat_session(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
        session_key: str,
    ) -> bool:
        """Delete one authorized multi-session entry and its favorite marker."""
        bot = self._get_authorized_chat_bot(user_id, bot_id, owner_id)
        # 以下为安全注释COSEC：禁止仅凭前端提供的 session_key 删除会话。
        owned = self._repo.get_owned_session(user_id, bot_id, owner_id, session_key)
        if not owned:
            raise BotNotFoundError("Session不存在或不属于当前用户")

        connection, need_poll = await self._prepare_chat_connection(
            bot, user_id, owner_id, None
        )
        if need_poll or connection is None:
            raise ConnectionError(
                "Bot服务正在启动，请稍后重试",
                error_code="5001",
            )

        # Keep the ownership row until runtime deletion is confirmed. Otherwise
        # a transient Adapter failure would make the session impossible to retry.
        await self._delete_adapter_session(
            bot, session_key, user_id, connection=connection
        )
        await self._remove_session_favorite(connection, session_key, user_id)
        self._repo.delete_owned_session(user_id, bot_id, owner_id, session_key)

        if self._repo.get_session(user_id, bot_id, owner_id) == session_key:
            remaining = self._repo.list_owned_sessions(user_id, bot_id, owner_id)
            if remaining:
                self._repo.save_session(
                    user_id,
                    bot_id,
                    owner_id,
                    remaining[0]["session_key"],
                )
            else:
                self._repo.delete_session(user_id, bot_id, owner_id)
        return True

    async def delete_chat_session(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> bool:
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

        # 3. 调用 Adapter 删除 session。运行时删除失败时保留新的 ownership，
        # 让新旧前端都能重试，而不是制造 Engine 孤儿会话。
        try:
            await self._delete_adapter_session(bot, session_key, user_id)
            logger.info(f"[ExpertChatService] Deleted adapter session: {session_key}")
        except Exception as e:
            logger.error(f"[ExpertChatService] Failed to delete adapter session {session_key}: {e}")
            raise

        # 4. 删除本地 session 映射
        self._repo.delete_owned_session(user_id, bot_id, owner_id, session_key)
        self._repo.delete_session(user_id, bot_id, owner_id)
        logger.info(
            "[ExpertChatService] Deleted local session mapping: user=%s, bot=%s, owner=%s",
            user_id,
            bot_id,
            owner_id,
        )

        return True

    # ============ Private Methods ============

    def _get_authorized_chat_bot(
        self, user_id: str, bot_id: str, owner_id: str
    ) -> Dict[str, Any]:
        """Resolve and authorize a Bot before any container-side operation."""
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot不存在: {bot_id}")
        if bot.get("status") != "ACTIVE":
            raise BotNotActiveError(f"Bot未激活: {bot_id}")

        # 以下为安全注释COSEC：互动列表归属和 Bot 访问权均使用服务端登录身份校验。
        bot_in_list = any(
            entry["bot_id"] == bot_id and entry["owner_id"] == owner_id
            for entry in self._repo.list_chat_bots(user_id)
        )
        if not bot_in_list:
            raise BotNotFoundError(f"Bot不在对话列表中: {bot_id}")
        self._check_chat_access(bot, user_id)
        return bot

    async def _prepare_chat_connection(
        self,
        bot: Dict[str, Any],
        user_id: str,
        owner_id: str,
        iam_token: Optional[str],
    ) -> tuple[Optional[Dict[str, Any]], bool]:
        """Resolve caller/owner binding and return Adapter connection data."""
        bot_id = bot["bot_id"]
        bot_call_type = McpCallType.parse(bot.get("call_type") or None)
        logger.info(
            "[ExpertChatService] Bot call_type: bot=%s, call_type=%s, parsed=%s",
            bot_id,
            bot.get("call_type"),
            bot_call_type,
        )
        if bot_call_type == McpCallType.CALLER:
            result = await self._instance_service.get_caller_connection(
                user_id=user_id,
                bot_id=bot_id,
                owner_id=owner_id,
                iam_token=iam_token,
            )
            if result.get("need_poll", False):
                logger.info(
                    "[ExpertChatService] Caller instance not ready: bot=%s, user=%s",
                    bot_id,
                    user_id,
                )
                return result.get("connection"), True
            instance = result.get("instance", {})
            ext = instance.get("ext") or {}
            bot["binding_id"] = ext.get("binding_id")
        elif bot.get("bot_type", "personal") == "service":
            from agentclaw.community.core.service_bot.repository.models import (
                PublishStatus,
            )

            bot["binding_id"] = self._baas_service.get_bind_id(
                bot_id=bot_id,
                owner_id=owner_id,
                bot_type="service",
                publish_status=PublishStatus.SUCCESS.value,
            )

        return self._get_connection(bot, user_id), False

    async def _list_owned_adapter_sessions(
        self,
        connection: Dict[str, Any],
        rows: List[Dict[str, Any]],
        user_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Batch-read Adapter metadata until every owned session is resolved.

        The Adapter may host sessions for multiple Bots or legacy keys that do
        not encode an ``agent_id``. We therefore omit the agent filter and make
        the Backend ownership index the final authorization boundary.
        """
        remaining_keys = {row["session_key"] for row in rows}
        matched: Dict[str, Dict[str, Any]] = {}
        engine_type = connection.get("engine_type", "openclaw")
        if engine_type in _EXACT_SESSION_LOOKUP_ENGINES:
            await self._lookup_remaining_adapter_sessions(
                connection=connection,
                remaining_keys=remaining_keys,
                matched=matched,
            )
            return matched

        seen_adapter_ids: set[str] = set()
        offset = 0
        list_unavailable = False

        while remaining_keys:
            try:
                response = await self._transport.invoke(
                    connection,
                    "GET",
                    "/api/sessions",
                    params={
                        "user_id": user_id,
                        "limit": _ADAPTER_SESSION_PAGE_SIZE,
                        "offset": offset,
                    },
                )
            except Exception as error:
                list_unavailable = True
                logger.warning(
                    "[ExpertChatService] Adapter session list unavailable; "
                    "unresolved sessions will use placeholders: offset=%s error=%s",
                    offset,
                    error,
                )
                break

            data = response.get("data")
            if not isinstance(data, list) or not data:
                break

            page_ids: set[str] = set()
            for raw_item in data:
                if not isinstance(raw_item, dict):
                    continue
                session_id = raw_item.get("id")
                if not isinstance(session_id, str) or not session_id:
                    continue
                page_ids.add(session_id)
                # 以下为安全注释COSEC：Adapter 结果必须与已授权的 Backend ownership 取交集。
                if session_id in remaining_keys:
                    matched[session_id] = raw_item
                    remaining_keys.remove(session_id)

            unseen_ids = page_ids - seen_adapter_ids
            if not unseen_ids:
                logger.warning(
                    "[ExpertChatService] Adapter session pagination made no progress; "
                    "stopping at offset=%s",
                    offset,
                )
                break
            seen_adapter_ids.update(unseen_ids)
            offset += len(data)

        if remaining_keys and not list_unavailable:
            await self._lookup_remaining_adapter_sessions(
                connection=connection,
                remaining_keys=remaining_keys,
                matched=matched,
            )

        return matched

    async def _lookup_remaining_adapter_sessions(
        self,
        connection: Dict[str, Any],
        remaining_keys: set[str],
        matched: Dict[str, Dict[str, Any]],
    ) -> None:
        """Resolve keys hidden by engines that filter after pagination."""
        for session_key in sorted(remaining_keys):
            encoded_session_key = quote(session_key, safe="")
            try:
                response = await self._transport.invoke(
                    connection,
                    "GET",
                    f"/api/sessions/{encoded_session_key}",
                )
            except Exception as error:
                if self._is_adapter_not_found(error):
                    continue
                logger.warning(
                    "[ExpertChatService] Exact session lookup unavailable; "
                    "remaining sessions will use placeholders: session=%s error=%s",
                    session_key,
                    error,
                )
                break

            item = response.get("data")
            # The session key comes from the authorized Backend index, and the
            # Adapter response must still match it exactly before enrichment.
            if isinstance(item, dict) and item.get("id") == session_key:
                matched[session_key] = item

    @staticmethod
    def _placeholder_session(row: Dict[str, Any]) -> Dict[str, Any]:
        """Build the complete list shape for an empty or unavailable session."""
        return {
            "id": row["session_key"],
            "title": "新会话",
            "user_id": row["user_id"],
            "agent_id": row["bot_id"],
            "model": None,
            "permission_mode": None,
            "cwd": None,
            "gmt_created": row.get("gmt_create") or "",
            "gmt_modified": row.get("gmt_modified") or "",
            "message_count": 0,
            "last_message": None,
        }

    @staticmethod
    def _session_sort_key(item: Dict[str, Any]) -> float:
        value = item.get("gmt_modified") or item.get("gmt_created")
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0

    async def _remove_session_favorite(
        self,
        connection: Dict[str, Any],
        session_key: str,
        user_id: str,
    ) -> None:
        """Best-effort favorite cleanup for engines whose delete is deferred."""
        encoded_session_key = quote(session_key, safe="")
        try:
            await self._transport.invoke(
                connection,
                "DELETE",
                f"/api/session-favorites/{encoded_session_key}",
                params={"user_id": user_id},
            )
        except Exception as error:
            logger.warning(
                "[ExpertChatService] Favorite cleanup skipped: session=%s error=%s",
                session_key,
                error,
            )

    async def _list_favorite_sessions(
        self,
        connection: Dict[str, Any],
        user_id: str,
        bot_id: str,
    ) -> List[Dict[str, Any]]:
        """Read existing Adapter favorites; old Adapters degrade to an empty tab."""
        try:
            response = await self._transport.invoke(
                connection,
                "GET",
                "/api/session-favorites",
                params={
                    "user_id": user_id,
                    "limit": 10_000,
                    "offset": 0,
                },
            )
            data = response.get("data")
            return data if isinstance(data, list) else []
        except Exception as error:
            logger.warning(
                "[ExpertChatService] Favorite list unavailable: bot=%s error=%s",
                bot_id,
                error,
            )
            return []

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

        # 1. 权限校验 — Defense-in-depth: primary check in get_chat_session
        #    保持此检查作为安全网，确保其他调用路径（如 refresh_connection）也有权限保护
        self._check_chat_access(bot, user_id)

        # 2. 走 resolver (全仓唯一 provider 解析点,替代 v2)
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
                "[ExpertChatService] Got proxy connection: bot=%s, provider=%s, sandbox_id=%s",
                bot_id,
                ctx.provider,
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

    async def _create_session(
        self,
        bot: Dict[str, Any],
        user_id: str,
        connection: Optional[Dict[str, Any]] = None,
        prefer_adapter_for_relay: bool = False,
    ) -> str:
        """调用 Adapter 创建 session

        Args:
            bot: Bot 信息字典
            user_id: 用户ID

        Returns:
            session_key
        """
        conn = connection or self._get_connection(bot, user_id)
        engine_type = conn.get("engine_type", "openclaw")

        # Preserve the legacy singular /session behavior. Only the new plural
        # /sessions flow opts into persisted, canonical relay session keys.
        if (
            engine_type in _LEGACY_LOCAL_SESSION_ENGINES
            and not prefer_adapter_for_relay
        ):
            return await self._create_aicoding_session(conn, bot, user_id)

        try:
            return await self._create_openclaw_session(conn, bot, user_id)
        except Exception as error:
            if (
                engine_type in _LEGACY_LOCAL_SESSION_ENGINES
                and self._is_adapter_endpoint_unsupported(error)
            ):
                logger.warning(
                    "[ExpertChatService] Adapter session creation unsupported; "
                    "using legacy local key: engine=%s bot=%s",
                    engine_type,
                    bot.get("bot_id"),
                )
                return await self._create_aicoding_session(conn, bot, user_id)
            raise

    async def _create_aicoding_session(
        self, conn: Dict[str, Any], bot: Dict[str, Any], user_id: str
    ) -> str:
        """
        AI Coding 引擎：teamclaw-aicoding-relay 按需建 session，本地生成 key，不调 Adapter。
        """
        import uuid

        session_key = f"session:{uuid.uuid4()}:user:{user_id}"
        logger.info(f"[aicoding] local session key generated: {session_key}")
        return session_key

    async def _create_openclaw_session(
        self, conn: Dict[str, Any], bot: Dict[str, Any], user_id: str
    ) -> str:
        """Create a session through the active Adapter and return its full ID."""
        payload = {
            "title": f"Chat with {bot.get('bot_name', bot['bot_id'])}",
            "user_id": user_id,
            "agent_id": bot["bot_id"],
            "engine": conn.get("engine_type", "openclaw"),
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
            create_timeout = (
                _RELAY_SESSION_CREATE_TIMEOUT_SECONDS
                if conn.get("engine_type") in _LEGACY_LOCAL_SESSION_ENGINES
                else None
            )
            invoke_kwargs: Dict[str, Any] = {"body": payload}
            if create_timeout is not None:
                invoke_kwargs["timeout"] = create_timeout
            data = await self._transport.invoke(
                conn,
                "POST",
                "/api/sessions",
                **invoke_kwargs,
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

    async def _check_session_exists(
        self,
        bot: Dict[str, Any],
        session_key: str,
        user_id: Optional[str] = None,
        connection: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        调用 Adapter 校验 session 是否还存在。
        AI Coding 引擎：teamclaw-aicoding-relay 按需创建 session，始终返回 True。
        """
        conn = connection or self._get_connection(bot, user_id)

        if conn.get("engine_type") == "aicoding":
            return True
        if conn.get("engine_type") == "claude_code":
            logger.info(
                "[ExpertChatService] claude_code session check: skipping "
                "adapter pre-check for session=%s",
                session_key,
            )
            return True

        try:
            encoded_session_key = quote(session_key, safe="")
            await self._transport.invoke(
                conn, "GET", f"/api/sessions/{encoded_session_key}"
            )
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
        connection: Optional[Dict[str, Any]] = None,
    ) -> None:
        """调用 Adapter 删除 session"""
        conn = connection or self._get_connection(bot, user_id)
        engine_type = conn.get("engine_type", "openclaw")

        logger.info(f"[ExpertChatService] Deleting adapter session via transport: session={session_key}")

        try:
            encoded_session_key = quote(session_key, safe="")
            await self._transport.invoke(
                conn, "DELETE", f"/api/sessions/{encoded_session_key}"
            )
            logger.info(f"[ExpertChatService] Successfully deleted adapter session: {session_key}")
        except Exception as e:
            if self._is_adapter_not_found(e):
                if self._is_ambiguous_adapter_delete_not_found(e):
                    # Engine's generic DELETE endpoint uses this 404 for both a
                    # missing session and an operational deletion failure. The
                    # Backend ownership index must remain retryable on ambiguity.
                    logger.error(
                        "[ExpertChatService] Adapter returned ambiguous delete "
                        "result; preserving ownership: session=%s",
                        session_key,
                    )
                    raise
                logger.warning(f"[ExpertChatService] Session not found in adapter: {session_key}")
                return
            if (
                engine_type in _LEGACY_LOCAL_SESSION_ENGINES
                and self._is_adapter_endpoint_unsupported(e)
            ):
                logger.warning(
                    "[ExpertChatService] Adapter session deletion unsupported; "
                    "keeping legacy metadata-only behavior: engine=%s session=%s",
                    engine_type,
                    session_key,
                )
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

    @staticmethod
    def _is_adapter_endpoint_unsupported(error: Exception) -> bool:
        if isinstance(error, DeviceAdapterEndpointNotFoundError):
            return True
        if isinstance(error, DeviceAdapterHTTPStatusError):
            return error.status_code in {404, 405}
        error_msg = (
            error.original_error
            if isinstance(error, SessionCreateError) and error.original_error
            else str(error)
        )
        return "Adapter returned" in error_msg and (
            "404" in error_msg or "405" in error_msg
        )
