from datetime import datetime
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    ImagePolicyState,
    ServiceBotImagePin,
)
from agentclaw.community.core.service_bot.services.publish_flow.image_policy_mixin import (
    PublishImagePolicyMixin,
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


def test_resolve_publish_image_pin_delegates_to_shared_publish_service_resolver():
    record = _record({"migration_path": "/build/v1"})
    expected = ServiceBotImagePin(ImagePolicyState.PINNED, "registry/arka:v2")
    svc = _ImagePolicyHarness()
    svc._publish_service.resolve_publish_image_pin.return_value = expected

    resolved = svc.resolve_publish_image_pin(record)

    assert resolved is expected
    svc._publish_service.resolve_publish_image_pin.assert_called_once_with(record)
