"""Shared BaaS-layer publish ops, mixed in."""
from __future__ import annotations

from typing import Any, Dict

from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class BaasPublishOpsMixin:
    """Shared BaaS-layer publish ops, mixed in.

    Owns the calls against the BaaS publish workflow — approve and progress
    query. Creating the device binding and writing the publish record's
    ext/status are separate concerns that live on ``DeviceBindingMixin`` /
    ``PublishExtMixin``; the release runner invokes the steps in sequence.
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

    def get_baas_publish_progress(
        self,
        *,
        baas_publish_id: int,
        include_devices: bool = True,
    ) -> Dict[str, Any]:
        """Query BaaS publish progress."""
        logger.info(
            f"[PublishFlowService.get_baas_publish_progress] Query BaaS progress: "
            f"baas_publish_id={baas_publish_id}, include_devices={include_devices}"
        )
        try:
            return self._baas_service.get_publish_progress(
                publish_id=int(baas_publish_id),
                include_devices=include_devices,
            )
        except Exception as e:
            logger.error(
                f"[PublishFlowService.get_baas_publish_progress] "
                f"Failed to get BaaS publish progress: baas_publish_id={baas_publish_id}, "
                f"error={e}"
            )
            raise
