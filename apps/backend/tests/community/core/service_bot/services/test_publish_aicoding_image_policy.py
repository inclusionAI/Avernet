"""Service-bot image policy excludes AICoding without changing deploy callers."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService
from agentclaw.community.core.service_bot.services.arca_image_pin import ImagePolicyState
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import TECLAW_DEVICE_PROVIDER


def _service():
    service = BotPublishService.__new__(BotPublishService)
    service._bot_service = MagicMock()
    service._image_policy_resolver = MagicMock()
    return service


@pytest.mark.parametrize("ext", [
    {}, {"sbot_use_default_image": True},
    {"sbot_pin_image": True, "sbot_docker_image": "old:image"},
    {"sbot_pin_image": True},
])
def test_aicoding_returns_default_without_resolving_or_writing_pin(ext):
    service = _service()
    service._bot_service.resolve_bot_template_uid.return_value = "aicoding_bot_template"
    record = SimpleNamespace(source_bot_id="source", owner_id="owner", env="pre", ext=ext)
    original = deepcopy(ext)
    result = service.resolve_publish_image_pin(record, device_provider="baas")
    assert result.state == ImagePolicyState.DEFAULT
    assert result.docker_image is None
    assert record.ext == original
    service._image_policy_resolver.resolve.assert_not_called()
    service._bot_service.resolve_bot_template_uid.assert_called_once_with(
        bot_id="source", user_id="owner", env="pre",
    )


@pytest.mark.parametrize("template_uid", ["default_bot_template", "claude_code_bot_template"])
def test_arca_templates_delegate_to_image_policy_resolver(template_uid):
    service = _service()
    service._bot_service.resolve_bot_template_uid.return_value = template_uid
    record = SimpleNamespace(source_bot_id="source", owner_id="owner", env="pre")
    result = service.resolve_publish_image_pin(record, device_provider="baas")
    assert result is service._image_policy_resolver.resolve.return_value
    service._image_policy_resolver.resolve.assert_called_once_with(record, device_provider="baas")


@pytest.mark.parametrize(
    "template_uid", ["aicoding_bot_template", "some_other_template", None]
)
def test_non_arca_template_returns_default_without_resolving(template_uid):
    service = _service()
    service._bot_service.resolve_bot_template_uid.return_value = template_uid
    record = SimpleNamespace(source_bot_id="source", owner_id="owner", env="pre", ext={})
    result = service.resolve_publish_image_pin(record, device_provider="baas")
    assert result.state == ImagePolicyState.DEFAULT
    service._image_policy_resolver.resolve.assert_not_called()


def test_teclaw_does_not_require_template_lookup():
    service = _service()
    record = SimpleNamespace()
    service.resolve_publish_image_pin(record, device_provider=TECLAW_DEVICE_PROVIDER)
    service._bot_service.resolve_bot_template_uid.assert_not_called()
    service._image_policy_resolver.resolve.assert_called_once_with(
        record, device_provider=TECLAW_DEVICE_PROVIDER,
    )


def test_identity_failure_does_not_fall_through_to_pin():
    service = _service()
    service._bot_service.resolve_bot_template_uid.side_effect = RuntimeError("lookup failed")
    record = SimpleNamespace(source_bot_id="source", owner_id="owner", env="pre")
    with pytest.raises(RuntimeError, match="lookup failed"):
        service.resolve_publish_image_pin(record, device_provider="baas")
    service._image_policy_resolver.resolve.assert_not_called()


def test_bot_identity_uses_persisted_context_without_uuid_resolution():
    service = BotService.__new__(BotService)
    service._repository = MagicMock()
    service._repository.get_by_id_and_owner.return_value = {
        "active_engine": "claude_code", "bot_type": "service",
        "template_type": "applicationCoding",
    }
    service._template_service = MagicMock()
    config = {"template_uid": "aicoding_bot_template", "image": "untouched:image"}
    service._template_service.get_template_config_strict.return_value = config
    service._baas_template_resolver = MagicMock()
    service._baas_template_resolver.resolve_template_uid.return_value = "aicoding_bot_template"
    assert service.resolve_bot_template_uid(
        bot_id="source", user_id="owner", env="pre",
    ) == "aicoding_bot_template"
    service._baas_template_resolver.resolve_template_uid.assert_called_once_with(
        bot_id="source", user_id="owner", env="pre", bot_type="service",
        engine_type="claude_code", template_type="applicationCoding", template_config=config,
    )
    service._baas_template_resolver.resolve_template.assert_not_called()
    assert config["image"] == "untouched:image"
    service._template_service.get_template_config_strict.side_effect = RuntimeError("DB unavailable")
    service._baas_template_resolver.reset_mock()
    with pytest.raises(RuntimeError, match="DB unavailable"):
        service.resolve_bot_template_uid(bot_id="source", user_id="owner", env="pre")
    service._baas_template_resolver.resolve_template_uid.assert_not_called()
