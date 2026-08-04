from unittest.mock import MagicMock
from datetime import datetime

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    apply_image_pin_to_ext,
    copy_image_pin_to_ext,
    overlay_image_pin_on_template_config,
    resolve_current_arka_image,
    resolve_publish_image_pin,
)


def test_resolve_current_image_uses_enabled_common_config():
    service = MagicMock()
    service.get_value.return_value = {"image": " registry.example/arka:v2 "}

    assert resolve_current_arka_image(service, env="pre") == "registry.example/arka:v2"
    service.get_value.assert_called_once_with(
        business_code="service_bot",
        param_code="sbot_pin_image",
        env="pre",
        default=None,
        only_enabled=True,
    )


def test_apply_pin_preserves_service_bot_config_and_clears_stale_pin():
    ext = {
        "service_bot_config": {"device_count": 3},
        "sbot_pin_image": True,
        "sbot_docker_image": "old:v1",
    }

    assert apply_image_pin_to_ext(ext, None) == {
        "service_bot_config": {"device_count": 3}
    }
    assert apply_image_pin_to_ext(ext, "new:v2") == {
        "service_bot_config": {"device_count": 3},
        "sbot_pin_image": True,
        "sbot_docker_image": "new:v2",
    }


def test_publish_copy_is_whitelisted_and_template_overlay_is_non_mutating():
    source = {
        "service_bot_config": {"device_count": 3},
        "sbot_pin_image": True,
        "sbot_docker_image": "arka:v2",
    }
    target = {"config_artifact": {"schema_version": 4}}
    template = {"image": "template:v1", "envs": {"A": "1"}}

    assert copy_image_pin_to_ext(source, target) == {
        "config_artifact": {"schema_version": 4},
        "sbot_pin_image": True,
        "sbot_docker_image": "arka:v2",
    }
    assert overlay_image_pin_on_template_config(template, source) == {
        "image": "arka:v2",
        "envs": {"A": "1"},
    }
    assert template["image"] == "template:v1"


def test_resolve_publish_image_pin_reads_only_publish_ext():
    record = BotPublishRecord(
        id=1,
        source_bot_pk=1,
        source_bot_id="bot-1",
        publish_bot_id="bot-1",
        name="bot",
        owner_id="u1",
        status="success",
        version=2,
        env="pre",
        ext={"sbot_pin_image": True, "sbot_docker_image": "arka:v2"},
        permission_owner="owner",
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )

    resolved = resolve_publish_image_pin(record)

    assert resolved.enabled is True
    assert resolved.docker_image == "arka:v2"
