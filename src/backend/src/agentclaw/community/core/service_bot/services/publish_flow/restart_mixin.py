"""Bot restart (re-deploy) operations, mixed into PublishFlowService."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

logger = get_logger()

DEVICE_COUNT_CONFIG_BUSINESS_CODE = "service_bot_device_count"
DEVICE_COUNT_DEFAULT_PARAM_CODE = "default"


class RestartMixin:
    """Bot restart (re-deploy) operations, mixed into PublishFlowService."""

    def restart_bot(
        self,
        publish_id: int,
        operator: str = "system",
    ) -> dict:
        """重启 Bot（异步执行）。

        根据发布单状态确定当前阶段，从 binding 信息获取 bot_uuid，调用 BaaS 层 upgrade 接口重新部署。
        该方法使用 asyncio.create_task 异步执行，不等待结果。

        流程：
        1. 根据 publish_id 查询发布单记录
        2. 根据发布单状态确定当前阶段（VERIFY/ONLINE）
        3. 从 ext 获取对应阶段的 binding_id
        4. 根据 binding_id 查询 device_binding 记录，获取 device_id（即 bot_uuid）
        5. 调用 BotBuildService.upgrade_async() 重新部署 Bot

        Args:
            publish_id: 发布单 ID
            operator: 操作者，默认为 "system"

        Returns:
            dict: 重启结果，包含:
                - success: 是否成功提交重启请求
                - message: 结果消息
                - stage: 发布阶段（成功时返回）
        """
        logger.info(
            f"[PublishFlowService.restart_bot] called: publish_id={publish_id}, operator={operator}"
        )

        # Step 1: 查询发布单记录
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            logger.warning(
                f"[PublishFlowService.restart_bot] Publish record not found: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"发布单不存在: publish_id={publish_id}",
            }

        # Step 2: 根据状态确定当前阶段
        current_status = PublishStatus(publish_record.status)
        stage = self._determine_restart_stage(current_status)
        if not stage:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"Cannot restart for status: {current_status}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"当前状态 {current_status} 不支持重启操作",
                "status": current_status,
            }

        # Step 3: 重置当前阶段的BaaS重启发布单
        ext = self._get_latest_ext(publish_id)
        if "restart" in ext and stage.value in ext.get("restart", {}):
            try:
                def _mutate(latest_ext: dict) -> None:
                    restart_info = latest_ext.get("restart", {})
                    if stage.value in restart_info:
                        restart_info.pop(stage.value, None)
                    if restart_info:
                        latest_ext["restart"] = restart_info
                    else:
                        latest_ext.pop("restart", None)

                ext = self._merge_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
            except Exception as e:
                logger.warning(
                    f"[PublishFlowService.restart_bot] "
                    f"Failed to reset restart_publish_id: publish_id={publish_id}, error={e}"
                )
                ext = self._get_latest_ext(publish_id)

        # Step 4: 从 ext 获取对应阶段的 binding_id
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService.restart_bot] "
                f"No binding_id found for stage={stage.value}, publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"未找到 {stage.value} 阶段的绑定信息",
                "stage": stage.value,
            }

        # Step 5: 根据 binding_id 查询 device_binding 记录
        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            logger.warning(
                f"[PublishFlowService.restart_bot] Device binding not found: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"设备绑定记录不存在: binding_id={binding_id}",
            }

        bot_uuid = binding.device_id
        if not bot_uuid:
            logger.warning(
                f"[PublishFlowService.restart_bot] No device_id in binding: binding_id={binding_id}"
            )
            return {
                "success": False,
                "message": f"设备绑定记录中没有 device_id: binding_id={binding_id}",
            }

        bot_service = self._bot_service
        bot = bot_service.get_bot(bot_id=publish_record.source_bot_id, user_id=publish_record.owner_id)
        if not bot:
            logger.warning(
                f"[PublishFlowService.restart_bot] Bot not found: bot_id={publish_record.source_bot_id}"
            )
            return {
                "success": False,
                "message": f"Bot不存在: {publish_record.source_bot_id}",
            }

        migration_path = ext.get("migration_path")
        config_artifact = ext.get("config_artifact")
        if not migration_path and not config_artifact:
            logger.warning(
                f"[PublishFlowService.restart_bot] No build artifact in ext: publish_id={publish_id}"
            )
            return {
                "success": False,
                "message": f"发布单缺少构建产物: publish_id={publish_id}",
            }

        # Step 6: 异步执行重启
        asyncio.create_task(
            self._restart_bot_async(
                publish_id=publish_id,
                publish_record=publish_record,
                migration_path=migration_path,
                bot_uuid=bot_uuid,
                binding_id=binding_id,
                bot=bot,
                stage=stage,
                operator=operator,
            )
        )

        logger.info(
            f"[PublishFlowService.restart_bot] Restart task created: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}, owner_id={publish_record.owner_id}"
        )

        return {
            "success": True,
            "message": f"重启任务已提交，阶段: {stage.value}",
            "stage": stage.value,
            "bot_uuid": bot_uuid,
        }

    async def _restart_bot_async(
        self,
        publish_id: int,
        publish_record: BotPublishRecord,
        migration_path: str,
        bot_uuid: str,
        binding_id: int,
        bot: Dict[str, Any],
        stage: PublishStage,
        operator: str,
    ) -> None:
        """异步执行 Bot 重启（通过 upgrade 接口重新部署）。

        Args:
            publish_id: 发布单 ID
            publish_record: 发布单记录
            migration_path: Bot 实例迁移后的目录路径
            bot_uuid: Bot UUID
            bot: Bot 信息字典
            stage: 发布阶段
            operator: 操作者
        """
        logger.info(
            f"[PublishFlowService._restart_bot_async] Starting restart: "
            f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}, "
            f"operator={operator}, owner_id={publish_record.owner_id}"
        )

        try:
            # 生成 request_id（用于后续审批 BaaS 发布单）
            request_id = self._build_service.generate_request_id(
                bot=bot,
                publish_stage=f"restart_{stage.value}",
            )
            version = f"{publish_record.version}"

            # Compose the delivery artifact for THIS stage: stamp engine_ext.stage
            # to the restarted stage and overlay that stage's stored channel
            # engine_overrides (reproducing what was promoted, NOT a live re-fetch).
            # Reading the per-stage slot fixes the single-config-slot hazard — a
            # restart of a non-latest stage no longer delivers another stage's
            # channels. No stored overrides (pre-feature record) or no
            # config_artifact (ARCA) → no-ops, preserving prior behavior.
            ext = publish_record.ext or {}
            # `or {}` (not a get-default): the key may hold JSON null in a raw ext blob.
            stored_overrides = (ext.get("engine_overrides_by_stage") or {}).get(stage.value)
            config_artifact = self._artifact_for_stage(
                ext.get("config_artifact"), stage, stored_overrides
            )
            restart_result = await self._build_service.upgrade_async(
                bot_uuid=bot_uuid,
                bot=bot,
                user_id=publish_record.owner_id,
                device_count=1,
                migration_path=migration_path,
                publish_stage=stage,
                version=version,
                config_artifact=config_artifact,
            )

            if restart_result.get("success") is False and restart_result.get("error_code") == "BOT_NOT_FOUND":
                logger.warning(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Restart target bot not found, fallback to first release: "
                    f"publish_id={publish_id}, bot_uuid={bot_uuid}, stage={stage.value}"
                )
                restart_result = await self._build_service.release_async(
                    bot=bot,
                    user_id=publish_record.owner_id,
                    migration_path=migration_path,
                    device_count=1,
                    publish_stage=stage,
                    version=version,
                    # teclaw: the fallback must carry the frozen artifact too,
                    # else create_teclaw_bot would receive an empty config.
                    config_artifact=config_artifact,
                )

            restart_publish_id = restart_result.get("publish_id")
            if not restart_publish_id:
                raise PublishFlowServiceError("BaaS 层重启未返回 publish_id")

            # Refresh the reused binding's teclaw status read handle to the
            # restart's publish workflow (no-op for non-teclaw; best-effort).
            self._refresh_publish_handle(
                binding_id, restart_publish_id
            )

            logger.info(
                f"[PublishFlowService._restart_bot_async] "
                f"Bot restart initiated: bot_uuid={bot_uuid}, stage={stage.value}, "
                f"publish_id={publish_id}, restart_publish_id={restart_publish_id}"
            )

            # 将 restart_publish_id 存入 ext: {"restart": {"<stage>": restart_publish_id}}
            try:
                def _mutate(ext: dict) -> None:
                    if "restart" not in ext:
                        ext["restart"] = {}
                    ext["restart"][stage.value] = restart_publish_id

                self._merge_and_update_ext(
                    publish_id=publish_id,
                    mutator=_mutate,
                )
                logger.info(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Restart publish_id saved to ext: publish_id={publish_id}, "
                    f"stage={stage.value}, restart_publish_id={restart_publish_id}"
                )
            except Exception as save_error:
                logger.warning(
                    f"[PublishFlowService._restart_bot_async] "
                    f"Failed to save restart_publish_id to ext: publish_id={publish_id}, "
                    f"error={save_error}"
                )

            # 审批 BaaS 层重启发布单
            self._approve_baas_publish(
                baas_publish_id=restart_publish_id,
                operator=operator,
                stage=stage,
                request_id=request_id,
            )
            logger.info(
                f"[PublishFlowService._restart_bot_async] "
                f"Bot restart approved: bot_uuid={bot_uuid}, stage={stage.value}, "
                f"restart_publish_id={restart_publish_id}"
            )

        except Exception as e:
            logger.error(
                f"[PublishFlowService._restart_bot_async] "
                f"Failed to restart bot: publish_id={publish_id}, bot_uuid={bot_uuid}, error={e}"
            )

