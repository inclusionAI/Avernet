"""
``restart_bot`` and the BaaS in-place restart — moved verbatim from
``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotRestartMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

import time

from typing import Any, Dict, Optional

from agentclaw.community.core.bot_management.bcn_identity import provider_address_kwargs
from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    outbound_avatar_from_record,
)
from agentclaw.community.core.bot_management.services.default_image_policy_listener import (
    DEFAULT_IMAGE_POLICY_VALUE,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    overlay_image_pin_on_template_config,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
    BotServiceError,
    logger,
)


class BotRestartMixin:
    """``restart_bot`` and the BaaS in-place restart."""

    def restart_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: Optional[str] = None,
        extra_configs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Restart a bot by releasing current device and allocating a new one.

        Args:
            bot_id: Bot ID
            user_id: User ID for permission check (must be the owner)
            nick_name: Nick name for device allocation (optional, defaults to user_id)
            extra_configs: Optional engine-agnostic restart extension envelope

        Returns:
            Updated bot record with PENDING status (device allocation in progress)

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If restart fails
        """
        if not user_id:
            raise BotServiceError("User ID is required for restarting bot")

        # Look up the bot first to derive the lock key and fail fast if missing.
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        if self.is_teclaw_bot(bot.get("active_engine")):
            logger.warning(
                "[bot_service.restart_bot] reject restart for teclaw bot: "
                "bot_id=%s user_id=%s",
                bot_id,
                user_id,
            )
            raise BotOperationNotAllowedError("teclaw 类型的 Bot 不支持重启")

        if bot.get("bot_type") == "desktop":
            raise BotServiceError(
                f"Desktop bot {bot_id} cannot be stopped via BotService.stop_bot, "
                "use DesktopBotService instead"
            )

        bot_status = str(bot.get("status") or "").upper()
        # REACTIVATING is an explicit lifecycle operation. PENDING is not:
        # failed startup reporting can strand a bot there indefinitely, so a
        # restart request must be allowed to reach the durable restart lock
        # below instead of being treated as proof that work is still running.
        if bot_status == "REACTIVATING":
            logger.info(
                "[bot_service.restart_bot] skip restart while activation is in progress: "
                "bot_id=%s user_id=%s bot_status=%s",
                bot_id,
                user_id,
                bot_status,
            )
            return self._activation_in_progress_result(bot)

        if bot_status not in {"ACTIVE", "FAILED", "PENDING"}:
            logger.warning(
                "[bot_service.restart_bot] reject restart for invalid lifecycle state: "
                "bot_id=%s user_id=%s bot_status=%s",
                bot_id,
                user_id,
                bot_status,
            )
            raise BotInvalidLifecycleStateError(
                bot_id=bot_id,
                current_status=bot_status or "UNKNOWN",
            )

        # Delegate optional engine-owned restart inputs after lifecycle guards
        # pass and before device allocation consumes persisted configuration.
        from agentclaw.community.core.bot_management.engines import (
            resolve_provisioning,
        )

        ctx, strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=str(bot.get("owner_id") or ""),
            bot_type=str(bot.get("bot_type") or ""),
            active_engine=bot.get("active_engine"),
            template_type=bot.get("template_type"),
            template_config=None,
        )
        try:
            strategy.apply_restart_extra_configs(
                ctx,
                extra_configs,
                template_service=self._template_service,
            )
            # confirmed_template_update 的 MCP/软链刷新由 engine 自己在
            # refresh_restart_authorization 里判断并执行；replacement restart
            # 等新设备 apply 成功后再 resolve 并调用，避免写到旧设备上。
        except Exception as exc:
            # Optional engine extensions must never block the existing restart path.
            logger.warning(
                "[bot_service.restart_bot] applying restart extra configs failed; "
                "continue with stored config: bot_id=%s error=%s",
                bot_id,
                exc,
                exc_info=True,
            )

        env = get_current_env()
        entity_id = bot.get("entity_id")
        if not entity_id:
            # entity_id is part of the lock key (and required for allocation);
            # a missing one would poison the key, so refuse rather than guard
            # against the wrong scope.
            raise BotServiceError(f"Bot {bot_id} has no entity_id; cannot restart")

        # Restart normally preserves its provider. Existing ARCA bots opt in to
        # BaaS only when the same database-backed template override used during
        # creation positively matches their owner; every other outcome keeps the
        # legacy provider behavior. An ACTIVE bot without a binding retains the
        # legacy fallback to normal allocation. FAILED bots require a trustworthy
        # historical provider because their current binding may be lost.
        current_device_provider = None
        binding_id = bot.get("binding_id")
        recovered_without_binding = False
        if binding_id:
            current_device_provider, binding_status = (
                self._resolve_current_device_restart_context(
                    bot_id=bot_id,
                    binding_id=binding_id,
                )
            )
            # An ACTIVE bot with a PENDING binding is still converging. When
            # both records are PENDING, however, allow the explicit restart to
            # recover the stranded lifecycle through the lock-protected path.
            if (
                binding_status == DeviceBindingStatus.PENDING.value
                and bot_status != "PENDING"
            ):
                logger.info(
                    "[bot_service.restart_bot] skip restart while binding is pending: "
                    "bot_id=%s user_id=%s binding_id=%s",
                    bot_id,
                    user_id,
                    binding_id,
                )
                current = self._activation_in_progress_result(bot)
                current["status"] = "PENDING"
                return current
            if binding_status and binding_status not in {
                DeviceBindingStatus.ACTIVE.value,
                DeviceBindingStatus.PENDING.value,
                DeviceBindingStatus.FAILED.value,
                DeviceBindingStatus.STOPPED.value,
            }:
                logger.warning(
                    "[bot_service.restart_bot] reject restart for invalid binding state: "
                    "bot_id=%s user_id=%s binding_id=%s binding_status=%s",
                    bot_id,
                    user_id,
                    binding_id,
                    binding_status,
                )
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id,
                    current_status=f"BINDING_{binding_status}",
                )
            logger.info(
                f"[bot_service.restart_bot] preserve device_provider before restart: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, "
                f"device_provider={current_device_provider}"
            )
        elif bot_status == "FAILED":
            current_device_provider = self._resolve_historical_unbound_restart_provider(
                bot_id=bot_id,
                entity_id=str(entity_id),
                entity_type=bot.get("entity_type", "staff"),
                env=env,
            )
            if not current_device_provider:
                logger.warning(
                    "[bot_service.restart_bot] reject failed bot without recoverable binding: "
                    "bot_id=%s user_id=%s",
                    bot_id,
                    user_id,
                )
                raise BotInvalidLifecycleStateError(
                    bot_id=bot_id,
                    current_status="FAILED_WITHOUT_BINDING",
                )
            recovered_without_binding = True
        else:
            logger.info(
                f"[bot_service.restart_bot] no binding provider to preserve: "
                f"bot_id={bot_id}, user_id={user_id}"
            )

        restart_target_provider = self._resolve_restart_target_provider(
            bot_id=bot_id,
            user_id=user_id,
            bot=bot,
            source_provider=current_device_provider,
        )

        # Engine precondition (e.g. coding's mounted-data backup) runs BEFORE
        # the short-lived lock: it may wait far longer than the lock TTL and
        # must not hold it. It never touches the lock — it returns an optional
        # verifier to run under the lock below. Failures raise here, before any
        # lock exists, so no cleanup is needed.
        verify_restart_backup = strategy.prepare_restart(
            ctx, binding_id=binding_id,
            restart_dependencies=self._restart_backup_dependencies,
            device_service_provider=self._device_service_provider,
            bot_repository=self._repository,
        )

        # Idempotency guard: acquire the per-bot restart lock. If a restart is
        # already in progress, suppress this duplicate and return the current
        # in-progress bot — the frontend is already polling on PENDING, so this
        # behaves identically to the first click (no duplicate sandbox/binding).
        lock = self._try_acquire_restart_lock(env, entity_id, bot_id, user_id)
        if lock is None:
            logger.info(
                "[bot_service.restart_bot] Restart already in progress for bot %s "
                "(env=%s, entity_id=%s); suppressing duplicate request.",
                bot_id, env, entity_id,
            )
            # We were suppressed because a restart is already in flight. Return
            # the in-progress shape the first request's stop_bot produces
            # (PENDING, binding cleared) so the client polls correctly.
            #
            # We apply this reset to the RETURNED record only — we do NOT
            # persist it. There is a window where the in-flight restart raced
            # ahead of stop_bot (so the DB still reads ACTIVE), and a window
            # where it already finished (ACTIVE + live binding) but hasn't
            # released the lock yet; persisting here would either be redundant
            # or clobber a live binding. Shaping only the response is safe.
            #
            # The "correct" fix is to make acquire-lock + set-PENDING atomic,
            # but this service has no multi-statement transaction support today
            # (prod runs at AUTOCOMMIT and each repo opens its own connection).
            # The service is broadly in a poor state re: transactional
            # consistency — revisit this once a transaction scope exists.
            current = dict(self._repository.get_by_id_and_owner(bot_id, user_id) or bot)
            current["status"] = "PENDING"
            current["binding_id"] = None
            current["device_id"] = None
            return current

        # Lock held. The async allocation thread is the authoritative releaser
        # (see _allocate_device_async). ``handed_off`` flips True once start_bot
        # has spawned that thread; until then this method's finally releases the
        # lock so a synchronous failure can't orphan it.
        lock_key = (env, entity_id, bot_id, lock.lock_token)
        handed_off = False
        try:
            # Under-lock verification: the backup must still match the binding
            # and container identity we are about to replace. On failure the
            # existing except/finally below releases the lock untouched.
            if verify_restart_backup is not None:
                verify_restart_backup()
            if bot.get("bot_type") == "service" and not self.is_teclaw_bot(
                bot.get("active_engine")
            ):
                bot = self._mark_service_bot_default_image(bot)
            if (
                binding_id
                and current_device_provider == BAAS_DEVICE_PROVIDER
                and self._baas_service_provider is not None
            ):
                # BaaS 原地重启：不 destroy、不 release binding，bot_uuid/device_uuid 不变,
                # session NAS 目录复用，历史 session 留存。arca 永不进此支(provider 取自 binding 表)。
                # 同步完成、无 async 接管，handed_off 保持 False → finally 当场释放锁。
                updated_bot = self._restart_bot_baas(
                    bot_id=bot_id, user_id=user_id, binding_id=binding_id, bot=bot,
                )
                logger.info(f"[bot_service.restart_bot] Bot {bot_id} in-place restart via BaaS, binding preserved")
                return updated_bot
            release_ok = True
            if not recovered_without_binding:
                release_ok = self.stop_bot(
                    bot_id=bot_id,
                    user_id=user_id,
                    nick_name=nick_name,
                    release_reason=f"Bot {bot_id} restarted",
                )
            else:
                # There is no current binding to release, but the asynchronous
                # allocation can take time. Publish PENDING before spawning it
                # so status polling does not continue to report the old FAILED
                # state while recovery is underway.
                self._repository.update_by_owner(
                    bot_id,
                    user_id,
                    {
                        "status": "PENDING",
                        "binding_id": None,
                        "device_id": None,
                    },
                )
            updated_bot = self.start_bot(
                bot_id=bot_id,
                user_id=user_id,
                nick_name=nick_name,
                device_provider=restart_target_provider,
                restart_lock_key=lock_key,
                bot_ext_override=(
                    bot.get("ext")
                    if bot.get("bot_type") == "service"
                    and not self.is_teclaw_bot(bot.get("active_engine"))
                    and (bot.get("ext") or {}).get("sbot_use_default_image") is True
                    else None
                ),
                extra_configs=extra_configs,
            )
            handed_off = True

            if not release_ok:
                logger.warning(f"[bot_service.restart_bot] Bot {bot_id} restart initiated with warnings: old device may not be released properly")
                updated_bot["restart_warning"] = "旧设备释放可能失败，如后续出现设备限制问题，请联系管理员"
            else:
                logger.info(f"[bot_service.restart_bot] Bot {bot_id} restart initiated, device allocation in progress")
            return updated_bot

        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.restart_bot] Failed to restart bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to restart bot: {e}")
        finally:
            # Only release here if the async thread never took ownership (i.e. a
            # synchronous failure before allocation was spawned). Otherwise the
            # thread's finally is responsible for the release. The token ensures
            # we only delete the row we acquired.
            if not handed_off:
                self._restart_lock_repo.release(env, entity_id, bot_id, lock.lock_token)

    def _restart_bot_baas(self, *, bot_id: str, user_id: str, binding_id: int, bot: Dict[str, Any]) -> Dict[str, Any]:
        """BaaS 原地重启：调 BaasService.upgrade_bot（走 /update）不 destroy、不 release binding。

        bot_uuid/device_uuid 不变 → session NAS 目录复用 → 历史 session 留存。
        binding/device_id 保持不动，返回当前 bot 记录。
        普通 restart 不传 migration_path，cmd 不拼 --source_dir；发布态迁移源
        只在 service bot 发布流程里传入。
        """
        binding = self._device_service_provider().get_device(binding_id=binding_id)
        bot_uuid = (
            binding.get("device_id") if isinstance(binding, dict)
            else getattr(binding, "device_id", None)
        )
        if not bot_uuid:
            raise BotServiceError(f"Bot {bot_id} binding {binding_id} missing bot_uuid; cannot baas restart")

        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            RESTART_IMAGE_POLICY_ON_SUCCESS_KEY,
            RESTART_REQUEST_ID_KEY,
            RESTART_WORKFLOW_BASELINE_KEY,
        )

        raw_binding_props = (
            binding.get("device_props", {})
            if isinstance(binding, dict)
            else (getattr(binding, "device_props", None) or {})
        )
        binding_props = raw_binding_props if isinstance(raw_binding_props, dict) else {}
        restart_request_id = binding_props.get(RESTART_REQUEST_ID_KEY)
        restart_workflow_baseline = binding_props.get(RESTART_WORKFLOW_BASELINE_KEY)
        has_durable_recovery_intent = (
            isinstance(restart_request_id, str)
            and bool(restart_request_id)
            and isinstance(restart_workflow_baseline, int)
            and not isinstance(restart_workflow_baseline, bool)
            and restart_workflow_baseline >= 0
        )
        if has_durable_recovery_intent:
            logger.info(
                "[bot_service._restart_bot_baas] restart already has a durable "
                "recovery intent: bot_id=%s binding_id=%s request_id=%s baseline=%s",
                bot_id,
                binding_id,
                restart_request_id,
                restart_workflow_baseline,
            )
            return dict(self._repository.get_by_id_and_owner(bot_id, user_id) or bot)
        if restart_request_id:
            logger.warning(
                "[bot_service._restart_bot_baas] ignoring legacy restart intent "
                "without a valid workflow baseline: bot_id=%s binding_id=%s "
                "request_id=%s baseline=%r",
                bot_id,
                binding_id,
                restart_request_id,
                restart_workflow_baseline,
            )

        active_engine = (bot.get("active_engine") or "").strip()
        bot_type = bot.get("bot_type") or "personal"
        bot_template_type = (bot.get("template_type") or "").strip()

        # 先读取模板快照，供后续 BaaS restart 复用。
        try:
            resolved_template_config = self._template_service.get_template_config(bot_id)
        except Exception as e:
            logger.warning(
                "[bot_service._restart_bot_baas] Failed to get template for bot %s: %s",
                bot_id, e,
            )
            resolved_template_config = None

        # BaaS 原地重启不会经过 start_bot，这里补齐启动链路的 BCN Provider 注册。
        # 注册接口幂等：已注册时直接返回，也能重试创建阶段失败的注册。
        connection_mode = self._resolve_bcn_provider_connection_mode(
            active_engine=active_engine,
            bot_type=bot_type,
        )
        logger.info(
            "[bot_service._restart_bot_baas] register bot to BCN as provider: "
            "bot_id=%s active_engine=%s bot_type=%s template_type=%s connection_mode=%s",
            bot_id,
            active_engine,
            bot_type,
            bot_template_type,
            connection_mode,
        )
        self._register_bot_to_bcn_as_provider(
            bot_id=bot_id,
            user_id=user_id,
            owner_workno=bot.get("owner_id") or user_id,
            bot_name=bot.get("bot_name") or bot_id,
            bot_summary=bot.get("bot_desc") or "",
            connection_mode=connection_mode,
            avatar_url=outbound_avatar_from_record(
                bot, log_context="[bot_service._restart_bot_baas]"
            ),
            **provider_address_kwargs(bot),
            respect_drm=False,
        )

        # 普通 restart 入口只重启当前 bot，不使用发布态 build 产物目录。
        # 发布态 verify/online 的重启由 PublishFlowService.restart_bot(publish_id) 处理。
        mig: Optional[str] = None
        restart_stage = (
            PublishStage.DRAFT.value
            if bot_type == "service"
            else None
        )

        # resolved_template_config 已在 BCN 注册前读取，后续 BaaS restart 复用同一快照。
        # 与 _allocate_device_async（create / arca-restart 路径）同口径构造
        # extra_envs，并独立透传 template_config。extra_envs 提供引擎策略
        # 变量（BOT_TYPE / RELAY_DEFAULT_* / AIX_DEVFLOW_INFO / GIT_ADDRESSES），
        # template_config 提供沙箱覆写（envs / image / resource_spec）。两者
        # 不能互相门控。
        extra_envs: Optional[Dict[str, Any]] = self._build_engine_extra_envs(
            bot_id=str(bot_id),
            owner_id=user_id,
            active_engine=active_engine,
            bot_type=bot.get("bot_type", ""),
            template_type=bot_template_type,
            template_config=resolved_template_config,
            log_context="bot_service._restart_bot_baas",
        )
        if active_engine == "openclaw":
            extra_envs = {
                **(extra_envs or {}),
                **self._skills_layout_env(
                    env=get_current_env(),
                    entity_id=str(bot.get("entity_id") or f"staff_{user_id}"),
                    bot_id=bot_id,
                ),
            }
        # 与 _allocate_device_async 对齐：BaaS 原地重启也必须透传模板快照。
        # template_config.envs / image / resource_spec 是独立的沙箱覆写能力，
        # 不能被 extra_envs（引擎策略环境变量）是否命中门控影响。否则非
        # coding 模板或仅配置 envs/image/spec 的模板在 restart -> /update 时会
        # 退化成默认 envs，丢失创建 Bot 时使用的沙箱覆写。
        try:
            device_template_config = self._attach_template_uid_context(
                bot_id=str(bot_id),
                user_id=user_id,
                bot_type=bot.get("bot_type", ""),
                engine_type=active_engine,
                template_type=bot_template_type,
                template_config=resolved_template_config,
            )
        except Exception as e:
            logger.warning(
                "[bot_service._restart_bot_baas] Failed to attach template uid context for bot %s: %s",
                bot_id, e,
            )
            device_template_config = resolved_template_config
        device_template_config = overlay_image_pin_on_template_config(
            device_template_config,
            bot.get("ext"),
        )

        import uuid as _uuid
        request_id = _uuid.uuid4().hex
        template_uuid = self._resolve_baas_restart_template_uuid(
            bot_id=bot_id,
            user_id=user_id,
            bot=bot,
            template_config=resolved_template_config,
        )
        upgrade_kwargs = {
            "bot_uuid": bot_uuid,
            "bot": bot,
            "owner_id": user_id,
            "request_id": request_id,
            "migration_path": mig,
            # 个人 Bot / 服务 Bot 草稿的普通重启不走发布产物迁移，但仍按 NAS home 目录运行。
            "mount_home_dir_storage": True,
            # extra_envs 可能因引擎策略门控为 None；template_config 仍需透传，
            # 以保留创建 Bot 时使用的 envs / image / resource_spec 沙箱覆写。
            "extra_envs": extra_envs,
            "template_config": device_template_config,
        }
        if restart_stage is not None:
            upgrade_kwargs["stage"] = restart_stage
        if template_uuid is not None:
            upgrade_kwargs["template_uuid"] = template_uuid
        image_policy_on_success = (
            DEFAULT_IMAGE_POLICY_VALUE
            if bot_type == "service"
            and not self.is_teclaw_bot(active_engine)
            and (bot.get("ext") or {}).get("sbot_use_default_image") is True
            else None
        )
        baas_service = self._baas_service_provider()
        try:
            workflows = baas_service.list_bot_publishes(bot_uuid)
            workflow_baseline = max(
                (
                    int(workflow["id"])
                    for workflow in (workflows or [])
                    if isinstance(workflow, dict)
                    and str(workflow.get("id", "")).isdigit()
                ),
                default=0,
            )
        except Exception as e:
            raise BotServiceError(
                f"Failed to snapshot BaaS restart workflow baseline: {e}"
            ) from e

        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
            BAAS_RESTART_PUBLISH_POLL_TASK,
            build_restart_publish_poll_payload,
        )

        # The durable task is the operation intent and must exist before the
        # external BaaS mutation. If enqueue fails, no remote side effect has
        # happened. The handler can adopt the workflow by baseline if the
        # process exits after BaaS accepts but before publish_id is persisted.
        task_queue_service = self._task_queue_service
        if task_queue_service is None:
            raise BotServiceError("BaaS restart task queue service is unavailable")
        started_at_epoch_s = time.time()
        try:
            task_queue_service.enqueue(
                BAAS_RESTART_PUBLISH_POLL_TASK,
                build_restart_publish_poll_payload(
                    binding_id=binding_id,
                    bot_id=bot_id,
                    owner_id=user_id,
                    publish_id=None,
                    started_at_epoch_s=started_at_epoch_s,
                    bot_uuid=bot_uuid,
                    image_policy_on_success=image_policy_on_success,
                    request_id=request_id,
                    workflow_baseline=workflow_baseline,
                ),
                deadline_seconds=86400,
                delay_seconds=2,
            )
        except Exception as e:
            raise BotServiceError(
                "BaaS restart was not submitted because its durable task "
                f"could not be persisted: {e}"
            ) from e

        previous_binding_status = (
            binding.get("status")
            if isinstance(binding, dict)
            else getattr(binding, "status", DeviceBindingStatus.ACTIVE.value)
        )
        previous_bot_status = bot.get("status") or DeviceBindingStatus.ACTIVE.value
        try:
            self._device_binding_repo.update_device_props(
                binding_id=binding_id,
                props={
                    RESTART_REQUEST_ID_KEY: request_id,
                    RESTART_WORKFLOW_BASELINE_KEY: workflow_baseline,
                    "restart_publish_id": None,
                    RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: image_policy_on_success,
                    "envs": extra_envs,
                },
            )
            self._device_binding_repo.update_status(
                binding_id=binding_id, status=DeviceBindingStatus.PENDING
            )
            if self._repository.update_by_owner(
                bot_id, user_id, {"status": DeviceBindingStatus.PENDING.value}
            ) is None:
                raise BotServiceError(f"Bot not found while preparing restart: {bot_id}")
        except Exception as e:
            # No BaaS call has happened yet. Invalidate the queued task's request
            # identity and restore the previous visible lifecycle state.
            try:
                self._device_binding_repo.update_device_props(
                    binding_id=binding_id,
                    props={
                        RESTART_REQUEST_ID_KEY: None,
                        RESTART_WORKFLOW_BASELINE_KEY: None,
                        RESTART_IMAGE_POLICY_ON_SUCCESS_KEY: None,
                    },
                )
                self._device_binding_repo.update_status(
                    binding_id=binding_id,
                    status=previous_binding_status or DeviceBindingStatus.ACTIVE.value,
                )
                self._repository.update_by_owner(
                    bot_id, user_id, {"status": previous_bot_status}
                )
            except Exception:
                logger.exception(
                    "[bot_service._restart_bot_baas] failed to roll back restart preparation"
                )
            raise BotServiceError(f"Failed to prepare durable BaaS restart: {e}") from e

        # From this point on, every ambiguous failure is recoverable by the
        # pre-existing task. It either reads the stored publish id or adopts the
        # single workflow issued after workflow_baseline.
        result = baas_service.upgrade_bot(**upgrade_kwargs)
        publish_id = (result or {}).get("publish_id") if isinstance(result, dict) else None
        if publish_id is not None:
            restart_publish_id = str(publish_id)
            try:
                self._device_binding_repo.update_device_props(
                    binding_id=binding_id,
                    props={
                        "publish_id": restart_publish_id,
                        "restart_publish_id": restart_publish_id,
                    },
                )
            except Exception:
                logger.exception(
                    "[bot_service._restart_bot_baas] publish_id persistence failed; "
                    "durable task will adopt by baseline: bot_id=%s publish_id=%s",
                    bot_id,
                    publish_id,
                )

        return dict(self._repository.get_by_id_and_owner(bot_id, user_id) or {})
