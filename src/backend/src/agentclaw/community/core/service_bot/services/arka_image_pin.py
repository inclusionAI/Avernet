"""ARKA service-bot image pin policy helpers.

The environment switch lives in ``ac_common_config``.  Bot and publish ``ext``
only carry the resolved snapshot so later operations (notably scale-up) do not
silently move to a newer image.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.log import get_logger

logger = get_logger()

IMAGE_PIN_BUSINESS_CODE = "service_bot"
IMAGE_PIN_PARAM_CODE = "sbot_pin_image"
IMAGE_PIN_ENABLED_KEY = "sbot_pin_image"
IMAGE_PIN_VALUE_KEY = "sbot_docker_image"


@dataclass(frozen=True)
class ServiceBotImagePin:
    """Immutable ARKA image snapshot resolved from one publish record."""

    enabled: bool
    docker_image: str | None


def resolve_current_arka_image(
    common_config_service: CommonConfigService | None,
    *,
    env: str,
) -> str | None:
    """Return the enabled ARKA image, or ``None`` for disabled/invalid config."""
    if common_config_service is None:
        return None
    value = common_config_service.get_value(
        business_code=IMAGE_PIN_BUSINESS_CODE,
        param_code=IMAGE_PIN_PARAM_CODE,
        env=env,
        default=None,
        only_enabled=True,
    )
    image = value.get("image") if isinstance(value, dict) else None
    if isinstance(image, str) and image.strip():
        return image.strip()
    if value is not None:
        logger.warning(
            "[arka_image_pin] ignore invalid enabled config: env=%s value=%r",
            env,
            value,
        )
    return None


def apply_image_pin_to_ext(
    ext: dict[str, Any] | None,
    image: str | None,
) -> dict[str, Any]:
    """Update only image-pin-owned keys while preserving unrelated Bot ext."""
    updated = dict(ext or {})
    updated.pop(IMAGE_PIN_ENABLED_KEY, None)
    updated.pop(IMAGE_PIN_VALUE_KEY, None)
    if image:
        updated[IMAGE_PIN_ENABLED_KEY] = True
        updated[IMAGE_PIN_VALUE_KEY] = image
    return updated


def read_image_pin_from_ext(ext: dict[str, Any] | None) -> str | None:
    """Read a valid immutable image snapshot from Bot/publish ext."""
    if not isinstance(ext, dict) or ext.get(IMAGE_PIN_ENABLED_KEY) is not True:
        return None
    image = ext.get(IMAGE_PIN_VALUE_KEY)
    return image.strip() if isinstance(image, str) and image.strip() else None


def resolve_publish_image_pin(
    publish_record: BotPublishRecord,
) -> ServiceBotImagePin:
    """Resolve the ARKA image snapshot owned by ``publish_record``.

    Published lifecycle operations must be reproducible, so this resolver never
    falls back to the environment's current common-config value.
    """
    image = read_image_pin_from_ext(publish_record.ext)
    return ServiceBotImagePin(enabled=image is not None, docker_image=image)


def copy_image_pin_to_ext(
    source_ext: dict[str, Any] | None,
    target_ext: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Whitelist-copy the Bot image snapshot into a publish ext."""
    target = apply_image_pin_to_ext(target_ext, read_image_pin_from_ext(source_ext))
    return target or None


def overlay_image_pin_on_template_config(
    template_config: dict[str, Any] | None,
    bot_ext: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Overlay a pinned image for one ARKA operation without mutating templates."""
    image = read_image_pin_from_ext(bot_ext)
    if not image:
        return template_config
    updated = dict(template_config or {})
    updated["image"] = image
    return updated
