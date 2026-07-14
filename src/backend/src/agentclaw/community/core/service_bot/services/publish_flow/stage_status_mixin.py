"""Stage-from-status helpers, mixed in.

Pure derivations (no persistence): map a publish status to the stage a restart
or progress sync applies to. The previous-publish supersede write moved to
``PublishExtMixin`` (it is a publish-record status+ext write).
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class StageStatusMixin:
    """Stage-from-status helpers, mixed in."""

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
