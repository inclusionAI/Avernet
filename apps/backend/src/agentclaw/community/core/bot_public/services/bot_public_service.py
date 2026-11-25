"""Bot public service implementation.

纯业务逻辑层，不包含任何 HTTP 关注点。
"""
import json
import re
import time
import threading
from agentclaw.community.core.operator_context import OperatorContext as OperatorContext
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from agentclaw.community.plugin_api.approval_workflow import (
    ApprovalWorkflowPlugin,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.utils.avernet_tenant import (
    bind_current_avernet_tenant,
    get_current_avernet_tenant,
)
from agentclaw.community.core.repository.protocols.bot import BotFriendRepositoryProtocol
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogMetadataServiceProtocol,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

if TYPE_CHECKING:
    # Type-only: runtime ``from agentclaw.community.di.modules.skill_center_module
    # import SkillSetServiceFactory`` would form a cycle (di/__init__ ->
    # container -> bot_public_module -> bot_public_service).
    # BotPublicService is @provider-constructed (bot_public_module), so
    # this annotation is never resolved at runtime.
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
    from agentclaw.community.di.config import BcsFuseConfig
    # ``DeviceSyncDispatcher`` 类源头在 plugins/prod/,但 core/ 不得依赖
    # plugins/(arch lint)。镜像 ``ChannelService`` 借 ``DeviceFilesystemDispatcher``
    # 的解法:类型注解走 di/modules/ 转出口(di 是合法的 composition 层)。
    from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.bot_publish_approval import (
    BotPublishApprovalPlugin,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.core.bot_public.bot_public_service_protocol import BotPublicServiceProtocol
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskRepository
from agentclaw.community.utils.env_utils import get_current_env

from .public_support import (
    BotNotPublicError as BotNotPublicError,
    BotPermissionError as BotPermissionError,
    logger,
    _resolve_access_mode,
    BotPublicServiceError,
    BotNotFoundError,
)
from .publication_actions import PublicationActions
from .friend_catalog_actions import FriendCatalogActions

class BotPublicService(PublicationActions, FriendCatalogActions, BotPublicServiceProtocol):
    """Bot public service for managing friend request approval flow.

    通过依赖注入接收 bot_friend_repo、bot_repository、process_service、bot_service。
    """

    def __init__(
        self,
        bot_friend_repo: BotFriendRepositoryProtocol,
        bot_repository: BotRepository,
        process_service: ApprovalWorkflowPlugin,
        bot_service: BotService,
        bcn_service: BcnService,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        publish_approval_plugin: BotPublishApprovalPlugin,
        skill_set_service_factory: "SkillSetServiceFactory",
        device_context_resolver_factory: "Callable[[], DeviceContextResolver]",
        device_sync_dispatcher: "DeviceSyncDispatcher",
        bcsfuse_config: "BcsFuseConfig",
        catalog_metadata_service: BotCatalogMetadataServiceProtocol,
        risk_repository: Optional[RiskRepository] = None,
        risk_approval_guard: Any = None,
    ) -> None:
        self._bot_friend_repo = bot_friend_repo
        self._bot_repository = bot_repository
        self._process_service = process_service
        self._bot_service = bot_service
        self._bcn_service = bcn_service
        self._passport_plugin = passport_plugin
        self._auth_relationship_plugin = auth_relationship_plugin
        self._publish_approval_plugin = publish_approval_plugin
        self._skill_set_service_factory = skill_set_service_factory
        # Task 2.1: resolver + dispatcher 替代 device_sync_supplier 在
        # sync_bot_config_to_device 路径上的角色。Task 6 收口后 supplier 已删。
        # Lazy: DeviceContextResolver's conn-info builders transitively reach
        # DeviceService, which (corp) depends on BotPublicService itself —
        # eager injection here would cycle. Resolved at call time instead.
        self._resolver_factory = device_context_resolver_factory
        self._device_sync_dispatcher = device_sync_dispatcher
        self._bcsfuse_config = bcsfuse_config
        self._catalog_metadata_service = catalog_metadata_service
        self._risk_repository = risk_repository
        self._risk_approval_guard = risk_approval_guard
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
                bot_type=str(bot.get("bot_type") or "personal"),
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

    def _resolve_bcsfuse_base_url(self) -> str:
        """The BCSFuse base URL this deployment's overlay configured."""
        return self._bcsfuse_config.base_url.rstrip("/")

    def _sync_bcsfuse_runtime_state(
        self, bot_id: str, owner_id: str, public: str
    ) -> None:
        """Best-effort sync bot visibility to BCSFuse runtime state."""
        worker_id = (
            f"{bot_id}:{owner_id}"
            if self._bcsfuse_config.worker_id_with_owner is True
            else bot_id
        )
        self._sync_bcsfuse_worker_runtime_state(bot_id, worker_id, public)

    def _sync_bcsfuse_worker_runtime_state(
        self, bot_id: str, worker_id: str, public: str
    ) -> None:
        """Sync an exact worker identity; BCS UUIDs must not be qualified again."""
        base_url = self._resolve_bcsfuse_base_url()
        if not base_url:
            logger.info(
                "[_sync_bcsfuse_runtime_state] Skip BCSFuse sync: no base_url configured, "
                "bot_id=%s public=%s",
                bot_id,
                public,
            )
            return

        runtime_state = "online" if public == "1" else "offline"
        url = f"{base_url}/v1/workers/{worker_id}/{runtime_state}"
        request = Request(url, data=b"", method="PUT")
        request.add_header("Content-Type", "application/json")

        try:
            with urlopen(request, timeout=15) as response:
                logger.info(
                    "[_sync_bcsfuse_runtime_state] Synced BCSFuse runtime state: "
                    "bot_id=%s worker_id=%s runtime_state=%s status=%s",
                    bot_id,
                    worker_id,
                    runtime_state,
                    getattr(response, "status", "unknown"),
                )
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning(
                "[_sync_bcsfuse_runtime_state] BCSFuse HTTP error ignored: "
                "bot_id=%s worker_id=%s runtime_state=%s status=%s body=%s",
                bot_id,
                worker_id,
                runtime_state,
                exc.code,
                body,
            )
        except URLError as exc:
            logger.warning(
                "[_sync_bcsfuse_runtime_state] BCSFuse URL error ignored: "
                "bot_id=%s worker_id=%s runtime_state=%s reason=%s",
                bot_id,
                worker_id,
                runtime_state,
                exc.reason,
            )
        except Exception as exc:
            logger.warning(
                "[_sync_bcsfuse_runtime_state] BCSFuse sync failed but ignored: "
                "bot_id=%s worker_id=%s runtime_state=%s error=%s",
                bot_id,
                worker_id,
                runtime_state,
                exc,
            )

    def handle_public_approval_callback(self, bot_id: str, owner_id: str, puid: str, last_operate: str, public_scope: str | None = None, *, risk_check_id: str | None = None, _risk_guarded: bool = False, _risk_target: dict | None = None) -> Dict[str, Any]:
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
        if not _risk_guarded and self._risk_repository is not None and puid:
            associated = self._risk_repository.find_approval(env=get_current_env(), puid=puid)
            if associated is not None:
                if risk_check_id and risk_check_id != associated.risk_check_id:
                    return {"success": False, "message": "risk callback association mismatch"}
                risk_check_id = associated.risk_check_id
            elif not risk_check_id and re.fullmatch(
                r"antprocess-agentclaw_botpublic_risk-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:_.+)?",
                puid,
            ):
                return {"success": False, "message": "risk callback association unavailable"}
        if risk_check_id and not _risk_guarded:
            guard = self._risk_approval_guard
            if guard is None:
                return {"success": False, "message": "risk callback guard unavailable"}
            if public_scope:
                backend_bot_id, separator, entity_id = bot_id.rpartition(":")
                if not separator or not backend_bot_id or not entity_id:
                    return {"success": False, "message": "risk callback bot identity invalid"}
                bot = self._bot_repository.get_by_id_and_entity(backend_bot_id, entity_id)
            else:
                bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
            if bot and str(bot.get("owner_id")) != owner_id:
                return {"success": False, "message": "risk callback owner changed"}
            if not bot:
                return {"success": False, "message": "risk callback bot unavailable"}
            result = guard.check(get_current_env(), risk_check_id, owner_id, bot["id"], puid,
                                 (last_operate or "").upper(), public_scope or "legacy")
            if result != "ALLOW":
                return {"success": result in {"DEFERRED", "ALREADY_APPLIED"}, "message": f"risk callback {result.lower()}"}
            try:
                risk_row = guard.repo.get(env=get_current_env(), risk_check_id=risk_check_id)
                if risk_row is None:
                    raise BotPublicServiceError("risk callback record unavailable")
                risk_target = json.loads(risk_row.target_json)
                applied = self.handle_public_approval_callback(
                    bot_id, owner_id, puid, last_operate, public_scope,
                    risk_check_id=risk_check_id, _risk_guarded=True, _risk_target=risk_target,
                )
            except Exception:
                guard.finish(get_current_env(), risk_check_id, puid, (last_operate or "").upper(), False)
                raise
            message = str(applied.get("message", "")).lower()
            success = applied.get("success") is True and not any(word in message for word in ("skipped", "mismatch", "noop"))
            guard.finish(get_current_env(), risk_check_id, puid, (last_operate or "").upper(), success)
            return applied if success else {"success": False, "message": "risk callback outcome unavailable"}
        # New-version publish (public_scope non-empty, e.g. "user"/"agent"): the
        # bot's visibility is delegated to BCS, so this callback must NOT flip
        # ac_bots.public or run the passport / auth-relationship / device-sync
        # side effects of the legacy path. Persist BCS attributes first, then
        # sync user publication to BCSFuse using the exact BCS identity.
        if public_scope:
            # New-version callback (public_scope 非空 → bot_id 即 bot_uid):
            # GET friend_ext → 按 public_scope 子块 (public_user_approval/
            # public_agent_approval) 写 status = last_operate → PATCH 回。
            try:
                return self._apply_callback_decision(
                    bot_uid=bot_id, public_scope=public_scope, last_operate=last_operate,
                    **({"expected_puid": puid, "expected_target": _risk_target,
                        "before_apply": lambda: self._risk_approval_guard.revalidate_claim(
                            get_current_env(), risk_check_id, puid, (last_operate or "").upper()
                        )} if _risk_guarded else {}),
                )
            except Exception as exc:  # noqa: BLE001 — callback must not crash antprocess
                if _risk_guarded:
                    raise
                logger.warning(
                    "[handle_public_approval_callback] BCS friend_ext update failed: %s", exc
                )
                return {
                    "success": True,
                    "public": None,
                    "message": f"public_scope={public_scope} callback; BCS update logged",
                }
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
        if _risk_guarded and (
            not isinstance(_risk_target, dict)
            or public_approval.get("public") != "1"
            or public_approval.get("permission_owner") != _risk_target.get("permission_owner")
            or public_approval.get("friend_approval") != _risk_target.get("friend_approval")
        ):
            return {"success": False, "public": None, "message": "risk approval target mismatch"}
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
            if _risk_guarded and self._risk_approval_guard.revalidate_claim(
                get_current_env(), risk_check_id, puid, last_operate_upper
            ) is not True:
                return {"success": False, "public": None, "message": "risk callback eligibility changed"}
            updated_bot = self._update_bot_with_notification(bot_id, owner_id, update_data, bot, notify_device=True)
            if not updated_bot:
                return {"success": False, "public": None, "message": "Failed to update bot"}
            # Sync passport accessMode and auth relationships
            access_mode = _resolve_access_mode(public_value, friend_approval_val)
            self._sync_access_mode_and_relations_or_raise(
                bot_id, owner_id, access_mode, public_value,
            )
            self._sync_bcsfuse_runtime_state(bot_id, owner_id, public_value)
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
            # Binding and connection details are resolved inside the device
            # context resolver; callers only provide the bot and owner identity.
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

        The resolver owns binding and connection lookup; the dispatcher selects
        the provider-specific Core ``DeviceSync`` service. Missing bindings or
        unsupported providers are converted to the established failure-result
        shape instead of escaping to the caller.
        """
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
            UnknownProviderError,
        )
        try:
            ctx = self._resolver_factory().resolve_for_bot(bot_id, user_id)
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
