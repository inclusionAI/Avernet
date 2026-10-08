"""Engine-neutral preparation shared by immediate and deferred BaaS restarts.

The prepared request is process-local: it may contain credentials and MUST NOT
be serialized into a durable task. Deferred workers rebuild it from the existing
configuration repositories and revalidate before submission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from agentclaw.community.core.bot_management.services.default_image_policy_listener import (
    DEFAULT_IMAGE_POLICY_VALUE,
)
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    overlay_image_pin_on_template_config,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass(frozen=True)
class BaasRestartInProgress:
    bot: dict


@dataclass(frozen=True)
class PreparedBaasRestart:
    binding: Any
    bot_uuid: str
    request_id: str
    workflow_baseline: int
    upgrade_kwargs: dict
    image_policy_on_success: str | None


class BaasRestartPreparationMixin:
    def _preflight_restart(self, *, bot: dict, user_id: str):
        """Validate a deferred submission before admission changes public status.

        No provider intent, poll task, PENDING write or remote update is issued.
        Other providers keep their existing admission path.
        """
        binding = bot.get("device_binding") or {}
        if binding.get("device_provider") != "baas" or bot.get("binding_id") is None:
            return None
        prepared = self._prepare_baas_restart(
            bot_id=bot["bot_id"],
            user_id=user_id,
            binding_id=bot["binding_id"],
            bot=bot,
        )
        return prepared.bot if isinstance(prepared, BaasRestartInProgress) else None

    def _prepare_baas_restart(
        self, *, bot_id: str, user_id: str, binding_id: int, bot: Dict[str, Any]
    ):
        from .bot_service import BotServiceError

        binding = self._device_service_provider().get_device(binding_id=binding_id)
        bot_uuid = (
            binding.get("device_id")
            if isinstance(binding, dict)
            else getattr(binding, "device_id", None)
        )
        if not bot_uuid:
            raise BotServiceError(
                f"Bot {bot_id} binding {binding_id} missing bot_uuid; cannot baas restart"
            )

        from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
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
            return BaasRestartInProgress(
                dict(self._repository.get_by_id_and_owner(bot_id, user_id) or bot)
            )
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

        # 先读取模板快照：BCN 能力门控与后续 BaaS restart 均使用同一份 resolved config。
        try:
            resolved_template_config = self._template_service.get_template_config(
                bot_id
            )
        except Exception as e:
            logger.warning(
                "[bot_service._restart_bot_baas] Failed to get template for bot %s: %s",
                bot_id,
                e,
            )
            resolved_template_config = None

        # BaaS 原地重启不会经过 start_bot，这里补齐启动链路的 BCN Provider 注册。
        # 注册接口幂等：已注册时直接返回，也能重试创建阶段失败的注册。
        should_register_bcn = self._should_register_bcn_provider(
            active_engine=active_engine,
            bot_type=bot_type,
            template_type=bot_template_type,
            template_config=resolved_template_config,
        )
        connection_mode = self._resolve_bcn_provider_connection_mode(
            active_engine=active_engine,
            bot_type=bot_type,
        )
        if should_register_bcn:
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
            )

        # 普通 restart 入口只重启当前 bot，不使用发布态 build 产物目录。
        # 发布态 verify/online 的重启由 PublishFlowService.restart_bot(publish_id) 处理。
        mig: Optional[str] = None
        restart_stage = PublishStage.DRAFT.value if bot_type == "service" else None

        # resolved_template_config 已在 BCN 能力门控前读取，后续 BaaS restart 复用同一快照。
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
                bot_id,
                e,
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

        return PreparedBaasRestart(
            binding=binding,
            bot_uuid=bot_uuid,
            request_id=request_id,
            workflow_baseline=workflow_baseline,
            upgrade_kwargs=upgrade_kwargs,
            image_policy_on_success=image_policy_on_success,
        )
