"""Bot public service implementation.

纯业务逻辑层，不包含任何 HTTP 关注点。
"""
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from agentclaw.community.plugin_api.approval_workflow import ApprovalWorkflowPlugin
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.utils.avernet_tenant import (
    bind_current_avernet_tenant,
    get_current_avernet_tenant,
)
from agentclaw.community.core.repository.protocols.bot import BotFriendRepositoryProtocol
from agentclaw.community.core.bot_public.repository.models import BotFriendQueryKey, BotFriendStatus, ApprovalStatus, ApprovalType
from agentclaw.community.core.operator_context import OperatorContext
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    # Type-only: runtime ``from agentclaw.community.di.modules.skill_center_module
    # import SkillSetServiceFactory`` would form a cycle (di/__init__ ->
    # container -> bot_public_module -> bot_public_service).
    # BotPublicService is @provider-constructed (bot_public_module), so
    # this annotation is never resolved at runtime.
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    # ``DeviceSyncDispatcher`` 类源头在 plugins/prod/,但 core/ 不得依赖
    # plugins/(arch lint)。镜像 ``ChannelService`` 借 ``DeviceFilesystemDispatcher``
    # 的解法:类型注解走 di/modules/ 转出口(di 是合法的 composition 层)。
    from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.bot_publish_approval import (
    BotPublishApprovalPlugin,
    BotPublishCallbacks,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()


def _resolve_access_mode(public: str, friend_approval: str) -> str:
    """Map bot visibility to passport accessMode."""
    if public == "1" and friend_approval == "0":
        return "OPEN"
    return "RESTRICTED"


class BotNotPublicError(Exception):
    """Exception raised when bot is not public."""
    def __init__(self, bot_id: str, message: str = "Bot is not public"):
        self.bot_id = bot_id
        self.message = f"{message}: bot_id={bot_id}"
        super().__init__(self.message)


class BotPublicServiceError(Exception):
    """Bot public service error."""
    pass


class BotNotFoundError(BotPublicServiceError):
    """Bot not found error."""
    pass


class BotPermissionError(BotPublicServiceError):
    """Bot permission error - user is not the owner."""
    pass


class BotPublicService:
    """Bot public service for managing friend request approval flow.

    通过依赖注入接收 bot_friend_repo、bot_repository、process_service、bot_service。
    """

    def __init__(
        self,
        bot_friend_repo: BotFriendRepositoryProtocol,
        bot_repository: BotRepository,
        process_service: ApprovalWorkflowPlugin,
        bot_service: BotService,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        publish_approval_plugin: BotPublishApprovalPlugin,
        skill_set_service_factory: "SkillSetServiceFactory",
        device_context_resolver: DeviceContextResolver,
        device_sync_dispatcher: "DeviceSyncDispatcher",
    ) -> None:
        self._bot_friend_repo = bot_friend_repo
        self._bot_repository = bot_repository
        self._process_service = process_service
        self._bot_service = bot_service
        self._passport_plugin = passport_plugin
        self._auth_relationship_plugin = auth_relationship_plugin
        self._publish_approval_plugin = publish_approval_plugin
        self._skill_set_service_factory = skill_set_service_factory
        # Task 2.1: resolver + dispatcher 替代 device_sync_supplier 在
        # sync_bot_config_to_device 路径上的角色。Task 6 收口后 supplier 已删。
        self._resolver = device_context_resolver
        self._device_sync_dispatcher = device_sync_dispatcher
        self._sync_lock = threading.Lock()
        self._syncing_bots: set[str] = set()
        self._pending_syncs: dict[str, tuple[str, str]] = {}  # sync_key -> (access_mode, public)

    def _sync_access_mode_and_relations(
        self,
        bot_id: str,
        owner_id: str,
        access_mode: str,
        public: str,
    ) -> None:
        """在后台同步 passport accessMode 并回收/重建授权关系。

        当 Bot 公开状态变化时调用，负责两件事：
        1. 更新 passport 的 accessMode（OPEN / RESTRICTED），控制外部访问策略。
        2. 根据新的访问模式回收和重建授权关系：
           - OPEN（公开）：删除所有授权关系（公开 Bot 不需要显式授权）。
           - RESTRICTED + public="0"（私人）：只保留 owner，删除其他人的授权关系。
           - RESTRICTED + public="1"（受限访问）：保留 owner + 所有在 bot_friend 表中
             人工审批通过的用户（通过 ext["approvals"] 中 approval_type="MANUAL" 且
             status="APPROVED" 来识别）。

        普通发布流程保留后台执行语义；失败会被线程入口记录。审批回调必须改用
        ``_sync_access_mode_and_relations_or_raise``，同步等待并传播失败。
        """
        # Tenant-scope the coalescing key: (owner_id, bot_id) is unique only
        # WITHIN a tenant, but _syncing_bots / _pending_syncs are process-wide.
        # Without the tenant prefix, a second tenant's sync for a colliding
        # (owner_id, bot_id) would be queued under the first tenant's key and
        # applied to the first tenant's bot by its (tenant-bound) thread, while
        # its own bot is never synced. The key is built in the request thread,
        # so get_current_avernet_tenant() is the request's tenant.
        sync_key = f"{get_current_avernet_tenant()}:{owner_id}:{bot_id}"
        with self._sync_lock:
            if sync_key in self._syncing_bots:
                # 当前已有同步任务在执行，把最新参数存入 pending。
                # 覆盖写保证 pending 里永远是最后一条请求的参数，
                # 避免中间状态的请求被累积执行。
                self._pending_syncs[sync_key] = (access_mode, public)
                logger.info(f"[_sync_access_mode_and_relations] Sync queued: {sync_key}")
                return
            self._syncing_bots.add(sync_key)

        def _do_sync():
            try:
                has_work = True
                while has_work:
                    # pending 覆盖当前参数，保证执行的是最后一次请求的参数
                    current_access_mode, current_public = access_mode, public
                    pending = self._pending_syncs.pop(sync_key, None)
                    if pending:
                        current_access_mode, current_public = pending
                        logger.info(f"[_sync_access_mode_and_relations] Consume pending: sync_key={sync_key}, access_mode={current_access_mode}, public={current_public}")

                    logger.info(f"[_sync_access_mode_and_relations] Start sync: sync_key={sync_key}, access_mode={current_access_mode}, public={current_public}")

                    self._sync_access_mode_and_relations_or_raise(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        access_mode=current_access_mode,
                        public=current_public,
                    )

                    with self._sync_lock:
                        has_work = sync_key in self._pending_syncs
                        if not has_work:
                            self._syncing_bots.discard(sync_key)
                logger.info(f"[_sync_access_mode_and_relations] Sync finished: sync_key={sync_key}")
            except Exception:
                with self._sync_lock:
                    self._syncing_bots.discard(sync_key)
                logger.exception(
                    f"[_sync_access_mode_and_relations] Sync thread crashed: "
                    f"sync_key={sync_key}, bot_id={bot_id}, owner_id={owner_id}, "
                    f"access_mode={access_mode}, public={public}"
                )

        # Bare threads don't copy context vars; carry the request tenant so the
        # BotRepository reads inside _do_sync stay tenant-scoped.
        thread = threading.Thread(
            target=bind_current_avernet_tenant(_do_sync), daemon=False
        )
        thread.start()
        logger.info(f"[_sync_access_mode_and_relations] Started background thread: bot_id={bot_id}, owner_id={owner_id}")

    def _sync_access_mode_and_relations_or_raise(
        self,
        bot_id: str,
        owner_id: str,
        access_mode: str,
        public: str,
    ) -> None:
        """同步 accessMode 与授权关系，任一步失败都传播给调用方。"""
        bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        from agentclaw.community.core.bot_management.utils import resolve_agent_code
        agent_code = resolve_agent_code(
            bot=bot,
            passport_plugin=self._passport_plugin,
        ) or None
        if not agent_code:
            raise BotPublicServiceError(
                f"agent_code 为空，无法重建授权关系: bot_id={bot_id}"
            )

        try:
            self._passport_plugin.update_passport(
                bot_id=bot_id,
                user_id=owner_id,
                bot_name=bot.get("bot_name"),
                bot_desc=bot.get("bot_desc"),
                engine_type=bot.get("active_engine") or DEFAULT_ENGINE_TYPE,
                access_mode=access_mode,
            )
        except Exception as exc:
            raise BotPublicServiceError(
                f"更新 passport accessMode 失败: {exc}"
            ) from exc

        logger.info(
            "[_sync_access_mode_and_relations_or_raise] Updated passport "
            "accessMode: bot_id=%s, owner_id=%s, access_mode=%s",
            bot_id,
            owner_id,
            access_mode,
        )
        self._rebuild_auth_relationships(
            bot_id=bot_id,
            owner_id=owner_id,
            owner_name=bot.get("owner_name"),
            agent_code=agent_code,
            access_mode=access_mode,
            public=public,
        )

    def _rebuild_auth_relationships(
        self,
        bot_id: str,
        owner_id: str,
        owner_name: str,
        agent_code: str,
        access_mode: str,
        public: str,
    ) -> None:
        """根据 Bot 访问模式回收和重建授权关系，失败直接传播。

        策略：循环查完所有现有关系，全部删除，再重新创建需要的。
        """
        plugin = self._auth_relationship_plugin

        if access_mode == "OPEN":
            # OPEN 模式：直接删除所有授权关系
            deleted = plugin.delete_relationships_by_agent(agent_code)
            logger.info(f"[_rebuild_auth_relationships] OPEN mode: deleted {deleted} auth relationships for bot_id={bot_id}, owner_id={owner_id}")
            return

        # RESTRICTED 模式：先收集保留名单，再全删重建
        keep_work_nos = {owner_id}
        logger.info(f"[_rebuild_auth_relationships] RESTRICTED mode: bot_id={bot_id}, owner_id={owner_id}, public={public}")

        if public == "1":
            # 收集人工审批通过的用户
            friends = self._bot_friend_repo.list_approved_friends_for_bot(
                bot_id=bot_id,
                owner_id=owner_id,
            )
            for friend in friends:
                requester_id = friend.get("requester_entity_id")
                if requester_id:
                    keep_work_nos.add(requester_id)
            logger.info(f"[_rebuild_auth_relationships] RESTRICTED public=1: keeping {len(keep_work_nos)} users for bot_id={bot_id}, owner_id={owner_id}")

        # 步骤1: 删除所有现有授权关系
        deleted = plugin.delete_relationships_by_agent(agent_code)
        logger.info(f"[_rebuild_auth_relationships] Deleted {deleted} auth relationships for bot_id={bot_id}, owner_id={owner_id}")

        # 步骤2: 重新创建需要的授权关系
        # 限制并发数，避免对授权关系服务造成冲击
        def _create_one(work_no: str) -> None:
            is_owner = work_no == owner_id
            result = plugin.create_relationship(
                work_no=work_no,
                agent_code=agent_code,
                description="Bot owner authorization by TeamClaw" if is_owner else "Authorized by TeamClaw via friend request approval",
                operator_work_no=owner_id,
                operator_name=owner_name,
            )
            if result:
                if result.get("already_exists"):
                    logger.info(f"[_rebuild_auth_relationships] Relationship already exists, skipped: bot_id={bot_id}, owner_id={owner_id}, work_no={work_no}")
                else:
                    logger.info(f"[_rebuild_auth_relationships] Created auth relationship: bot_id={bot_id}, owner_id={owner_id}, work_no={work_no}, auth_id={result.get('auth_id')}")
            else:
                raise BotPublicServiceError(
                    "授权关系服务返回失败: "
                    f"bot_id={bot_id}, owner_id={owner_id}, work_no={work_no}"
                )

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(_create_one, keep_work_nos))

        logger.info(f"[_rebuild_auth_relationships] Rebuild done: bot_id={bot_id}, owner_id={owner_id}, total={len(keep_work_nos)}")

    def public_bot(self, bot_id: str, owner_id: str, public: str, permission_owner: str, friend_approval: str, operator: OperatorContext) -> Dict[str, Any]:
        """
        公开/取消公开 Bot。

        根据 permission_owner 决定是否需要审批：
        - permission_owner="owner": 需要发起审批，审批信息保存在 ext 中，等待回调后更新 public 字段
        - permission_owner="caller": 直接更新 public 字段

        Args:
            bot_id: Bot ID
            owner_id: Owner ID（用于权限检查）
            public: 公开标志位 "0"=私有, "1"=公开
            permission_owner: 权限所有者 "caller"=直接更新, "owner"=需要审批
            friend_approval: 好友审批标志位 "0"=不需要审批, "1"=需要审批
            operator: 操作者上下文

        Returns:
            包含操作结果的字典
            - 直接更新: {"success": True, "action": "direct_update", "public": "0|1"}
            - 需要审批: {"success": True, "action": "approval_created", "puid": "xxx", "approval_url": "xxx"}

        Raises:
            BotNotFoundError: 如果 bot 不存在
            BotPermissionError: 如果用户不是 owner
            BotServiceError: 如果操作失败
        """
        if not owner_id:
            raise BotPublicServiceError("Owner ID is required")
        if public not in ("0", "1"):
            raise BotPublicServiceError(f"Invalid public value: {public}, must be '0' or '1'")
        if permission_owner not in ("caller", "owner"):
            raise BotPublicServiceError(f"Invalid permission_owner: {permission_owner}, must be 'caller' or 'owner'")
        bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        operator_id = operator.staff_id if operator else owner_id
        ext = bot.get("ext") or {}

        # Compute access mode and update passport
        access_mode = _resolve_access_mode(public, friend_approval)

        if public == "0":
            return self._publish_directly(
                bot=bot, ext=ext, bot_id=bot_id, owner_id=owner_id,
                operator_id=operator_id, public="0",
                permission_owner=None, friend_approval=friend_approval,
                access_mode=access_mode,
                log_tag="unpublish",
            )

        # Skip the approval workflow when the owner is the same as the
        # caller — by-design, no third-party approval needed.
        if permission_owner == "caller":
            return self._publish_directly(
                bot=bot, ext=ext, bot_id=bot_id, owner_id=owner_id,
                operator_id=operator_id, public=public,
                permission_owner=permission_owner,
                friend_approval=friend_approval, access_mode=access_mode,
                log_tag="caller permission",
            )

        # permission_owner == "owner": delegate the approval flow to the
        # injected strategy plugin. Local impl publishes directly (no
        # antprocess); prod impl starts the antprocess workflow and the
        # bot stays in PENDING until the callback fires. Rule 14 — mode
        # never reaches this service.
        return self._publish_approval_plugin.publish(
            bot=bot, ext=ext, bot_id=bot_id, owner_id=owner_id,
            operator_id=operator_id, operator=operator,
            public=public, permission_owner=permission_owner,
            friend_approval=friend_approval, access_mode=access_mode,
            callbacks=self._publish_callbacks(),
        )

    def _publish_callbacks(self) -> BotPublishCallbacks:
        """Bind the publish-approval plugin's callback bag to this
        service's bound methods.

        TODO(architecture): see ``plugins/bot_publish_approval.py``'s
        module docstring for the long-term plan to remove this.
        """
        return BotPublishCallbacks(
            publish_directly=self._publish_directly,
            archive_approval=self._archive_public_approval,
            update_with_notification=self._update_bot_with_notification,
            handle_approval_callback=self.handle_public_approval_callback,
            refetch_bot=self._bot_repository.get_by_id_and_owner,
            build_approval_context=self._build_public_approval_context,
        )

    def _publish_directly(
        self,
        *,
        bot: Dict[str, Any],
        ext: Dict[str, Any],
        bot_id: str,
        owner_id: str,
        operator_id: str,
        public: str,
        permission_owner: Optional[str],
        friend_approval: str,
        access_mode: str,
        log_tag: str = "direct publish",
    ) -> Dict[str, Any]:
        """Persist the bot's new public/permission state without going
        through an approval workflow.

        Shared by three call sites (un-publish, caller-permission
        publish, and the local strategy's owner-permission fallback).
        ``permission_owner=None`` means "drop the ext field" (the
        un-publish case); otherwise it's set on ext.
        """
        self._archive_public_approval(ext, bot_id)
        if permission_owner is None:
            ext.pop("permission_owner", None)
        else:
            ext["permission_owner"] = permission_owner
        ext["friend_approval"] = friend_approval
        update_data = {"public": public, "modifier_id": operator_id, "ext": ext}
        updated_bot = self._update_bot_with_notification(
            bot_id, owner_id, update_data, bot, notify_device=True,
        )
        if not updated_bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        self._sync_access_mode_and_relations(
            bot_id, owner_id, access_mode, public,
        )
        logger.info(
            "[bot_service.public_bot] %s: bot_id=%s public=%s "
            "owner=%s operator=%s",
            log_tag, bot_id, public, owner_id, operator_id,
        )
        return updated_bot

    def _archive_public_approval(self, ext: Dict[str, Any], bot_id: str) -> None:
        if "public_approval" not in ext:
            return
        from datetime import datetime
        old_approval = ext.pop("public_approval")
        old_approval["processed_at"] = datetime.now().isoformat()
        history = ext.get("public_approval_history", [])
        history.append(old_approval)
        ext["public_approval_history"] = history[-5:]
        logger.info(f"[bot_service.public_bot] Moved existing approval to history: bot_id={bot_id}, old_puid={old_approval.get('puid')}")

    def _build_public_approval_context(self, bot: Dict[str, Any], operator: Optional[OperatorContext] = None) -> Dict[str, str]:
        """构建公开审批上下文信息。

        Args:
            bot: Bot 对象字典
            operator: 操作者上下文，用于 owner_name 为空时获取操作人名称

        Returns:
            包含 publishHint、botSkills、botMcps 的字典
        """
        owner_name = bot.get("owner_name") or ""
        if not owner_name and operator:
            owner_name = operator.nick_name or operator.operator_name or operator.staff_id or ""
        bot_name = bot.get("bot_name") or bot.get("bot_id") or ""
        owner_id = bot.get("owner_id") or ""
        bot_id = bot.get("bot_id") or ""
        entity_id = bot.get("entity_id") or ""
        # owner_id 就是 user_id
        user_id = owner_id
        publish_hint = f"\"{owner_name}\"同学正在发布\"{bot_name}\"bot至TeamClaw的Bot广场，审批通过后平台全体成员均可基于\"{owner_name}\"权限进行服务调用。在服务发布前，请您审核：该Agent是否涉及敏感数据处理或核心业务操作；如涉及，为避免造成由于权限扩散造成的不必要风险，请重新考虑是否发布。"
        # 获取 bot skills 并格式化为字符串
        bot_skills_str = ""
        try:
            skill_set_service = self._skill_set_service_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            skill_sets = skill_set_service.get_all_skill_sets_with_skills(user_id=user_id, bolt_id=bot_id)
            skill_parts = []
            for skill_set in skill_sets:
                # 过滤掉默认技能集
                if skill_set.get("is_default"):
                    continue
                is_active = skill_set.get("is_active", False)
                set_name = skill_set.get("name", "")
                skills = skill_set.get("skills", [])
                if skills:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    skill_parts.append(f"{set_name}{active_marker}:")
                    for s in skills:
                        name = s.get("name", "")
                        if not name:
                            continue
                        # 根据 is_public 决定拼接内容（兼容 bool 和 str）
                        is_public_val = s.get("is_public")
                        is_public = is_public_val in (True, "1", 1) if is_public_val is not None else False
                        if is_public:
                            # 公开：只显示 name
                            skill_parts.append(f"  - {name}")
                        else:
                            # 私有：显示 name 和 description
                            description = s.get("description", "")
                            skill_parts.append(f"  - {name}: {description}" if description else f"  - {name}")
            bot_skills_str = "\n".join(skill_parts) if skill_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get skill sets for bot {bot_id}: {e}")
            bot_skills_str = "获取失败"
        # 获取 bot mcps 并格式化为字符串
        bot_mcps_str = ""
        try:
            skill_set_service = self._skill_set_service_factory.create(
                entity_id=entity_id, bot_id=bot_id
            )
            mcp_sets = skill_set_service.get_all_skill_sets_with_mcps(user_id=user_id, bolt_id=bot_id)
            mcp_parts = []
            for mcp_set in mcp_sets:
                is_active = mcp_set.get("is_active", False)
                set_name = mcp_set.get("name", "")
                mcps = mcp_set.get("mcps", [])
                mcp_names = [m.get("name", "") for m in mcps if m.get("name")]
                if mcp_names:
                    active_marker = "[激活]" if is_active else "[未激活]"
                    mcp_parts.append(f"{set_name}{active_marker}:")
                    for name in mcp_names:
                        mcp_parts.append(f"  - {name}")
            bot_mcps_str = "\n".join(mcp_parts) if mcp_parts else "无"
        except Exception as e:
            logger.warning(f"[_build_public_approval_context] Failed to get mcp sets for bot {bot_id}: {e}")
            bot_mcps_str = "获取失败"
        return {"publishHint": publish_hint, "botSkills": bot_skills_str, "botMcps": bot_mcps_str}

    def handle_public_approval_callback(self, bot_id: str, owner_id: str, puid: str, last_operate: str) -> Dict[str, Any]:
        """
        处理 Bot 公开审批回调。

        根据审批结果更新 bot 的 public 字段。

        Args:
            bot_id: Bot ID (业务ID)
            owner_id: Owner ID
            puid: 审批流程 ID
            last_operate: 最后操作 "AGREE"=同意, "DISAGREE"=拒绝, "CANCEL"=撤销

        Returns:
            操作结果字典 {"success": bool, "public": str|None, "message": str}
        """
        bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            logger.error(f"[bot_service.handle_public_approval_callback] Bot not found: {bot_id}")
            return {"success": False, "public": None, "message": f"Bot not found: {bot_id}"}
        ext = bot.get("ext") or {}
        public_approval = ext.get("public_approval")
        # 如果 public_approval 为空或者 stored_puid != puid，重试最多4次，每次间隔1秒
        retry_count = 0
        max_retries = 1
        while retry_count < max_retries:
            # 检查是否满足条件：public_approval 不为空且 puid 匹配
            if public_approval:
                stored_puid = public_approval.get("puid")
                if stored_puid == puid:
                    break  # 条件满足，退出重试
                # public_approval 存在但 puid 不匹配
                logger.info(f"[bot_service.handle_public_approval_callback] PUID mismatch, retry {retry_count + 1}/{max_retries}: bot_id={bot_id}, stored={stored_puid}, received={puid}")
            else:
                # public_approval 为空
                logger.info(f"[bot_service.handle_public_approval_callback] public_approval is empty, retry {retry_count + 1}/{max_retries}: bot_id={bot_id}")

            time.sleep(1)
            # 重新获取 bot 数据
            bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
            if bot:
                ext = bot.get("ext") or {}
                public_approval = ext.get("public_approval")
            retry_count += 1
        # 最终检查
        if not public_approval:
            logger.error(f"[bot_service.handle_public_approval_callback] No public_approval info in ext after {max_retries} retries: bot_id={bot_id}")
            return {"success": False, "public": None, "message": "No approval info found"}
        stored_puid = public_approval.get("puid")
        if stored_puid != puid:
            logger.error(f"[bot_service.handle_public_approval_callback] PUID mismatch after {max_retries} retries: bot_id={bot_id}, stored={stored_puid}, received={puid}")
            return {"success": True, "public": None, "message": "PUID mismatch"}
        last_operate_upper = last_operate.upper() if last_operate else ""
        if last_operate_upper == "AGREE":
            public_value = public_approval.get("public", bot.get("public", "1"))
            friend_approval_val = public_approval.get('friend_approval', '0')
            public_approval["status"] = "AGREE"
            public_approval["processed_at"] = datetime.now().isoformat()
            ext["public_approval"] = public_approval
            ext["permission_owner"] = public_approval['permission_owner']
            ext["friend_approval"] = friend_approval_val
            update_data = {"public": public_value, "modifier_id": public_approval.get("applicant", owner_id), "ext": ext}
            updated_bot = self._update_bot_with_notification(bot_id, owner_id, update_data, bot, notify_device=True)
            if not updated_bot:
                return {"success": False, "public": None, "message": "Failed to update bot"}
            # Sync passport accessMode and auth relationships
            access_mode = _resolve_access_mode(public_value, friend_approval_val)
            self._sync_access_mode_and_relations_or_raise(
                bot_id, owner_id, access_mode, public_value,
            )
            logger.info(f"[bot_service.handle_public_approval_callback] Approval agreed: bot_id={bot_id}, public={public_value}")
            return {"success": True, "public": public_value, "message": "Public status updated"}
        elif last_operate_upper == "DISAGREE":
            ext = bot.get("ext") or {}
            public_approval["status"] = "DISAGREE"
            public_approval["processed_at"] = datetime.now().isoformat()
            ext["public_approval"] = public_approval
            self._update_bot_with_notification(bot_id, owner_id, {"ext": ext, "modifier_id": public_approval.get("applicant", owner_id)}, bot, notify_device=False)
            logger.info(f"[bot_service.handle_public_approval_callback] Approval DISAGREE: bot_id={bot_id}")
            return {"success": True, "public": None, "message": "Approval DISAGREE, public not changed"}
        elif last_operate_upper == "CANCEL":
            ext = bot.get("ext") or {}
            public_approval["status"] = "CANCEL"
            public_approval["processed_at"] = datetime.now().isoformat()
            ext["public_approval"] = public_approval
            self._update_bot_with_notification(bot_id, owner_id, {"ext": ext, "modifier_id": public_approval.get("applicant", owner_id)}, bot, notify_device=False)
            logger.info(f"[bot_service.handle_public_approval_callback] Approval CANCEL: bot_id={bot_id}")
            return {"success": True, "public": None, "message": "Approval CANCEL, public not changed"}
        else:
            logger.warning(f"[bot_service.handle_public_approval_callback] Unknown last_operate: {last_operate}")
            return {"success": False, "public": None, "message": f"Unknown operation: {last_operate}"}

    def _update_bot_with_notification(self, bot_id: str, owner_id: str, update_data: Dict[str, Any], bot: Dict[str, Any], notify_device: bool) -> Optional[Dict[str, Any]]:
        """更新 bot 并可选地发送通知（先通知，成功再更新）。

        Args:
            bot_id: Bot ID
            owner_id: Owner ID
            update_data: 要更新的数据字典
            bot: Bot 对象字典（用于通知上下文）
            notify_device: 是否同步配置到 device

        Returns:
            更新后的 bot 记录，如果失败返回 None
        """
        if notify_device:
            public = update_data.get("public") or bot.get("public") or ""
            permission_owner = (update_data.get("ext") or {}).get("permission_owner") or (bot.get("ext") or {}).get("permission_owner") or ""
            # Skip device sync in local mode: the engine's /api/bot/config endpoint writes
            # ROLE/VISIBILITY to a flat file that nothing on the engine side actually reads.
            # Locally it fails because /home/admin/.credentials doesn't exist on macOS.
            # Task 2.1: 走 resolver + dispatcher,binding/conn_info 由 resolver
            # 内部查 — caller 不再透传 binding_id / nick_name。
            # Mode never reaches this service — Rule 14.
            self.sync_bot_config_to_device(
                bot_id=bot_id,
                user_id=owner_id,
                public=public,
                permission_owner=permission_owner,
            )
            logger.info(f"[_update_bot_with_notification] Notification sent for bot {bot_id}, owner_id={owner_id}, public={public}, permission_owner={permission_owner}")

        return self._bot_repository.update_by_owner(bot_id, owner_id, update_data)

    def sync_bot_config_to_device(
        self,
        bot_id: str,
        user_id: str,
        public: str,
        permission_owner: Optional[str],
    ) -> Dict[str, Any]:
        """通过 BaaS/Arca/Teclaw 链路把 bot config 同步到容器。

        Task 2.1: 走 resolver + DeviceSyncDispatcher 收口,入参精简为
        ``(bot_id, user_id, public, permission_owner)`` — binding 由
        resolver 内部查,nick_name 死参兜底成 user_id(plugin 内不读)。

        Rule 14: this service is mode-blind. Resolver/dispatcher 替代
        旧 ``DeviceSyncPluginSupplier.for_binding``。无 binding /
        无可用 provider 时 resolver 抛 ``DeviceNotBoundError`` /
        ``UnknownProviderError``,我们捕获并转 ``{"success": False, ...}``
        — 与旧 ``DeviceSyncUnavailableError`` 的 wire shape 对齐。
        """
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
            UnknownProviderError,
        )
        try:
            ctx = self._resolver.resolve_for_bot(bot_id, user_id)
        except (DeviceNotBoundError, UnknownProviderError) as e:
            logger.warning(
                "[bot_service.sync_bot_config_to_device] bot=%s no syncable device: %s",
                bot_id, e,
            )
            return {"success": False, "message": str(e)}
        plugin = self._device_sync_dispatcher.dispatch(ctx)
        return plugin.sync_bot_config(
            bot_id=bot_id,
            binding_id=ctx.binding_id,
            public=public,
            permission_owner=permission_owner,
            user_id=user_id,
            nick_name=user_id,  # 死参兼容,plugin 内未读
        )

    def update_bot_ext(self, bot_id: str, user_id: str, ext_update: Dict[str, Any]) -> None:
        """局部更新 bot.ext 字段。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID（用于权限检查）
            ext_update: 要更新的 ext 字段内容
        """
        bot = self._bot_service.get_bot(bot_id, user_id)
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}
        ext.update(ext_update)
        self._bot_repository.update_by_owner(bot_id, user_id, {"ext": ext})
        logger.info(f"[bot_service.update_bot_ext] Updated ext for bot {bot_id}: {ext_update}")

    def create_friend_request_approval(
        self, bot_id: str, owner_id: str, operator_id: str, operator_name: str,
        approval_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create friend request approval for a public bot.

        Args:
            bot_id: Bot ID
            owner_id: Owner ID
            operator_id: Operator user ID (applicant)
            operator_name: Operator user name (nick name)
            approval_context: Optional additional context for approval

        Returns:
            Approval result with success, puid, approval_url, error_msg

        Raises:
            BotPublicServiceError: If bot not found
        """
        logger.info(f"[create_friend_request_approval] Starting: bot_id={bot_id}, owner_id={owner_id}, operator_id={operator_id}")
        bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotPublicServiceError(f"Bot not found: bot_id={bot_id}, owner_id={owner_id}")

        # 获取bot的ext中的friend_approval配置，默认为0（不需要审批）
        bot_ext = bot.get("ext") or {}
        friend_approval = bot_ext.get("friend_approval", "0")

        # 先查询是否已存在好友关系
        existing_record = self._bot_friend_repo.get_by_entity_ids(
            requester_entity_id=operator_id,
            target_entity_id=owner_id,
            target_bot_id=bot_id
        )

        # 构建好友申请数据
        friend_data = {
            "requester_entity_id": operator_id,
            "requester_name": operator_name,
            "target_entity_id": owner_id,
            "target_name": bot.get("bot_name"),
            "target_bot_id": bot_id,
            "target_owner_name": bot.get("owner_name"),
        }

        if friend_approval == "0":
            # 无需审批，直接创建或更新好友关系
            if existing_record:
                # 已存在记录，更新状态为 ACCEPTED（如果之前不是）
                record = existing_record
                if existing_record.get("status") != BotFriendStatus.ACCEPTED:
                    record = self._bot_friend_repo._update_status_and_ext(
                        existing_record["id"],
                        BotFriendStatus.ACCEPTED,
                        existing_record.get("ext") or {}
                    )
                    logger.info(f"[create_friend_request_approval] Updated existing record to accepted: record_id={record.get('id')}")
                else:
                    logger.info(f"[create_friend_request_approval] Already accepted: record_id={record.get('id')}")
            else:
                # 不存在，插入新记录
                friend_data["status"] = BotFriendStatus.ACCEPTED
                record = self._bot_friend_repo.insert(friend_data)
                logger.info(f"[create_friend_request_approval] Auto accepted: bot_id={bot_id}, operator_id={operator_id}, record_id={record.get('id')}")

            return {
                "success": True,
                "auto_accepted": True,
                "bot_friend_id": record.get("id"),
                "message": "Friend request auto accepted",
            }
        else:
            # 需要审批，创建或更新记录
            if existing_record:
                # 已存在记录，更新状态为 PENDING 并清空审批单
                bot_friend_id = existing_record["id"]
                ext = existing_record.get("ext") or {}
                ext["approvals"] = []
                record = self._bot_friend_repo._update_status_and_ext(
                    bot_friend_id,
                    BotFriendStatus.PENDING,
                    ext
                )
                bot_friend_id = record.get("id")
                logger.info(f"[create_friend_request_approval] Updated existing record to pending and cleared approvals: bot_friend_id={bot_friend_id}")
            else:
                # 不存在，插入新记录
                friend_data["status"] = BotFriendStatus.PENDING
                record = self._bot_friend_repo.insert(friend_data)
                bot_friend_id = record.get("id")
                logger.info(f"[create_friend_request_approval] Created pending record: bot_friend_id={bot_friend_id}")

            # 创建审批单
            approval_result = self._create_friend_request_approval(
                bot, operator_id, operator_name, bot_friend_id, approval_context
            )

            if approval_result.get("success"):
                # 将审批信息存入BotFriend的ext属性
                approval_info = {
                    "uuid": approval_result.get("puid"),
                    "approval_type": ApprovalType.MANUAL,
                    "require_approval": True,
                    "approval_url": approval_result.get("approval_url"),
                    "status": ApprovalStatus.PENDING,
                }
                self._bot_friend_repo.create_approval(bot_friend_id, approval_info)
                logger.info(f"[create_friend_request_approval] Approval created: bot_friend_id={bot_friend_id}, puid={approval_result.get('puid')}")
            else:
                # 审批创建失败，记录错误日志
                logger.error(f"[create_friend_request_approval] Approval creation failed: bot_friend_id={bot_friend_id}, error={approval_result.get('error_msg')}")

            approval_result["bot_friend_id"] = bot_friend_id
            return approval_result

    def _create_friend_request_approval(
        self, bot: Dict[str, Any], operator_id: str, operator_name: str,
        bot_friend_id: int,
        approval_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create friend request approval for a public bot.

        Args:
            bot: Bot object with 'id', 'public', 'owner_id' fields
            operator_id: Operator user ID (applicant)
            operator_name: Operator user name (nick name)
            bot_friend_id: Bot friend record ID
            approval_context: Optional additional context for approval

        Returns:
            Approval result with success, puid, approval_url, error_msg

        Raises:
            BotNotPublicError: If bot is not public
            BotPublicServiceError: If failed to create approval
        """
        bot_id = bot.get("bot_id", "unknown")
        owner_id = bot.get("owner_id")
        logger.info(f"[_create_friend_request_approval] Starting: bot_id={bot_id}, owner_id={owner_id}, operator_id={operator_id}, bot_friend_id={bot_friend_id}")

        # Check if bot is public
        if bot.get("public", "0") != "1":
            logger.warning(f"[_create_friend_request_approval] Bot is not public: bot_id={bot_id}, owner_id={owner_id}")
            raise BotNotPublicError(bot_id=bot_id)

        # Get required fields from bot
        bot_pk = bot.get("id")
        if not bot_pk:
            raise BotPublicServiceError(f"Bot missing required field 'id': bot_id={bot_id}, owner_id={owner_id}")
        if not owner_id:
            raise BotPublicServiceError(f"Bot missing required field 'owner_id': bot_id={bot_id}")

        # Build approval context and create approval
        owner_name = bot.get("owner_name") or owner_id
        bot_name = bot.get("bot_name") or bot_id
        bot_desc = bot.get("bot_desc")
        bot_name_with_desc = f"{bot_name}:{bot_desc}" if bot_desc else bot_name
        context = {
            "bot_friend_id": str(bot_friend_id),
            "bot_public_owner_audit": f"w[{owner_id}]",
            "owner_name": owner_name,
            "bot_name": bot_name_with_desc,
            "operator_name": operator_name or operator_id,
        }
        if approval_context:
            context.update(approval_context)

        try:
            approval_result = self._process_service.start_approval(
                applicant=operator_id,
                biz_id=f"{str(bot_friend_id)}{datetime.now().strftime('%Y%m%d%H%M%S')}",
                biz_type="botpublicfriends",
                process_code="teamclaw_bot_public_friends",
                context=context,
            )
            logger.info(f"[_create_friend_request_approval] Completed: bot_id={bot_id}, owner_id={owner_id}, success={approval_result.get('success')}")
            return approval_result
        except Exception as e:
            logger.error(f"[_create_friend_request_approval] Failed: {e}", exc_info=True)
            raise BotPublicServiceError(f"Failed to create approval: {e}")

    def handle_friend_request_approval_callback(
        self, puid: str, last_operate: str, bot_friend_id: int,
    ) -> Dict[str, Any]:
        """Handle friend request approval callback.

        Args:
            puid: Process unique ID
            last_operate: Last operation (AGREE/DISAGREE/CANCEL)
            bot_friend_id: Bot friend record ID

        Returns:
            Callback result with success and message
        """
        logger.info(f"[handle_friend_request_approval_callback] Received: puid={puid}, last_operate={last_operate}, bot_friend_id={bot_friend_id}")

        # 查询BotFriend记录
        friend_record = self._bot_friend_repo.get_by_id(bot_friend_id)
        if not friend_record:
            logger.error(f"[handle_friend_request_approval_callback] BotFriend record not found: bot_friend_id={bot_friend_id}")
            return {"success": False, "message": f"BotFriend record not found: bot_friend_id={bot_friend_id}"}

        last_operate_upper = (last_operate or "").upper()

        if last_operate_upper == "AGREE":
            # 接受好友申请
            updated_record = self._bot_friend_repo.accept(bot_friend_id, approval_uuid=puid)
            if updated_record:
                self._create_auth_relationship_for_approval(friend_record, bot_friend_id)
                logger.info(f"[handle_friend_request_approval_callback] Approval agreed: bot_friend_id={bot_friend_id}")
                return {"success": True, "message": "Approval agreed", "bot_friend": updated_record}
            else:
                logger.error(f"[handle_friend_request_approval_callback] Failed to accept: bot_friend_id={bot_friend_id}")
                return {"success": False, "message": "Failed to update friend record"}
        elif last_operate_upper == "DISAGREE":
            # 拒绝好友申请
            updated_record = self._bot_friend_repo.reject(bot_friend_id, approval_uuid=puid)
            if updated_record:
                logger.info(f"[handle_friend_request_approval_callback] Approval disagreed: bot_friend_id={bot_friend_id}")
                return {"success": True, "message": "Approval disagreed", "bot_friend": updated_record}
            else:
                logger.error(f"[handle_friend_request_approval_callback] Failed to reject: bot_friend_id={bot_friend_id}")
                return {"success": False, "message": "Failed to update friend record"}
        elif last_operate_upper == "CANCEL":
            # 取消好友申请
            updated_record = self._bot_friend_repo.cancel(bot_friend_id, approval_uuid=puid)
            if updated_record:
                logger.info(f"[handle_friend_request_approval_callback] Approval canceled: bot_friend_id={bot_friend_id}")
                return {"success": True, "message": "Approval canceled", "bot_friend": updated_record}
            else:
                logger.error(f"[handle_friend_request_approval_callback] Failed to cancel: bot_friend_id={bot_friend_id}")
                return {"success": False, "message": "Failed to update friend record"}
        else:
            logger.warning(f"[handle_friend_request_approval_callback] Unknown operation: {last_operate}")
            return {"success": False, "message": f"Unknown operation: {last_operate}"}

    def _create_auth_relationship_for_approval(self, friend_record: dict[str, Any], bot_friend_id: int) -> None:
        """为审批通过的好友申请创建授权关系，失败直接传播。

        流程：
        1. 查 bot 并确认当前是 RESTRICTED 模式（friend_approval=1）。
           OPEN 模式跳过，因为公开 Bot 不需要显式授权。
        2. 取 agent_code 并调用授权关系服务创建单条授权关系。

        仅 OPEN 模式无需显式授权，其他失败都会阻断审批回调完成。
        """
        bot_id = friend_record.get("target_bot_id")
        operator_id = friend_record.get("requester_entity_id")
        if not bot_id or not operator_id:
            raise BotPublicServiceError(
                "好友审批缺少授权关系所需字段: "
                f"bot_friend_id={bot_friend_id}"
            )

        bot = self._bot_repository.get_by_id_and_owner(
            bot_id,
            friend_record.get("target_entity_id"),
        )
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        # 判断 Bot 当前是否为 RESTRICTED 模式，OPEN 模式不需要授权关系
        bot_ext = bot.get("ext") or {}
        if isinstance(bot_ext, str):
            bot_ext = json.loads(bot_ext)
        friend_approval = bot_ext.get("friend_approval", "0")
        if friend_approval != "1":
            logger.info(f"[_create_auth_relationship_for_approval] Skipped (OPEN mode): bot_id={bot_id}, operator_id={operator_id}")
            return

        from agentclaw.community.core.bot_management.utils import resolve_agent_code
        agent_code = resolve_agent_code(
            bot=bot,
            passport_plugin=self._passport_plugin,
        ) or None
        if not agent_code:
            raise BotPublicServiceError(
                f"agent_code 为空，无法创建授权关系: bot_id={bot_id}"
            )

        owner_id = bot.get("owner_id")
        owner_name = bot.get("owner_name")
        result = self._auth_relationship_plugin.create_relationship(
            work_no=operator_id,
            agent_code=agent_code,
            description="Authorized by TeamClaw via friend request approval",
            operator_work_no=owner_id,
            operator_name=owner_name,
        )
        if result:
            if result.get("already_exists"):
                logger.info(f"[_create_auth_relationship_for_approval] Already exists: bot_id={bot_id}, operator_id={operator_id}")
            else:
                logger.info(f"[_create_auth_relationship_for_approval] Created: bot_id={bot_id}, operator_id={operator_id}, auth_id={result.get('auth_id')}")
        else:
            raise BotPublicServiceError(
                "授权关系服务返回失败: "
                f"bot_id={bot_id}, operator_id={operator_id}"
            )

    def search_public_bots_by_keyword(
        self,
        user_id: str,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """根据关键词分页查询公开 Bot。

        Args:
            user_id: 当前用户ID
            search: 搜索关键词，模糊匹配 owner_name 或 bot_name (可选)
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            分页结果 {"total": int, "items": list}，每个 item 可能包含:
            - friend_record_approval: 好友申请记录 (有申请记录时)
        """
        logger.info(f"[search_public_bots_by_keyword] user_id={user_id}, search={search}, page={page}, page_size={page_size}")
        public_bot_result = self._bot_service.list_bots_by_search(public="1", search=search, page=page, page_size=page_size)

        items = public_bot_result.get("items", [])
        if not items:
            return public_bot_result

        # Sanitize sensitive fields to avoid leaking them through the public search API.
        EXT_SENSITIVE_KEYS = {"iam_token"}
        for bot in items:
            # Redact top-level device_id
            if "device_id" in bot:
                bot["device_id"] = None
            # Redact sensitive fields inside ext
            ext = bot.get("ext")
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    ext = {}
            if isinstance(ext, dict):
                if isinstance(ext.get("passport"), dict) and "token" in ext["passport"]:
                    ext["passport"]["token"] = None
                for key in EXT_SENSITIVE_KEYS:
                    if key in ext:
                        ext[key] = None
                bot["ext"] = ext

        # 查询好友申请记录
        query_keys: list[BotFriendQueryKey] = []
        for bot in items:
            bot_id = bot.get("bot_id")
            if bot_id and bot.get("owner_id"):
                query_keys.append(BotFriendQueryKey(
                    requester_entity_id=user_id,
                    target_entity_id=bot.get("owner_id"),
                    target_bot_id=bot_id,
                ))

        if query_keys:
            try:
                friend_records = self._bot_friend_repo.get_by_entity_ids_batch(keys=query_keys)
                friend_map = {f"{r['requester_entity_id']}:{r['target_bot_id']}:{r['target_entity_id']}": r for r in friend_records}
                for bot in items:
                    bot["friend_record_approval"] = friend_map.get(f"{user_id}:{bot.get('bot_id')}:{bot.get('owner_id')}")
            except Exception as e:
                logger.warning(f"[search_public_bots_by_keyword] Failed to query friend records: {e}")

        return public_bot_result

    def list_my_bot_friends(
        self,
        user_id: str,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """查询当前用户的审批通过的好友 Bot 列表。

        Args:
            user_id: 当前用户ID
            search: 搜索关键词，模糊匹配 owner_name 或 bot_name (可选)
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            分页结果 {"total": int, "items": list}
        """
        logger.info(f"[list_my_bot_friends] user_id={user_id}, search={search}, page={page}, page_size={page_size}")

        # 查询用户作为申请方的审批通过的好友记录
        # search 同时匹配 target_name（目标方姓名/bot名称）和 target_owner_name（拥有者花名）
        total, records = self._bot_friend_repo.list_by_requester(
            requester_entity_id=user_id,
            status=BotFriendStatus.ACCEPTED,
            target_name=search,
            target_owner_name=search,
            page=page,
            page_size=page_size,
        )

        # 根据 owner_id (target_entity_id) 和 bot_id (target_bot_id) 并发查询 bot 信息
        if records:
            # 收集唯一的 (owner_id, bot_id) 组合
            unique_keys = set()
            for record in records:
                owner_id = record.get("target_entity_id")
                bot_id = record.get("target_bot_id")
                unique_keys.add((owner_id, bot_id))

            # 并发查询 bot 信息
            bot_map = {}
            # ThreadPoolExecutor workers don't copy context vars; bind the
            # request tenant so these BotRepository reads stay tenant-scoped.
            _get_bot = bind_current_avernet_tenant(
                self._bot_repository.get_by_id_and_owner
            )
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_key = {
                    executor.submit(_get_bot, bot_id, owner_id): (owner_id, bot_id)
                    for owner_id, bot_id in unique_keys
                }
                for future in as_completed(future_to_key):
                    owner_id, bot_id = future_to_key[future]
                    try:
                        bot = future.result()
                        bot_map[f"{owner_id}:{bot_id}"] = bot
                    except Exception as e:
                        logger.warning(f"[list_my_bot_friends] Failed to get bot: owner_id={owner_id}, bot_id={bot_id}, error={e}")

            # 将 bot 信息添加到记录中
            for record in records:
                owner_id = record.get("target_entity_id")
                bot_id = record.get("target_bot_id")
                record["bot"] = bot_map.get(f"{owner_id}:{bot_id}")

        return {
            "total": total,
            "items": records,
        }

    def get_friend_record(
        self,
        user_id: str,
        target_entity_id: str,
        target_bot_id: str,
    ) -> Optional[Dict[str, Any]]:
        """查询当前用户与目标 Bot 的好友关系记录。

        返回结果中额外包含目标 Bot 的公开状态信息（bot_access_info），
        方便前端判断该 Bot 的访问模式（公开/私人/受限访问）。

        Args:
            user_id: 当前用户ID（申请方）
            target_entity_id: 目标方实体ID
            target_bot_id: 目标Bot ID

        Returns:
            好友记录信息，额外包含 bot_access_info 字段；不存在则返回 None
        """
        logger.info(f"[get_friend_record] user_id={user_id}, target_entity_id={target_entity_id}, target_bot_id={target_bot_id}")

        friend_record = self._bot_friend_repo.get_by_entity_ids(
            requester_entity_id=user_id,
            target_entity_id=target_entity_id,
            target_bot_id=target_bot_id,
        )
        if not friend_record:
            logger.info(f"[get_friend_record] No friend record found: user_id={user_id}, target_entity_id={target_entity_id}, target_bot_id={target_bot_id}")
            return None

        # 补充目标 Bot 的公开状态信息
        bot = self._bot_repository.get_by_id_and_owner(target_bot_id, target_entity_id)
        if not bot:
            raise ValueError(f"Bot not found: bot_id={target_bot_id}, owner_id={target_entity_id}")

        try:
            bot_ext = bot.get("ext") or {}
            if isinstance(bot_ext, str):
                bot_ext = json.loads(bot_ext)
            friend_record["bot_access_info"] = {
                "public": bot.get("public", "0"),
                "friend_approval": bot_ext.get("friend_approval", "0"),
            }
            # 返回整个 bot 对象
            friend_record["bot"] = bot
            logger.info(f"[get_friend_record] Bot access info attached: bot_id={target_bot_id}, owner_id={target_entity_id}, public={friend_record['bot_access_info']['public']}, friend_approval={friend_record['bot_access_info']['friend_approval']}")
        except Exception as e:
            logger.error(f"[get_friend_record] Failed to build bot access info: bot_id={target_bot_id}, owner_id={target_entity_id}, error={e}")
            raise

        return friend_record
