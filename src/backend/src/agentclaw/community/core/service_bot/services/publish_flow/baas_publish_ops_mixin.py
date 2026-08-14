"""Shared BaaS-layer publish ops, mixed in."""
from __future__ import annotations

from typing import Any, Dict

from agentclaw.community.log import get_logger

logger = get_logger()


class BaasPublishOpsMixin:
    """Shared BaaS-layer publish progress query, mixed in.

    Owns the BaaS publish-workflow progress query. Under all-auto approval
    (#197) there is no client-side approve step — every mutation is
    auto-approved server-side — so the former ``approve_baas_publish`` is
    removed. Creating the device binding and writing the publish record's
    ext/status are separate concerns (``DeviceBindingMixin`` /
    ``PublishExtMixin``).
    """

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
