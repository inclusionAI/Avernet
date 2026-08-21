from datetime import datetime
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    ImagePolicyState,
    ServiceBotImagePin,
)
from agentclaw.community.core.service_bot.services.publish_flow.image_policy_mixin import (
    PublishImagePolicyMixin,
)
from agentclaw.community.core.service_bot.services.publish_flow.provider_behavior import (
    DefaultProviderBehavior,
    ProviderBehaviorRouter,
)


def _record(ext=None) -> BotPublishRecord:
    return BotPublishRecord(
        id=1,
        source_bot_pk=1,
        source_bot_id="bot-1",
        publish_bot_id="bot-1",
        name="bot",
        owner_id="u1",
        status="success",
        version=2,
        env="pre",
        ext=ext,
        permission_owner="owner",
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


class _ImagePolicyHarness(PublishImagePolicyMixin):
    def __init__(self) -> None:
        self._publish_service = MagicMock()
        self._baas_service = MagicMock()
        self._provider_behaviors = ProviderBehaviorRouter(
            {"baas": DefaultProviderBehavior()}, default_provider_key="baas"
        )


def test_resolve_publish_image_pin_delegates_to_shared_publish_service_resolver():
    record = _record({"migration_path": "/build/v1"})
    expected = ServiceBotImagePin(ImagePolicyState.PINNED, "registry/arca:v2")
    svc = _ImagePolicyHarness()
    svc._publish_service.resolve_publish_image_pin.return_value = expected

    resolved = svc.resolve_publish_image_pin(record, device_provider="baas")

    assert resolved is expected
    svc._publish_service.resolve_publish_image_pin.assert_called_once_with(
        record, device_provider="baas"
    )


def test_device_provider_asks_the_bot_not_the_publish_record():
    """Provider identity comes from the bot's container, never from ``ext``."""
    svc = _ImagePolicyHarness()
    svc._baas_service.resolve_container_provider.return_value = "baas"
    bot = {"bot_id": "bot-1", "active_engine": "openclaw"}

    assert svc.device_provider(bot) == "baas"
    assert isinstance(svc.provider_behavior(bot), DefaultProviderBehavior)
    svc._baas_service.resolve_container_provider.assert_called_with(bot)
