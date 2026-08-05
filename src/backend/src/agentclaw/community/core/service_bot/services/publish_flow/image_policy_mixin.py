"""Publish-record ARKA image-policy resolution."""

from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    ServiceBotImagePin,
)


class PublishImagePolicyMixin:
    """Delegate every publish path to the shared persisted policy resolver."""

    def resolve_publish_image_pin(
        self, publish_record: BotPublishRecord
    ) -> ServiceBotImagePin:
        return self._publish_service.resolve_publish_image_pin(publish_record)
