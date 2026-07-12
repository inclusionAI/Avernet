"""Shared BaaS approve + release-result recording, mixed in."""
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


class BaasPublishOpsMixin:
    """Shared BaaS approve + release-result recording, mixed in."""

    def _approve_baas_publish(
        self,
        baas_publish_id: int | str,
        operator: str,
        stage: PublishStage,
        request_id: str,
    ) -> bool:
        """审批 BaaS 层发布单。

        Args:
            baas_publish_id: BaaS 层发布工作流 ID
            operator: 审批操作者用户 ID
            stage: 发布阶段，用于日志区分
            request_id: 请求 ID，用于幂等性控制
        """
        if not baas_publish_id:
            logger.warning(
                f"[PublishFlowService._approve_baas_publish] "
                f"No baas_publish_id provided, skipping approve for stage={stage.value}"
            )
            return False

        logger.info(
            f"[PublishFlowService._approve_baas_publish] "
            f"Approving BaaS publish: baas_publish_id={baas_publish_id}, stage={stage.value}"
        )

        try:
            self._baas_service.approve_publish(
                publish_id=int(baas_publish_id),
                operator=operator,
                request_id=request_id,
                comment=f"自动审批 - {stage.value}环境发布",
            )
            logger.info(
                f"[PublishFlowService._approve_baas_publish] "
                f"BaaS publish approved: baas_publish_id={baas_publish_id}, stage={stage.value}"
            )
            return True
        except Exception as e:
            logger.warning(
                f"[PublishFlowService._approve_baas_publish] "
                f"Failed to approve BaaS publish: baas_publish_id={baas_publish_id}, "
                f"stage={stage.value}, error={e}, continuing..."
            )
            return False

    def _record_release_result(
        self,
        publish_id: int,
        bot: dict,
        bot_uuid: str,
        baas_publish_id: int | str,
        operator: str,
        ext: dict,
        stage: PublishStage,
        source_status: PublishStatus,
        target_status: PublishStatus,
        engine_overrides: dict | None = None,
    ) -> tuple[int, dict]:
        """记录发布结果：创建 device binding 并更新扩展字段。

        Args:
            publish_id: 发布单 ID
            bot: Bot 信息字典
            bot_uuid: BaaS 层返回的 bot_uuid
            baas_publish_id: BaaS 层发布单 ID
            operator: 操作者
            ext: 扩展字段
            stage: 发布阶段（VERIFY/ONLINE）
            source_status: 源状态
            target_status: 目标状态

        Returns:
            tuple[int, dict]: (binding_id, 更新后的 ext)
        """
        # 创建 device binding 记录。device_provider 由 baas 决定（查源 bot 的容器
        # provider_type，teclaw→teclaw 否则 baas），替代原先写死的 "baas"；与 build
        # 阶段的生产者选择共用同一解析规则（同一 resolve_container_provider）。
        binding_id = self._publish_service.create_device_binding(
            entity_id=bot.get("entity_id", ""),
            entity_type=bot.get("entity_type", "staff"),
            device_id=bot_uuid,
            device_provider=self._baas_service.resolve_container_provider(bot),
            # Stash the baas publish_id as the teclaw status read handle (no-op
            # for non-teclaw bindings — their status reads ignore this key).
            device_props={"publish_id": baas_publish_id},
            applied_by=operator,
            apply_reason="",
            status=DeviceBindingStatus.PENDING,
        )

        ext = self._get_latest_ext(publish_id)

        # 记录 binding_id 到扩展字段
        if "binding" not in ext:
            ext["binding"] = {}
        ext["binding"][stage.value] = binding_id

        # 记录 baas 层发布单 ID 到扩展字段
        if "publish" not in ext:
            ext["publish"] = {}
        ext["publish"][stage.value] = baas_publish_id

        # 把本阶段（canary/release）持久化进存储的 config_artifact 快照。本方法在内部
        # 重新读取了 ext（覆盖调用方的修改），所以首次发布路径的 stage 持久化必须落在
        # 这里；ARCA 无 config_artifact 时 no-op。
        self._restamp_ext_artifact(ext, stage)

        # Persist this stage's engine_overrides (DingTalk channels) next to the
        # binding/publish refs. Same reason as the restamp above: this method
        # re-reads ext, so the first-release store must land here. No-op for ARCA
        # (engine_overrides is None).
        self._store_stage_overrides(ext, stage, engine_overrides)

        # 更新状态
        self._update_publish_status(
            publish_id=publish_id,
            target_status=target_status,
            source_status=source_status,
            ext=ext,
        )

        # 审批 BaaS 层发布单
        request_id = self._build_service.generate_request_id(
            bot=bot,
            publish_stage=stage.value,
        )

        self._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=stage,
            request_id=request_id,
        )

        logger.info(
            f"[PublishFlowService._record_release_result] "
            f"Release recorded: publish_id={publish_id}, stage={stage.value}, "
            f"binding_id={binding_id}, baas_publish_id={baas_publish_id}"
        )

        return binding_id, ext
