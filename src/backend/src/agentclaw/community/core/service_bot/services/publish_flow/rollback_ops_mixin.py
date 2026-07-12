"""Rollback deploy + BaaS bot teardown, mixed into PublishFlowService."""
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


class RollbackOpsMixin:
    """Rollback deploy + BaaS bot teardown, mixed into PublishFlowService."""

    async def execute_rollback(
        self,
        current_publish_id: int,
        target_publish_id: int,
        operator: str,
    ) -> "PublishFlowResult":
        """执行回滚部署。

        使用目标版本的配置（migration_path/config_artifact/binding）重新部署到线上。
        回滚部署在目标版本上进行，前端应同步 target_publish_id 的部署进度。

        Args:
            current_publish_id: 当前版本 ID（已变为 DRAFT，仅用于获取 owner_id 和 bot_id）
            target_publish_id: 目标版本 ID（回滚目标，已变为 SUCCESS，部署在此版本上）
            operator: 操作者

        Returns:
            PublishFlowResult: 部署结果，publish_id 为 target_publish_id
        """
        from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult

        logger.info(
            f"[PublishFlowService.execute_rollback] called: "
            f"current_publish_id={current_publish_id}, target_publish_id={target_publish_id}, "
            f"operator={operator}"
        )

        # 1. 获取目标版本记录
        target_record = self._publish_service.get_publish_by_id(target_publish_id)
        if not target_record:
            raise PublishNotFoundError(f"Target publish record not found: {target_publish_id}")

        current_record = self._publish_service.get_publish_by_id(current_publish_id)
        if not current_record:
            raise PublishNotFoundError(f"Current publish record not found: {current_publish_id}")

        # 2. 获取目标版本的构建产物
        target_ext = self._get_latest_ext(target_publish_id)
        migration_path = target_ext.get("migration_path")
        config_artifact = target_ext.get("config_artifact")

        if not migration_path and not config_artifact:
            raise PublishFlowServiceError(
                f"目标版本缺少构建产物: target_publish_id={target_publish_id}"
            )

        # 3. 从目标版本获取线上 binding
        online_binding_id = target_ext.get("binding", {}).get(PublishStage.ONLINE.value)
        if not online_binding_id:
            raise PublishFlowServiceError(
                f"目标版本缺少线上 binding: target_publish_id={target_publish_id}"
            )

        binding = self._publish_service.get_device_binding_by_id(online_binding_id)
        if not binding or not binding.device_id:
            raise PublishFlowServiceError(
                f"设备绑定记录不存在或缺少 device_id: binding_id={online_binding_id}"
            )

        bot_uuid = binding.device_id

        # 4. 获取 Bot 信息（从 current_record 获取，因为它是原始发布单）
        owner_id = current_record.owner_id
        bot = self._bot_service.get_bot(bot_id=current_record.source_bot_id, user_id=owner_id)
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {current_record.source_bot_id}")

        # 5. 调用 BaaS upgrade 接口重新部署
        version = f"{target_record.version}"
        upgrade_result = await self._build_service.upgrade_async(
            bot_uuid=bot_uuid,
            bot=bot,
            user_id=owner_id,
            device_count=1,
            migration_path=migration_path,
            publish_stage=PublishStage.ONLINE,
            version=version,
            config_artifact=config_artifact,
        )

        baas_publish_id = upgrade_result.get("publish_id")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS 层升级未返回 publish_id")

        # 6. 更新目标版本记录新的 BaaS 发布单 ID（回滚部署在目标版本上进行）
        if "publish" not in target_ext:
            target_ext["publish"] = {}
        target_ext["publish"][PublishStage.ONLINE.value] = baas_publish_id

        self._update_publish_status(
            publish_id=target_publish_id,
            target_status=PublishStatus.ONLINE_PUB,
            source_status=PublishStatus.SUCCESS,
            ext=target_ext,
        )

        # 7. 审批 BaaS 发布单
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage="rollback",
        )
        self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=PublishStage.ONLINE,
            request_id=request_id,
        )

        logger.info(
            f"[PublishFlowService.execute_rollback] Rollback deployment initiated: "
            f"current_publish_id={current_publish_id}, target_publish_id={target_publish_id}, "
            f"bot_uuid={bot_uuid}, baas_publish_id={baas_publish_id}"
        )

        return PublishFlowResult(
            publish_id=target_publish_id,
            status=PublishStatus.ONLINE_PUB,
            message="回滚发布已提交",
            action="rollback",
            target_publish_id=target_publish_id,
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id),
            device_binding_id=online_binding_id,
        )

    def _destroy_bot_by_stage(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
    ) -> None:
        """销毁指定阶段的 bot 实例。

        Args:
            publish_record: 发布单记录
            stage: 发布阶段（VERIFY/ONLINE）
        """
        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            logger.warning(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"No binding_id found for stage={stage.value}, skipping destroy"
            )
            return

        try:
            # 查询 device_binding 获取 bot_uuid
            binding = self._publish_service.get_device_binding_by_id(binding_id)
            if not binding:
                logger.warning(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"Device binding not found: binding_id={binding_id}"
                )
                return

            bot_uuid = binding.device_id
            if not bot_uuid:
                logger.warning(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"No device_id in binding: binding_id={binding_id}"
                )
                return

            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Destroying bot: bot_uuid={bot_uuid}, stage={stage.value}"
            )

            # 生成 request_id（销毁场景使用特殊标识）
            request_id = self._build_service.generate_request_id(
                bot={"entity_id": binding.entity_id, "entity_type": binding.entity_type, "bot_id": publish_record.source_bot_id},
                publish_stage=f"destroy_{stage.value}",
            )

            # 调用 BaaS 销毁 bot
            destroy_result = self._baas_service.stop_bot(
                bot_uuid=bot_uuid,
                operator="system",
                request_id=request_id,
            )

            destroy_publish_id = destroy_result.get("publish_id")
            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Bot destroy initiated: bot_uuid={bot_uuid}, stage={stage.value}, destroy_publish_id={destroy_publish_id}"
            )

            # 审批销毁流程单
            if destroy_publish_id:
                self._approve_baas_publish(
                    baas_publish_id=destroy_publish_id,
                    operator="system",
                    stage=stage,
                    request_id=request_id,
                )
                logger.info(
                    f"[PublishFlowService._destroy_bot_by_stage] "
                    f"Bot destroy approved: bot_uuid={bot_uuid}, stage={stage.value}, destroy_publish_id={destroy_publish_id}"
                )

            # 更新 device_binding 状态为 RELEASED
            self._publish_service.update_device_binding_with_props(
                binding_id=binding_id,
                status=DeviceBindingStatus.RELEASED,
                device_props={"destroy_publish_id": destroy_publish_id} if destroy_publish_id else {},
            )
            logger.info(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Device binding status updated to RELEASED: binding_id={binding_id}"
            )

        except Exception as e:
            logger.warning(
                f"[PublishFlowService._destroy_bot_by_stage] "
                f"Failed to destroy bot: binding_id={binding_id}, stage={stage.value}, error={e}"
            )


