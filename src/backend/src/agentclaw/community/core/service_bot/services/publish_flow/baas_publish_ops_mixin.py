"""Shared BaaS publish approval, mixed in."""
from __future__ import annotations

from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class BaasPublishOpsMixin:
    """Shared BaaS publish approval, mixed in.

    This mixin owns only the BaaS-layer approve call. Creating the device binding
    and writing the publish record's ext/status are separate concerns that live on
    the facade (``create_release_binding`` / ``record_release_ext``); the release
    runner invokes the three steps in sequence.
    """

    def approve_baas_publish(
        self,
        baas_publish_id: int,
        operator: str,
        stage: PublishStage,
        request_id: str,
    ) -> bool:
        """Approve the BaaS-layer publish workflow.

        Args:
            baas_publish_id: BaaS publish workflow id (the caller guarantees a real
                id — the release/upgrade paths raise if BaaS returned none).
            operator: Approving operator user id.
            stage: Publish stage, for log disambiguation.
            request_id: Request id, for idempotency control.
        """
        logger.info(
            f"[PublishFlowService.approve_baas_publish] "
            f"Approving BaaS publish: baas_publish_id={baas_publish_id}, stage={stage.value}"
        )

        try:
            self._baas_service.approve_publish(
                publish_id=baas_publish_id,
                operator=operator,
                request_id=request_id,
                comment=f"Auto-approve - {stage.value} stage publish",
            )
            logger.info(
                f"[PublishFlowService.approve_baas_publish] "
                f"BaaS publish approved: baas_publish_id={baas_publish_id}, stage={stage.value}"
            )
            return True
        except Exception as e:
            logger.warning(
                f"[PublishFlowService.approve_baas_publish] "
                f"Failed to approve BaaS publish: baas_publish_id={baas_publish_id}, "
                f"stage={stage.value}, error={e}, continuing..."
            )
            return False
