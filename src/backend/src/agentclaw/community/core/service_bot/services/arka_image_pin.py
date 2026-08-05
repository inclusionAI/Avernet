"""ARKA service-bot image policy helpers.

New bots and successful draft restarts explicitly opt into the BaaS/ARKA
default image. Published operations are reproducible: they read only the target
publish record. Records created before the policy existed are lazily protected
by the environment common-config and snapshot the configured image once.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.log import get_logger

logger = get_logger()

IMAGE_PIN_BUSINESS_CODE = "service_bot"
IMAGE_PIN_PARAM_CODE = "sbot_pin_image"
IMAGE_DEFAULT_KEY = "sbot_use_default_image"
IMAGE_PIN_ENABLED_KEY = "sbot_pin_image"
IMAGE_PIN_VALUE_KEY = "sbot_docker_image"
IMAGE_POLICY_KEYS = (
    IMAGE_DEFAULT_KEY,
    IMAGE_PIN_ENABLED_KEY,
    IMAGE_PIN_VALUE_KEY,
)


class ImagePolicyState(str, Enum):
    DEFAULT = "default"
    PINNED = "pinned"
    LEGACY = "legacy"


class ImagePinConfigError(ValueError):
    """Enabled image-pin configuration or snapshot is malformed."""


@dataclass(frozen=True)
class ServiceBotImagePin:
    """Resolved image policy for one publish record."""

    state: ImagePolicyState
    docker_image: str | None

    @property
    def enabled(self) -> bool:
        """Backward-compatible pinned predicate."""
        return self.state == ImagePolicyState.PINNED


PublishExtWriter = Callable[[dict[str, Any]], None]


def resolve_current_arka_image(
    common_config_service: CommonConfigService | None,
    *,
    env: str,
) -> str | None:
    """Return the enabled legacy-protection image.

    Missing/disabled configuration returns ``None``. An enabled but malformed
    value fails closed so a historical bot is not silently moved to the current
    default image.
    """
    if common_config_service is None:
        return None
    value = common_config_service.get_value(
        business_code=IMAGE_PIN_BUSINESS_CODE,
        param_code=IMAGE_PIN_PARAM_CODE,
        env=env,
        default=None,
        only_enabled=True,
    )
    if value is None:
        return None
    image = value.get("image") if isinstance(value, dict) else None
    if isinstance(image, str) and image.strip():
        return image.strip()
    raise ImagePinConfigError(
        f"Enabled {IMAGE_PIN_PARAM_CODE} config has no valid image: env={env}"
    )


def has_explicit_image_policy(ext: dict[str, Any] | None) -> bool:
    return isinstance(ext, dict) and any(key in ext for key in IMAGE_POLICY_KEYS)


def apply_default_image_to_ext(ext: dict[str, Any] | None) -> dict[str, Any]:
    """Mark default-image behavior while preserving unrelated ext fields."""
    updated = dict(ext or {})
    updated.pop(IMAGE_PIN_ENABLED_KEY, None)
    updated.pop(IMAGE_PIN_VALUE_KEY, None)
    updated[IMAGE_DEFAULT_KEY] = True
    return updated


def apply_image_pin_to_ext(
    ext: dict[str, Any] | None,
    image: str | None,
) -> dict[str, Any]:
    """Set/clear a Pin snapshot while preserving unrelated ext fields."""
    updated = dict(ext or {})
    updated.pop(IMAGE_DEFAULT_KEY, None)
    updated.pop(IMAGE_PIN_ENABLED_KEY, None)
    updated.pop(IMAGE_PIN_VALUE_KEY, None)
    if image:
        updated[IMAGE_PIN_ENABLED_KEY] = True
        updated[IMAGE_PIN_VALUE_KEY] = image
    return updated


def read_image_pin_from_ext(ext: dict[str, Any] | None) -> str | None:
    if not isinstance(ext, dict) or ext.get(IMAGE_PIN_ENABLED_KEY) is not True:
        return None
    image = ext.get(IMAGE_PIN_VALUE_KEY)
    return image.strip() if isinstance(image, str) and image.strip() else None


def copy_image_policy_to_ext(
    source_ext: dict[str, Any] | None,
    target_ext: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Whitelist-copy an explicit default/Pin policy into a publish ext."""
    target = dict(target_ext or {})
    for key in IMAGE_POLICY_KEYS:
        target.pop(key, None)
    if isinstance(source_ext, dict) and source_ext.get(IMAGE_DEFAULT_KEY) is True:
        target[IMAGE_DEFAULT_KEY] = True
    else:
        image = read_image_pin_from_ext(source_ext)
        if image:
            target[IMAGE_PIN_ENABLED_KEY] = True
            target[IMAGE_PIN_VALUE_KEY] = image
    return target or None


def resolve_publish_image_pin(
    publish_record: BotPublishRecord,
    *,
    common_config_service: CommonConfigService | None = None,
    env: str | None = None,
    persist_ext: PublishExtWriter | None = None,
) -> ServiceBotImagePin:
    """Resolve default/pinned/legacy policy from the target publish record.

    Explicit publish snapshots never consult common-config. A legacy record
    consults it once; when enabled, the selected image is appended to ``ext``
    through ``persist_ext`` before being used. Disabled/missing config leaves the
    record legacy and uses the provider default for this operation.
    """
    ext = dict(publish_record.ext or {})
    if ext.get(IMAGE_DEFAULT_KEY) is True:
        return ServiceBotImagePin(ImagePolicyState.DEFAULT, None)

    pin_enabled = ext.get(IMAGE_PIN_ENABLED_KEY) is True
    image = read_image_pin_from_ext(ext)
    if pin_enabled:
        if image is None:
            raise ImagePinConfigError(
                f"Publish {publish_record.id} enables image Pin without a valid image"
            )
        return ServiceBotImagePin(ImagePolicyState.PINNED, image)

    # Any dangling image-policy field is malformed rather than legacy.
    if has_explicit_image_policy(ext):
        raise ImagePinConfigError(
            f"Publish {publish_record.id} has an inconsistent image policy snapshot"
        )

    image = resolve_current_arka_image(
        common_config_service,
        env=env or publish_record.env,
    )
    if image is None:
        return ServiceBotImagePin(ImagePolicyState.LEGACY, None)

    pinned_ext = apply_image_pin_to_ext(ext, image)
    if persist_ext is not None:
        persist_ext(pinned_ext)
    publish_record.ext = pinned_ext
    logger.info(
        "[arka_image_pin] snapshotted legacy publish image: publish_id=%s env=%s image=%s",
        publish_record.id,
        env or publish_record.env,
        image,
    )
    return ServiceBotImagePin(ImagePolicyState.PINNED, image)


def overlay_image_pin_on_template_config(
    template_config: dict[str, Any] | None,
    bot_ext: dict[str, Any] | None,
) -> dict[str, Any] | None:
    image = read_image_pin_from_ext(bot_ext)
    if not image:
        return template_config
    updated = dict(template_config or {})
    updated["image"] = image
    return updated
