"""Device-binding ops, mixed into ``PublishFlowService``.

Owns writes to the device-binding table for the publish flow. Kept distinct from
the publish-record ext writes (``PublishExtMixin``) and the BaaS approve
(``BaasPublishOpsMixin``); the release runner invokes create-binding, record-ext,
then approve in sequence.
"""
from __future__ import annotations

from agentclaw.community.core.devices.models import DeviceBindingStatus
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
