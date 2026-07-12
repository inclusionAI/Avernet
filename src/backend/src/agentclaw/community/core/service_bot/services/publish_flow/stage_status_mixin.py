"""Stage-from-status helpers + previous-publish supersede, mixed in."""
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


class StageStatusMixin:
    """Stage-from-status helpers + previous-publish supersede, mixed in."""

    def _determine_restart_stage(self, current_status: PublishStatus) -> PublishStage | None:
        """根据当前状态确定重启阶段。

        Args:
            current_status: 当前发布单状态

        Returns:
            发布阶段（VERIFY/ONLINE），如果不支持重启则返回 None
        """
        # VALIDATING / VALIDATE_PUB: 验证环境，重启验证环境的 bot
        if current_status in (PublishStatus.VALIDATING, PublishStatus.VALIDATE_PUB):
            return PublishStage.VERIFY
        # SUCCESS / ONLINE_PUB: 线上环境，重启线上的 bot
        elif current_status in (PublishStatus.SUCCESS, PublishStatus.ONLINE_PUB):
            return PublishStage.ONLINE
        return None

    def _determine_sync_stage(self, current_status: PublishStatus) -> PublishStage | None:
        """根据当前状态确定同步阶段。

        Args:
            current_status: 当前发布单状态

        Returns:
            发布阶段（VERIFY/ONLINE），如果不支持同步则返回 None
        """
        if current_status == PublishStatus.VALIDATE_PUB:
            return PublishStage.VERIFY
        elif current_status == PublishStatus.ONLINE_PUB:
            return PublishStage.ONLINE
        return None

    def _mark_previous_publish_superseded(
        self,
        publish_record: BotPublishRecord,
        stage: PublishStage,
        target_status: PublishStatus,
    ) -> None:
        """更新上一个发布单状态为 UPGRADED（仅当 online 阶段成功时）。

        Args:
            publish_record: 当前发布单记录
            stage: 发布阶段（VERIFY/ONLINE）
            target_status: 目标状态
        """
        # 只有 online 阶段成功时才需要更新上一个发布单
        if stage != PublishStage.ONLINE or target_status != PublishStatus.SUCCESS:
            return

        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return

        # 查询上一个发布单记录
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            logger.warning(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Last publish record not found: last_pub_id={last_pub_id}"
            )
            return

        # 清除 rollback_restored_from 标记（如果存在）
        last_ext = last_publish.ext or {}
        if last_ext.pop("rollback_restored_from", None):
            logger.info(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Clearing rollback_restored_from for publish {last_pub_id}"
            )

        # 更新上一个发布单状态为 UPGRADED，同时更新 ext
        try:
            self._publish_service.update_publish_status_with_ext(
                publish_id=last_pub_id,
                target_status=PublishStatus.UPGRADED,
                ext=last_ext,
                source_status=PublishStatus.SUCCESS,
            )
            logger.info(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Last publish status updated to UPGRADED: last_pub_id={last_pub_id}"
            )
        except Exception as e:
            logger.warning(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Failed to update last publish status: last_pub_id={last_pub_id}, error={e}"
            )

