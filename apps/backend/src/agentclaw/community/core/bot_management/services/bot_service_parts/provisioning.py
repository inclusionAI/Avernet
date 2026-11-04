"""
Provisioning-claim release, post-creation provisioning and asynchronous device
allocation — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotProvisioningMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

import threading

from typing import Any, Dict, List, Optional, Tuple

from agentclaw.community.core.bot_management.services.default_image_policy_listener import (
    DEFAULT_IMAGE_POLICY_VALUE,
    IMAGE_POLICY_ON_ACTIVE_KEY,
)
from agentclaw.community.core.devices.errors import (
    DeviceAllocateError,
    DeviceLimitExceededError,
    DeviceNotFoundError,
    InvalidDeviceStatusError,
    ResourceInsufficientError,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus, SynlinkMappingInfo
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    overlay_image_pin_on_template_config,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotServiceError,
    DeviceAllocationError,
    _compose_operator_context,
    logger,
)


class BotProvisioningMixin:
    """Provisioning-claim release, post-creation provisioning and asynchronous device
    allocation."""

    def _release_provisioning_claim(self, bot_id: str, user_id: str) -> None:
        """Best effort: a record the failed step 2 left behind goes back to
        ``PENDING``. A soft-deleted record (the allocation failures delete it)
        is not found and nothing is written."""
        try:
            current = self._repository.get_by_id_and_owner(bot_id, user_id)
            if current and current.get("status") == "PROVISIONING" and not current.get("binding_id"):
                self._repository.update_by_owner(bot_id, user_id, {"status": "PENDING"})
        except Exception as e:  # noqa: BLE001 — the original error is the one to raise
            logger.warning(f"[bot_service.provision_bot] could not release the claim for {bot_id}: {e}")

    def _provision_created_bot(
        self,
        bot_record: Dict[str, Any],
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        bot_desc: Optional[str],
        resolved_bot_name: Optional[str],
        resolved_entity_id: str,
        resolved_entity_type: str,
        resolved_engine_types: list[str],
        resolved_active_engine: str,
        resolved_bot_type: str,
        template_type: Optional[str],
        template_config: Optional[Dict[str, Any]],
        cookie: Optional[str],
    ) -> Dict[str, Any]:
        """Step 2 and its tail, shared by ``create_bot`` and ``provision_bot``.

        The body is ``create_bot``'s own, moved: the device
        allocation (or the teclaw container), the record update, the publish
        record, the draft artifact, the template attach and memory
        initialisation. A device-allocation failure soft-deletes the record
        and raises ``BotServiceError``, as it always has.
        """
        # Step 2: 设备分配（错误立即透出给前端）。teclaw bot 走 BaaS 即时备容器
        # (create + approve)，不经 DeviceService.apply_device()；其余引擎走 apply_device。
        # 两条路径汇合到下方共享尾部（更新 bot_record + service 发布单创建）。
        # 注：teclaw service bot 的 source/草稿 容器在此即时备好；其 verify/online
        # 容器仍由发布流程（按 device_provider）单独备好。
        # teclaw 即时备容器时投递的初始 artifact —— 用于在下方创建草稿发布单后
        # 回填其 ext.config_artifact（仅 teclaw service bot）。
        teclaw_config_artifact: dict | None = None
        try:
            teclaw_provision = self._teclaw_provision_provider()
            if teclaw_provision.is_teclaw(resolved_active_engine):
                logger.info(
                    f"[bot_service.create_bot] Bot {bot_id} is teclaw, provisioning "
                    f"container via BaaS (skipping DeviceService.apply_device)"
                )
                # The passport token is fetched and pushed by the create
                # publish poll task once BaaS reports the container started —
                # the PaaS device it is written onto does not exist yet here.
                provision = teclaw_provision.provision(
                    bot=bot_record,
                    owner_id=user_id,
                )
                binding_id = provision.binding_id
                device_id = provision.device_id
                final_status = provision.status
                teclaw_config_artifact = provision.config_artifact
            else:
                service = self._device_service_provider()
                operator = _compose_operator_context(user_id, nick_name)

                # DRM: 判断新建 bot 是否走 NAS
                force_nas = self._is_new_bot_use_nas()

                # 生成软链配置
                symlink_mappings: List[SynlinkMappingInfo] = []
                try:
                    skill_set_service = self._skill_set_factory.create(
                        entity_id=resolved_entity_id,
                        entity_type=resolved_entity_type,
                        bot_id=str(bot_id),
                        engine_type=resolved_active_engine,
                    )
                    symlink_mappings = skill_set_service.get_symlink_mappings(
                        user_id=resolved_entity_id,
                        bolt_id=str(bot_id)
                    )
                    logger.info(f"[bot_service.create_bot] Generated symlink_mappings: {len(symlink_mappings)}")
                except Exception as e:
                    logger.warning(f"[bot_service.create_bot] Failed to get symlink_mappings: {e}")

                # Engine strategy 场景：按具体引擎策略传入额外环境变量。
                extra_envs = None
                # 路由到具体 provider 前，先把 template_uid 上下文带给 device 层。
                # 解析失败先记下来；只有后续真正走 BaaS 时才需要 fail-fast。
                device_template_config = self._attach_template_uid_context(
                    bot_id=str(bot_id),
                    user_id=user_id,
                    bot_type=resolved_bot_type,
                    engine_type=resolved_active_engine,
                    template_type=template_type,
                    template_config=template_config,
                )
                device_template_config = overlay_image_pin_on_template_config(
                    device_template_config,
                    bot_record.get("ext"),
                )
                extra_envs = self._build_engine_extra_envs(
                    bot_id=str(bot_id),
                    owner_id=user_id,
                    active_engine=resolved_active_engine,
                    bot_type=resolved_bot_type,
                    template_type=template_type,
                    template_config=template_config,
                    log_context="bot_service.create_bot",
                )
                if resolved_active_engine == "openclaw":
                    extra_envs = {
                        **(extra_envs or {}),
                        **self._skills_layout_env(
                            env=get_current_env(),
                            entity_id=resolved_entity_id,
                            bot_id=str(bot_id),
                        ),
                    }

                create_kwargs = dict(
                    apply_reason=f"Create bot: {resolved_bot_name or bot_id}",
                    entity_id=resolved_entity_id,
                    entity_type=resolved_entity_type,
                    operator=operator,
                    bot_id=str(bot_id),
                    engine=resolved_active_engine,
                    bot_type=resolved_bot_type,
                    owner_id=user_id,  # 创建时，owner_id 是当前登录用户
                    symbol=symlink_mappings,
                    force_nas=force_nas,
                    extra_envs=extra_envs,
                    admins=None,  # 新建 bot 还未添加协作者
                    template_type=template_type,
                    template_config=device_template_config,
                )

                # 存储业务组件决定接入范围，只返回原申请参数覆写，不创建设备。
                # 重启/补建不调用初始化，设备路由只负责原有设备申请。
                if self._bot_storage_policy is not None:
                    create_kwargs.update(self._bot_storage_policy.prepare_bot_storage_policy(**create_kwargs))
                device_result = service.apply_device(**create_kwargs)

                if not device_result:
                    raise DeviceAllocationError("设备申请返回空结果")

                binding_id = device_result.id
                device_id = device_result.device_id
                device_status = device_result.status

                # 更新 bot 记录（绑定设备）
                if device_status == DeviceBindingStatus.ACTIVE.value:
                    final_status = "ACTIVE"
                else:
                    final_status = "PENDING"  # DaaS 设备等待回调

            self._repository.update_by_owner(bot_id, user_id, {
                "binding_id": binding_id,
                "device_id": device_id,
            })
            logger.info(f"[bot_service.create_bot] Bot {bot_id} device allocated: "
                       f"binding_id={binding_id}, status={final_status}")

            bot_record["binding_id"] = binding_id
            bot_record["device_id"] = device_id
            bot_record["status"] = final_status
            bot_record["engine_types"] = resolved_engine_types

            # 如果是服务型 bot，创建发布单
            if resolved_bot_type == "service":
                try:
                    publish_service = self._bot_publish_provider()

                    publish_record = publish_service.create_publish(
                        source_bot_pk=bot_record["id"],
                        source_bot_id=bot_id,
                        publish_bot_id=bot_id,
                        name=resolved_bot_name,
                        owner_id=user_id,
                        permission_owner="owner",
                        description=bot_desc,
                        owner_name=nick_name,
                    )
                    # 将发布单信息保存到 bot_record 中
                    bot_record["publish"] = publish_record.to_dict()
                    logger.info(f"[bot_service.create_bot] Created publish record for service bot {bot_id}")

                except Exception as e:
                    # 发布单是服务化治理的载体，直接创建即服务的口径依赖它存在；
                    # 持久化失败不能静默返回成功（那会留下一个无法发布、无法被
                    # 编排推进的"服务 bot"）。与 workspace-hosting 失败同惯例：
                    # 保留已插入 Bot/layout 选择并上抛；重试通过 provision claim
                    # 复用同一选择并补建缺失发布单。
                    logger.exception(
                        "[bot_service.create_bot] Failed to create publish record for service bot %s",
                        bot_id,
                    )
                    raise BotServiceError(
                        f"Failed to create publish record for service bot {bot_id}"
                    ) from e

                # teclaw service bot：把即时备容器时投递的初始 artifact 回填到
                # 刚创建的草稿发布单 ext.config_artifact，使草稿期也有当前配置的
                # 持久记录。独立 try（不与建单混淆日志），best-effort 不影响建 bot。
                # 草稿期 publish_bot_id == bot_id，故 record_draft_artifact 按
                # publish_bot_id 即可定位该草稿行。
                if teclaw_config_artifact is not None:
                    try:
                        publish_service.record_draft_artifact(
                            bot_id=bot_id,
                            artifact=teclaw_config_artifact,
                        )
                    except Exception as e:
                        logger.warning(
                            f"[bot_service.create_bot] draft artifact record "
                            f"failed for bot {bot_id}: {e}"
                        )

            # Attach template_config to bot_record if template was created
            if template_config and template_type:
                bot_record["template_config"] = template_config

            # applicationCoding 保留原 memory 初始化；新通用 CC / 架构师 Bot
            # 的业务知识库、Wiki、RepoWiki 复用同一链路，但从
            # template_config resolved 快照检测配置来源。
            if self._should_trigger_memory_initialization(
                active_engine=resolved_active_engine,
                template_type=template_type,
                template_config=template_config,
                on_create=True,
            ):
                logger.info(
                    f"[bot_service.create_bot] Triggering memory initialization for "
                    f"template-backed bot {bot_id}, template_type={template_type}"
                )
                from agentclaw.community.core.bot_management.utils import trigger_memory_initialization

                trigger_memory_initialization(
                    bot_id=bot_id,
                    bot_name=resolved_bot_name,
                    user_id=user_id,
                    template_config=template_config,
                    cookie=cookie or "",
                    aixcore_base_url=self._workspace_hosting_config.aixcore_base_url,
                )
                logger.info(
                    f"[bot_service.create_bot] Memory initialization completed for bot {bot_id}"
                )

            return bot_record
        except BotServiceError:
            # The caller releases the provisioning claim.  The Bot/layout
            # choice remains durable so a retry cannot re-evaluate rollout.
            raise
        except (ResourceInsufficientError, DeviceAllocateError, DeviceLimitExceededError) as e:
            logger.error(f"[bot_service.create_bot] Device allocation failed for bot {bot_id}: {e}")
            raise BotServiceError(f"设备申请失败: {e}")
        except Exception as e:
            logger.exception(f"[bot_service.create_bot] Unexpected error during device allocation for bot {bot_id}: {e}")
            raise BotServiceError(f"设备申请失败: {e}")

    def _allocate_device_async(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        entity_id: str,
        entity_type: str,
        engine_types: list[str],
        bot_name: Optional[str] = None,
        active_engine: str = DEFAULT_ENGINE_TYPE,
        owner_id: Optional[str] = None,
        force_nas: bool = False,
        device_provider: Optional[str] = None,
        restart_lock_key: Optional[Tuple[str, str, str, str]] = None,
        bot_ext_override: Optional[Dict[str, Any]] = None,
        extra_configs: Optional[Dict[str, Any]] = None,
    ):
        """
        Allocate device asynchronously in background thread.

        This runs the device allocation in a separate thread to avoid blocking
        the HTTP response. Updates bot status and binding_id based on result.
        Device binding is stored in ac_entity_device_binding table (reused from device service).

        Args:
            restart_lock_key: When set (restart flow), a ``(env, entity_id,
                bot_id, lock_token)`` tuple identifying the restart idempotency
                lock. The background thread owns releasing it: the row is
                compare-and-deleted (by token) in the ``finally`` once
                allocation finishes (success or failure), so a row another
                holder acquired after a reap is never deleted. When ``None``
                (create/start flows) no lock is touched.
            device_provider: Explicit device_provider fact to preserve for
                existing bots during restart. ``None`` means this is a new
                create/start allocation and the router may use creation rollout.
        """
        def do_allocate():
            try:
                logger.info(f"[bot_service._allocate_device_async] Starting device allocation for bot {bot_id}")

                # 桌面 bot 的设备分配走 BaaS 流程（device_id = BOT-xxx），
                # 不应走 DeviceService.apply_device()（会生成 staff_xxx 格式 device_id）。
                bot_record = self._repository.get_by_id_and_owner(bot_id, user_id)
                if bot_record and bot_record.get("bot_type") == "desktop":
                    logger.warning(
                        f"[bot_service._allocate_device_async] Skipping bot {bot_id}: "
                        f"desktop bots use BaaS device allocation, not DeviceService"
                    )
                    return

                # Get device service instance
                service = self._device_service_provider()

                # Build operator context
                operator = _compose_operator_context(user_id, nick_name)

                # 判断重启 bot 是否走 NAS：
                # 1. 上层显式指定 force_nas=True（如迁移服务）→ 直接走 NAS
                # 2. 迁移名单中 storage_status == "nas" → 走 NAS
                # 3. DRM 开关 is_new_bot_use_nas() == True → 走 NAS
                resolved_force_nas = force_nas
                if not resolved_force_nas:
                    try:
                        record = self._oss_record_repo.get_record(entity_id, str(bot_id))
                        if record and record.get("storage_status") == "nas":
                            resolved_force_nas = True
                            logger.info(f"[bot_service._allocate_device_async] Bot {bot_id} in NAS migration list, force_nas=True")
                    except Exception as e:
                        logger.info(f"[bot_service._allocate_device_async] Migration record check skipped: {e}")

                if not resolved_force_nas:
                    try:
                        resolved_force_nas = self._is_new_bot_use_nas()
                        logger.info(f"[bot_service._allocate_device_async] DRM is_new_bot_use_nas={resolved_force_nas}")
                    except Exception as e:
                        logger.warning(f"[bot_service._allocate_device_async] DRM check failed, fallback force_nas=False: {e}")

                # 生成软链配置
                symlink_mappings: List[SynlinkMappingInfo] = []
                try:
                    skill_set_service = self._skill_set_factory.create(
                        entity_id=entity_id,
                        entity_type=entity_type,
                        bot_id=str(bot_id),
                        engine_type=active_engine if active_engine == "claude_code" else None,
                    )
                    symlink_mappings = skill_set_service.get_symlink_mappings(
                        user_id=entity_id,
                        bolt_id=str(bot_id)
                    )
                    logger.info(f"[bot_service._allocate_device_async] Generated symlink_mappings: {len(symlink_mappings)}")
                except Exception as e:
                    logger.warning(f"[bot_service._allocate_device_async] Failed to get symlink_mappings: {e}")

                # Read template_config from ac_templates (not ac_bots).
                # bot_record comes from ac_bots which has no template_config column;
                # the value lives in ac_templates.ext.  Fetching it here ensures
                # restart / start-bot paths use the saved overrides (image,
                # command, envs, resource_spec) instead of losing them.
                bot_template_type = bot_record.get("template_type") if bot_record else None
                # Get bot_type from bot_record before building provisioning context.
                resolved_bot_type = bot_record.get("bot_type", "") if bot_record else ""
                resolved_template_config = None
                try:
                    resolved_template_config = self._template_service.get_template_config(bot_id)
                except Exception as e:
                    logger.warning(
                        "[bot_service._allocate_device_async] Failed to get template for bot %s: %s",
                        bot_id, e,
                    )

                # Engine strategy 重启场景：传入额外的环境变量。
                extra_envs = self._build_engine_extra_envs(
                    bot_id=str(bot_id),
                    owner_id=owner_id or user_id,
                    active_engine=active_engine,
                    bot_type=resolved_bot_type,
                    template_type=bot_template_type,
                    template_config=resolved_template_config,
                    log_context="bot_service._allocate_device_async",
                )
                if active_engine == "openclaw":
                    extra_envs = {
                        **(extra_envs or {}),
                        **self._skills_layout_env(
                            env=get_current_env(),
                            entity_id=entity_id,
                            bot_id=str(bot_id),
                        ),
                    }

                # Call device service to allocate device
                # This creates a record in ac_entity_device_binding table
                resolved_owner_id = owner_id or user_id
                admins = self._query_admin_worknos(bot_id=str(bot_id), owner_id=resolved_owner_id)
                # 路由到具体 provider 前，先把 template_uid 上下文带给 device 层。
                # 解析失败先记下来；只有后续真正走 BaaS 时才需要 fail-fast。
                device_template_config = self._attach_template_uid_context(
                    bot_id=str(bot_id),
                    user_id=user_id,
                    bot_type=resolved_bot_type,
                    engine_type=active_engine,
                    template_type=bot_template_type,
                    template_config=resolved_template_config,
                )
                effective_bot_ext = (
                    bot_ext_override
                    if bot_ext_override is not None
                    else (bot_record.get("ext") if bot_record else None)
                )
                device_template_config = overlay_image_pin_on_template_config(
                    device_template_config,
                    effective_bot_ext,
                )
                logger.info(
                    f"[bot_service._allocate_device_async] allocation requested: "
                    f"bot_id={bot_id}, user_id={user_id}, entity_id={entity_id}, "
                    f"active_engine={active_engine}, bot_type={resolved_bot_type}, "
                    f"explicit_device_provider={device_provider or '<empty>'}, "
                    f"force_nas={resolved_force_nas}"
                )
                apply_kwargs = {
                    "apply_reason": f"Create bot: {bot_name or bot_id}",
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "operator": operator,
                    "bot_id": str(bot_id),
                    "engine": active_engine,
                    "bot_type": resolved_bot_type,
                    "owner_id": resolved_owner_id,  # 重启时 owner_id 从原 bot 表获取
                    "symbol": symlink_mappings,
                    "force_nas": resolved_force_nas,
                    "extra_envs": extra_envs,
                    "admins": admins,
                    "template_type": bot_template_type,
                    "template_config": device_template_config,
                }
                # Restart passes the provider that created the previous
                # binding. New create/start calls leave it empty so the router
                # can still apply the create-time rollout policy.
                if device_provider is not None:
                    apply_kwargs["device_provider"] = device_provider
                if bot_ext_override is not None:
                    apply_kwargs["device_props_extra"] = {
                        IMAGE_POLICY_ON_ACTIVE_KEY: DEFAULT_IMAGE_POLICY_VALUE
                    }

                device_result = service.apply_device(**apply_kwargs)

                if not device_result:
                    raise DeviceAllocationError("Device allocation returned empty result")

                binding_id = device_result.id
                device_id = device_result.device_id
                allocated_device_provider = device_result.device_provider
                device_status = device_result.status

                if not binding_id:
                    raise DeviceAllocationError("Device allocation did not return binding_id")

                logger.info(f"[bot_service._allocate_device_async] Device allocated for bot {bot_id}: "
                           f"binding_id={binding_id}, device_id={device_id}, provider={allocated_device_provider}, status={device_status}")

                if restart_lock_key is not None:
                    try:
                        from agentclaw.community.core.bot_management.engines import (
                            resolve_provisioning,
                        )

                        refresh_ctx, refresh_strategy = resolve_provisioning(
                            bot_id=str(bot_id),
                            owner_id=str((bot_record or {}).get("owner_id") or owner_id or user_id),
                            bot_type=str((bot_record or {}).get("bot_type") or resolved_bot_type or ""),
                            active_engine=(bot_record or {}).get("active_engine") or active_engine,
                            template_type=(bot_record or {}).get("template_type") or bot_template_type,
                            template_config=None,
                        )
                        runtime_reconciler = self._runtime_reconciler
                        if runtime_reconciler is None and self._runtime_reconciler_provider is not None:
                            try:
                                runtime_reconciler = self._runtime_reconciler_provider()
                            except Exception as runtime_error:
                                logger.warning(
                                    "[bot_service._allocate_device_async] restart runtime reconciler unavailable; continue allocation: bot_id=%s error=%s",
                                    bot_id,
                                    runtime_error,
                                    exc_info=True,
                                )
                        refresh_strategy.refresh_restart_authorization(
                            refresh_ctx,
                            bot_record or {},
                            extra_configs,
                            skill_set_factory=self._skill_set_factory,
                            runtime_reconciler=runtime_reconciler,
                            template_service=self._template_service,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[bot_service._allocate_device_async] restart authorization refresh failed; "
                            "continue allocation: bot_id=%s error=%s",
                            bot_id,
                            exc,
                            exc_info=True,
                        )

                # Map device status to bot status
                # Device status can be: PENDING, ACTIVE, RELEASED
                if device_status == DeviceBindingStatus.ACTIVE.value:
                    final_status = "ACTIVE"
                elif device_status == DeviceBindingStatus.PENDING.value:
                    final_status = "PENDING"
                else:
                    final_status = "FAILED"

                # Update bot with binding_id, device_id and final status
                # Use update_by_owner to ensure we only update the owner's bot
                bot_update = {
                    "binding_id": binding_id,
                    "device_id": device_id,
                    "status": final_status,
                }
                updated = self._repository.update_by_owner(bot_id, user_id, bot_update)
                if not updated:
                    logger.error(f"[bot_service._allocate_device_async] Failed to update bot {bot_id} for user {user_id}: bot not found or not owner")
                    return
                if (
                    bot_ext_override is not None
                    and device_status == DeviceBindingStatus.ACTIVE.value
                ):
                    self._persist_service_bot_default_image(
                        {**(bot_record or {}), "bot_id": bot_id, "ext": bot_ext_override},
                        user_id=user_id,
                    )

                logger.info(f"[bot_service._allocate_device_async] Bot {bot_id} updated with binding_id={binding_id}, status={final_status}")

            except (DeviceNotFoundError, InvalidDeviceStatusError, DeviceLimitExceededError) as e:
                logger.error(f"[bot_service._allocate_device_async] Device allocation failed for bot {bot_id}: {e}")
                # Update bot status to FAILED (use update_by_owner to ensure correct ownership)
                self._repository.update_by_owner(bot_id, user_id, {"status": "FAILED"})

            except Exception as e:
                logger.error(f"[bot_service._allocate_device_async] Unexpected error for bot {bot_id}: {e}")
                # Update bot status to FAILED (use update_by_owner to ensure correct ownership)
                self._repository.update_by_owner(bot_id, user_id, {"status": "FAILED"})

            finally:
                # Release the restart idempotency lock once allocation finishes,
                # on every path (success, desktop early-return, error). This is
                # the authoritative release point — the lock is held across the
                # whole ~2-min allocation so concurrent restart clicks are
                # suppressed until it completes. No-op for non-restart flows.
                if restart_lock_key is not None:
                    try:
                        self._restart_lock_repo.release(*restart_lock_key)
                        logger.info(
                            "[bot_service._allocate_device_async] Released restart lock for bot %s: %s",
                            bot_id, restart_lock_key,
                        )
                    except Exception as release_err:
                        # A failed release is non-fatal: the TTL reaper will
                        # reclaim the row on the next restart attempt.
                        logger.error(
                            "[bot_service._allocate_device_async] Failed to release restart lock for bot %s (%s): %s",
                            bot_id, restart_lock_key, release_err,
                        )

        # Start device allocation in background thread
        thread = threading.Thread(
            target=bind_current_avernet_tenant(do_allocate), daemon=True
        )
        thread.start()
        logger.info(f"[bot_service._allocate_device_async] Started background thread for bot {bot_id} device allocation")
