"""Eval-environment publish/teardown + status query, mixed in."""
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


class EvalPublishMixin:
    """Eval-environment publish/teardown + status query, mixed in."""

    async def general_publish(
        self,
        publish_id: int,
        operator: str,
        publish_stage: PublishStage = PublishStage.EVAL,
        biz_id: str = "",
    ) -> dict:
        """发布评估环境。

        评估环境是主发布流程之外的旁支能力：
        - 不推进主发布单状态机
        - 不写入 publish.ext / binding
        - 仅复用发布单上的构建产物与 bot 基础信息
        """
        logger.info(
            f"[PublishFlowService.general_publish] Start release: "
            f"publish_id={publish_id}, operator={operator}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        owner_id = self._get_owner_id(publish_record)
        migration_path = (publish_record.ext or {}).get("migration_path", "")
        config_artifact = (publish_record.ext or {}).get("config_artifact")
        if not migration_path and not config_artifact:
            raise PublishFlowServiceError("构建产物路径不存在，请先执行构建")

        bot = self._bot_service.get_bot(
            bot_id=publish_record.source_bot_id,
            user_id=owner_id,
        )
        if not bot:
            raise PublishFlowServiceError(f"Bot不存在: {publish_record.source_bot_id}")

        eval_overrides = self._stage_overrides(publish_record, publish_stage)
        eval_artifact = self._artifact_for_stage(
            config_artifact,
            publish_stage,
            eval_overrides,
        )

        ext_info = {}
        if biz_id:
            ext_info["biz_id"] = biz_id

        # 发布评估环境
        release_result = await self._build_service.release_async(
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count=1,
            publish_stage=publish_stage,
            version=str(publish_record.version or 1),
            config_artifact=eval_artifact,
            ext_info=ext_info,
        )

        bot_uuid = release_result.get("bot_uuid")
        baas_publish_id = release_result.get("publish_id")
        if not bot_uuid:
            raise PublishFlowServiceError("评估环境发布失败: BaaS 未返回 bot_uuid")

        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage=publish_stage.value,
        )
        self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=publish_stage,
            request_id=request_id,
        )

        result = {
            "success": True,
            "publish_id": publish_id,
            "stage": publish_stage.value,
            "bot_uuid": bot_uuid,
            "baas_publish_id": baas_publish_id,
            "baas_bot_status": release_result.get("status"),
        }
        logger.info(
            f"[PublishFlowService.general_publish] Release success: {result}"
        )
        return result

    def general_teardown(
        self,
        bot_uuid: str,
        *,
        operator: str = "system",
        request_bot: dict | None = None,
    ) -> dict:
        """销毁评估环境。

        仅依赖评估环境自身 bot_uuid；调用方后续可改为从独立评估任务表读取并传入。
        不触碰主发布单 ext/binding。
        """
        if not bot_uuid:
            raise PublishFlowServiceError("bot_uuid 不能为空")

        request_bot = request_bot or {"bot_id": bot_uuid}
        request_id = self._build_service.generate_request_id(
            bot=request_bot,
            publish_stage="destroy_eval",
        )
        destroy_result = self._baas_service.destroy_bot(
            bot_uuid=bot_uuid,
            operator=operator,
            request_id=request_id,
        )
        destroy_publish_id = destroy_result.get("publish_id")
        self._approve_baas_publish(
            baas_publish_id=destroy_publish_id,
            operator=operator,
            stage=PublishStage.EVAL,
            request_id=request_id,
        )
        result = {
            "success": True,
            "bot_uuid": bot_uuid,
            "baas_publish_id": destroy_publish_id,
            "message": "评估环境销毁已提交",
        }
        logger.info(
            f"[PublishFlowService.general_teardown] Destroy success: {result}"
        )
        return result

    def get_publish_bot_status(
        self,
        publish_id: int,
        stage: PublishStage,
    ) -> Dict[str, Any]:
        """查询指定发布单阶段对应的 BaaS bot 状态 / 详情。"""
        logger.info(
            f"[PublishFlowService.get_publish_bot_status] Start query: publish_id={publish_id}, stage={stage.value}"
        )

        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        ext = publish_record.ext or {}
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)
        if not binding_id:
            raise PublishFlowServiceError(f"未找到 {stage.value} 阶段的绑定信息")

        binding = self._publish_service.get_device_binding_by_id(binding_id)
        if not binding:
            raise PublishFlowServiceError(f"未找到绑定记录: binding_id={binding_id}")

        bot_uuid = getattr(binding, "device_id", "")
        if not bot_uuid:
            raise PublishFlowServiceError(f"绑定记录缺少 bot_uuid: binding_id={binding_id}")

        baas_bot = self._baas_service.get_bot(bot_uuid=bot_uuid)
        result = {
            "publish_id": publish_id,
            "stage": stage.value,
            "binding_id": binding_id,
            "bot_uuid": bot_uuid,
            "baas_bot_status": baas_bot.get("status"),
        }
        logger.info(
            f"[PublishFlowService.get_publish_bot_status] Query success: {result}"
        )
        return result

    def destroy_publish_history(
        self,
        publish_id: int,
        stage: PublishStage,
    ) -> dict:
        """销毁发布历史。

        注意：调用此方法前需先将 bot 推进到相应状态：
        - 验证阶段 (VERIFY): 需先回退到草稿状态 (DRAFT)
        - 线上阶段 (ONLINE): 需先推进到下线状态 (RELEASED)

        销毁流程：
        1. 通过 publish_id 查询 BotPublishRecord 记录
        2. 调用 _destroy_bot_by_stage 方法销毁指定阶段的 BaaS 层 bot

        Args:
            publish_id: AgentClaw 层发布单 ID
            stage: 发布阶段（VERIFY/ONLINE）

        Returns:
            dict: 销毁结果，包含:
                - success: 是否成功
                - bot_destroyed: 是否销毁了 bot
                - message: 结果消息

        Raises:
            PublishNotFoundError: 发布单不存在
            PublishFlowServiceError: 销毁失败
        """
        logger.info(f"[PublishFlowService.destroy_publish_history] Starting destroy: publish_id={publish_id}, stage={stage.value}")

        # Step 1: 查询发布单
        publish_record = self._publish_service.get_publish_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        current_status = PublishStatus(publish_record.status)

        # 检查状态：只有 DRAFT 和 RELEASED 状态才能销毁
        allowed_statuses = [PublishStatus.DRAFT, PublishStatus.RELEASED]
        if current_status not in allowed_statuses:
            raise PublishFlowServiceError(
                f"发布单状态不允许销毁: 当前状态={current_status}, "
                f"仅允许状态: {[s.value for s in allowed_statuses]}"
            )

        result = {
            "success": True,
            "bot_destroyed": False,
            "message": "",
        }

        # Step 2: 销毁 BaaS 层的 bot
        try:
            self._destroy_bot_by_stage(publish_record, stage)
            result["bot_destroyed"] = True
            logger.info(
                f"[PublishFlowService.destroy_publish_history] "
                f"Bot destroyed: publish_id={publish_id}, stage={stage.value}"
            )

        except Exception as e:
            logger.warning(
                f"[PublishFlowService.destroy_publish_history]"
                f"Failed to destroy BaaS bots: publish_id={publish_id}, stage={stage.value}, error={e}"
            )
            # 销毁 bot 失败不阻塞整体流程

        result["message"] = f"发布历史销毁完成: publish_id={publish_id}, stage={stage.value}"

        logger.info(f"[PublishFlowService.destroy_publish_history] Destroy completed: {result}")

        return result
