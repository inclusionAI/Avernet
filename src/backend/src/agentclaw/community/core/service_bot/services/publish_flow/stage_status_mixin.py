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
        """Determine the restart stage based on the current status.

        Args:
            current_status: Current publish record status

        Returns:
            Publish stage (VERIFY/ONLINE), or None if restart is not supported
        """
        # VALIDATING / VALIDATE_PUB: verify environment, restart the bot in the verify environment
        if current_status in (PublishStatus.VALIDATING, PublishStatus.VALIDATE_PUB):
            return PublishStage.VERIFY
        # SUCCESS / ONLINE_PUB: online environment, restart the online bot
        elif current_status in (PublishStatus.SUCCESS, PublishStatus.ONLINE_PUB):
            return PublishStage.ONLINE
        return None

    def _determine_sync_stage(self, current_status: PublishStatus) -> PublishStage | None:
        """Determine the sync stage based on the current status.

        Args:
            current_status: Current publish record status

        Returns:
            Publish stage (VERIFY/ONLINE), or None if sync is not supported
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
        """Update the previous publish record status to UPGRADED (only when the online stage succeeds).

        Args:
            publish_record: Current publish record
            stage: Publish stage (VERIFY/ONLINE)
            target_status: Target status
        """
        # Only update the previous publish record when the online stage succeeds
        if stage != PublishStage.ONLINE or target_status != PublishStatus.SUCCESS:
            return

        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return

        # Query the previous publish record
        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            logger.warning(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Last publish record not found: last_pub_id={last_pub_id}"
            )
            return

        # Clear the rollback_restored_from marker (if present)
        last_ext = last_publish.ext or {}
        if last_ext.pop("rollback_restored_from", None):
            logger.info(
                f"[PublishFlowService._mark_previous_publish_superseded] "
                f"Clearing rollback_restored_from for publish {last_pub_id}"
            )

        # Update the previous publish record status to UPGRADED, and update ext at the same time
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

