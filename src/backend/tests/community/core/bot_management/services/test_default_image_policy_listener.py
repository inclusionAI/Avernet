from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.default_image_policy_listener import (
    DEFAULT_IMAGE_POLICY_VALUE,
    IMAGE_POLICY_ON_ACTIVE_KEY,
    DefaultImagePolicyActivationListener,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import DeviceActivatedEvent


def _event() -> DeviceActivatedEvent:
    return DeviceActivatedEvent(
        device_id="device-7",
        binding_id=7,
        entity_id="staff-u1",
        entity_type="staff",
        device_provider="arca",
    )


def _listener(*, marker=DEFAULT_IMAGE_POLICY_VALUE, active_engine="openclaw"):
    bot_repo = MagicMock()
    bot_repo.get_by_binding_id.return_value = {
        "bot_id": "bot-1",
        "owner_id": "u1",
        "bot_type": "service",
        "active_engine": active_engine,
    }
    publish_repo = MagicMock()
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = SimpleNamespace(
        id=7,
        env="pre",
        device_props={IMAGE_POLICY_ON_ACTIVE_KEY: marker},
    )
    return (
        DefaultImagePolicyActivationListener(
            bot_repository=bot_repo,
            publish_repository=publish_repo,
            binding_repository=binding_repo,
        ),
        bot_repo,
        publish_repo,
        binding_repo,
    )


@pytest.mark.asyncio
async def test_startup_subscribes_once():
    reset_event_bus()
    listener, *_ = _listener()

    await listener.startup()
    await listener.startup()

    assert get_event_bus()._handlers[DeviceActivatedEvent] == [listener.handle]
    reset_event_bus()


def test_activation_persists_default_then_clears_intent():
    listener, bot_repo, publish_repo, binding_repo = _listener()

    with patch(
        "agentclaw.community.core.bot_management.services."
        "default_image_policy_listener.persist_default_image_policy"
    ) as persist:
        listener.handle(_event())

    persist.assert_called_once_with(
        bot_repository=bot_repo,
        publish_repository=publish_repo,
        bot_id="bot-1",
        owner_id="u1",
        env="pre",
    )
    binding_repo.update_device_props.assert_called_once_with(
        binding_id=7,
        props={IMAGE_POLICY_ON_ACTIVE_KEY: None},
    )


def test_activation_without_restart_intent_is_ignored():
    listener, _bot_repo, _publish_repo, binding_repo = _listener(marker=None)

    with patch(
        "agentclaw.community.core.bot_management.services."
        "default_image_policy_listener.persist_default_image_policy"
    ) as persist:
        listener.handle(_event())

    persist.assert_not_called()
    binding_repo.update_device_props.assert_not_called()


def test_persistence_failure_keeps_restart_intent():
    listener, _bot_repo, _publish_repo, binding_repo = _listener()

    with patch(
        "agentclaw.community.core.bot_management.services."
        "default_image_policy_listener.persist_default_image_policy",
        side_effect=RuntimeError("db unavailable"),
    ), pytest.raises(RuntimeError, match="db unavailable"):
        listener.handle(_event())

    binding_repo.update_device_props.assert_not_called()


def test_teclaw_activation_is_ignored():
    listener, _bot_repo, _publish_repo, binding_repo = _listener(
        active_engine="teclaw"
    )

    with patch(
        "agentclaw.community.core.bot_management.services."
        "default_image_policy_listener.persist_default_image_policy"
    ) as persist:
        listener.handle(_event())

    persist.assert_not_called()
    binding_repo.update_device_props.assert_not_called()
