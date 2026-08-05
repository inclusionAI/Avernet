"""Publish-record ARKA image-policy resolution."""

from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    IMAGE_POLICY_KEYS,
    ImagePolicyState,
    ServiceBotImagePin,
    has_explicit_image_policy,
    resolve_publish_image_pin as resolve_publish_image_pin_policy,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.utils.env_utils import get_current_env


class PublishImagePolicyMixin:
    """Resolve and persist image policy for publish-record operations."""

    def resolve_publish_image_pin(
        self,
        publish_record: BotPublishRecord,
        bot: dict,
    ) -> ServiceBotImagePin:
        """Resolve and, for legacy rows, atomically append the Pin snapshot."""
        if (
            self._baas_service.resolve_container_provider(bot)
            == TECLAW_DEVICE_PROVIDER
        ):
            return ServiceBotImagePin(ImagePolicyState.LEGACY, None)

        was_legacy = not has_explicit_image_policy(publish_record.ext)

        def _persist(snapshot: dict) -> None:
            def _mutate(latest_ext: dict) -> None:
                # Another concurrent operation may have already fixed the policy.
                # Never replace an explicit decision; otherwise append only our
                # owned fields to the latest ext snapshot.
                if has_explicit_image_policy(latest_ext):
                    return
                for key in IMAGE_POLICY_KEYS:
                    latest_ext.pop(key, None)
                for key in IMAGE_POLICY_KEYS:
                    if key in snapshot:
                        latest_ext[key] = snapshot[key]

            self._mutate_and_update_ext(publish_record.id, _mutate)

        resolved = resolve_publish_image_pin_policy(
            publish_record,
            common_config_service=self._common_config_service,
            env=get_current_env(),
            persist_ext=_persist,
        )
        if was_legacy and resolved.enabled:
            latest = self._publish_service.get_publish_by_id(publish_record.id)
            if latest is not None:
                publish_record.ext = latest.ext
                return resolve_publish_image_pin_policy(latest)
        return resolved
