"""Publish-record ARCA image-policy resolution."""

from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    RUNTIME_KIND_TECLAW,
    ServiceBotImagePin,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    DEFAULT_DEVICE_PROVIDER,
    TECLAW_DEVICE_PROVIDER,
)


class PublishImagePolicyMixin:
    """Delegate every publish path to the shared persisted policy resolver."""

    def resolve_publish_image_pin(
        self, publish_record: BotPublishRecord
    ) -> ServiceBotImagePin:
        return self._publish_service.resolve_publish_image_pin(publish_record)

    def resolve_publish_runtime_kind(self, publish_record: BotPublishRecord) -> str:
        return self._publish_service.resolve_publish_runtime_kind(publish_record)

    def _publish_provider_behavior(self, publish_record: BotPublishRecord):
        """Resolve behavior from immutable Publish metadata/bindings."""
        runtime_kind = self.resolve_publish_runtime_kind(publish_record)
        provider = (
            TECLAW_DEVICE_PROVIDER
            if runtime_kind == RUNTIME_KIND_TECLAW
            else DEFAULT_DEVICE_PROVIDER
        )
        return self._provider_behaviors.resolve(provider)
