"""Device-binding ops, mixed into ``PublishFlowService``.

Owns ALL writes to the device-binding table for the publish flow — create on
release, activate on sync success, release on destroy. Feature mixins
(progress sync, rollback) orchestrate; the binding writes live here. Kept
distinct from the publish-record ext writes (``PublishExtMixin``) and the BaaS
approve (``BaasPublishOpsMixin``).
"""
from __future__ import annotations

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class DeviceBindingMixin:
    """Device-binding table writes for the publish flow."""

    def create_release_binding(
        self,
        *,
        bot: dict,
        bot_uuid: str,
        baas_publish_id: int,
        operator: str,
    ) -> int:
        """Create the device binding for a release and return its id.

        ``device_provider`` is resolved from the source bot's container (the same
        rule the build phase uses to pick a producer) rather than hardcoded; the
        baas publish_id is stashed as the teclaw status read handle (a no-op key
        for other providers)."""
        return self._publish_service.create_device_binding(
            entity_id=bot.get("entity_id", ""),
            entity_type=bot.get("entity_type", "staff"),
            device_id=bot_uuid,
            device_provider=self._baas_service.resolve_container_provider(bot),
            device_props={"publish_id": baas_publish_id},
            applied_by=operator,
            apply_reason="",
            status=DeviceBindingStatus.PENDING,
        )

    def _update_binding_on_success(
        self,
        ext: dict,
        stage: PublishStage,
        progress: dict,
        baas_status: str,
        baas_publish_id: int,
        bot_id: str,
    ) -> None:
        """Update device_binding to the success status.

        Args:
            ext: Extension fields
            stage: Publish stage (VERIFY/ONLINE)
            progress: BaaS publish progress information
            baas_status: BaaS publish status
            baas_publish_id: BaaS publish record ID
            bot_id: Bot ID
        """
        # Get binding_id from the extension fields
        binding_info = ext.get("binding", {})
        binding_id = binding_info.get(stage.value)

        if not binding_id:
            # A record that reached sync-success for this stage must have a binding
            # recorded; a missing one is an inconsistent state, not something to
            # silently skip.
            raise PublishFlowServiceError(
                f"No binding_id found for stage={stage.value} while recording sync success "
                f"(bot_id={bot_id})"
            )

        device_details = progress.get("device_details", [])
        device_props = {
            "bolt_id": bot_id,
            "device_details": device_details,
            "baas_status": baas_status,
            "baas_publish_id": baas_publish_id,
            "overall_progress": progress.get("overall_progress", {}),
        }

        self._publish_service.update_device_binding_with_props(
            binding_id=binding_id,
            status=DeviceBindingStatus.ACTIVE,
            device_props=device_props,
        )

        logger.info(
            f"[PublishFlowService._update_binding_on_success] "
            f"Device binding updated: binding_id={binding_id}, status=ACTIVE"
        )

    def _release_binding(
        self,
        binding_id: int,
        *,
        destroy_publish_id: int | None,
    ) -> None:
        """Mark a device binding RELEASED after its bot was destroyed, stashing
        the destroy workflow id in ``device_props`` when BaaS returned one."""
        self._publish_service.update_device_binding_with_props(
            binding_id=binding_id,
            status=DeviceBindingStatus.RELEASED,
            device_props={"destroy_publish_id": destroy_publish_id} if destroy_publish_id else {},
        )
        logger.info(
            f"[PublishFlowService._release_binding] "
            f"Device binding status updated to RELEASED: binding_id={binding_id}"
        )
