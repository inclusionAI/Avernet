"""Publish-record ARCA image-policy resolution + the provider seam it shares.

Provider identity is a fact about the *bot's container*, not about the publish
record: it is resolved through ``BaasService.resolve_container_provider`` — the
same call the build stage makes to pick the artifact producer — so build and
every later deploy stage can never disagree. The publish record's ``ext`` holds
the frozen build artifact and the frozen image, never the provider.
"""

from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    ServiceBotImagePin,
)
from agentclaw.community.core.service_bot.services.publish_flow.provider_behavior import (
    ProviderBehavior,
)


class PublishImagePolicyMixin:
    """Provider resolution + the shared persisted image-policy resolver."""

    def device_provider(self, bot: dict) -> str:
        """``bot``'s container token — the single provider question in the flow."""
        return self._baas_service.resolve_container_provider(bot)

    def provider_behavior(self, bot: dict) -> ProviderBehavior:
        """The :class:`ProviderBehavior` for ``bot``'s container, resolved via the
        same ``resolve_container_provider`` mapping used for producer selection."""
        return self._provider_behaviors.resolve(self.device_provider(bot))

    def resolve_publish_image_pin(
        self,
        publish_record: BotPublishRecord,
        *,
        device_provider: str,
    ) -> ServiceBotImagePin:
        return self._publish_service.resolve_publish_image_pin(
            publish_record, device_provider=device_provider
        )
