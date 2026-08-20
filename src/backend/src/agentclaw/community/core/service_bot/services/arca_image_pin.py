"""ARCA service-bot image policy helpers.

New bots and successful draft restarts explicitly opt into the BaaS/ARCA
default image. The *image* a published operation ships is reproducible: it is
read only from the target publish record. Records created before the policy
existed are lazily protected by the environment common-config and snapshot the
configured image once.

:class:`PublishImagePolicyResolver` is the single entry point for that: it owns
the repository read and the CAS write. ``image_policy_from_ext`` beside it is
only the pure decoder it uses internally — it reads a record's existing policy
and never acquires one, so it is not a second way to "resolve" the policy.

Which *provider* a bot runs on is deliberately NOT a publish-record fact. The
container follows the bot (``resolve_container_provider``) and its device
binding; callers pass the resolved ``device_provider`` in rather than having it
sniffed back out of the record's ``ext`` blob, so the image policy can never
disagree with the container the build and release stages actually target.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
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


class ImagePinPersistenceError(RuntimeError):
    """A legacy publish image could not be durably snapshotted."""


@dataclass(frozen=True)
class ServiceBotImagePin:
    """Resolved image policy for one publish record."""

    state: ImagePolicyState
    docker_image: str | None

    @property
    def enabled(self) -> bool:
        """Backward-compatible pinned predicate."""
        return self.state == ImagePolicyState.PINNED


def resolve_current_arca_image(
    common_config_service: CommonConfigService | None,
    *,
    env: str,
) -> str | None:
    """Return the enabled legacy-protection image.

    Missing, disabled, or lacking a valid image means the feature is inactive
    and preserves the historical platform-default behavior.
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
    return None


def has_explicit_image_policy(ext: dict[str, Any] | None) -> bool:
    return isinstance(ext, dict) and any(key in ext for key in IMAGE_POLICY_KEYS)


def apply_default_image_to_ext(ext: dict[str, Any] | None) -> dict[str, Any]:
    """Mark default-image behavior while preserving unrelated ext fields."""
    updated = dict(ext or {})
    updated.pop(IMAGE_PIN_ENABLED_KEY, None)
    updated.pop(IMAGE_PIN_VALUE_KEY, None)
    updated[IMAGE_DEFAULT_KEY] = True
    return updated


def clear_image_policy_from_ext(
    ext: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Remove all image-policy fields while preserving unrelated ext fields."""
    updated = dict(ext or {})
    for key in IMAGE_POLICY_KEYS:
        updated.pop(key, None)
    return updated or None


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


def image_policy_from_ext(publish_record: BotPublishRecord) -> ServiceBotImagePin:
    """Decode the policy a publish record already carries on its ``ext``.

    Pure: no repository, no common-config, no writes. A record with no explicit
    policy is LEGACY — deciding whether such a record should *acquire* one is the
    persisted, CAS-backed job of :class:`PublishImagePolicyResolver`, which is the
    only thing callers outside this module should use.
    """
    ext = dict(publish_record.ext or {})
    if ext.get(IMAGE_DEFAULT_KEY) is True:
        return ServiceBotImagePin(ImagePolicyState.DEFAULT, None)

    image = read_image_pin_from_ext(ext)
    if ext.get(IMAGE_PIN_ENABLED_KEY) is True:
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

    return ServiceBotImagePin(ImagePolicyState.LEGACY, None)


def persist_default_image_policy(
    *,
    bot_repository: Any,
    publish_repository: Any,
    bot_id: str,
    owner_id: str,
    env: str,
    common_config_service: CommonConfigService | None,
    max_cas_attempts: int = 3,
) -> bool:
    """Persist DEFAULT only while the fully configured master switch is active.

    The restart intent can outlive the request that created it. Re-checking the
    CommonConfig here prevents an asynchronous completion from adding image
    policy fields after the switch was disabled, deleted, or lost its image.
    Existing Bot/Draft markers are ignored but left untouched.
    """
    if resolve_current_arca_image(common_config_service, env=env) is None:
        logger.info(
            "[arca_image_pin] skipped default-image policy because it is "
            "inactive: bot_id=%s env=%s",
            bot_id,
            env,
        )
        return False

    updated_bot_ext: dict[str, Any] | None = None
    for _attempt in range(max_cas_attempts):
        bot = bot_repository.get_by_id_and_owner(bot_id, owner_id)
        if not isinstance(bot, dict):
            raise ImagePinPersistenceError(
                f"Bot not found while persisting image policy: {bot_id}"
            )
        current_bot_ext = bot.get("ext")
        updated_bot_ext = apply_default_image_to_ext(current_bot_ext)
        if updated_bot_ext == (current_bot_ext or {}):
            break
        updated_bot = bot_repository.compare_and_set_ext(
            bot_id=bot_id,
            owner_id=owner_id,
            expected_ext=current_bot_ext,
            ext=updated_bot_ext,
        )
        if updated_bot is not None:
            break
    else:
        raise ImagePinPersistenceError(
            f"Bot image policy CAS conflicted repeatedly: {bot_id}"
        )

    assert updated_bot_ext is not None

    for _attempt in range(max_cas_attempts):
        draft = publish_repository.get_draft_by_publish_bot_id(
            publish_bot_id=bot_id, env=env
        )
        if draft is None:
            return True
        draft_ext = copy_image_policy_to_ext(updated_bot_ext, draft.ext) or {}
        if draft_ext == (draft.ext or {}):
            return True
        updated = publish_repository.compare_and_set_ext(
            publish_id=draft.id, expected_ext=draft.ext, ext=draft_ext
        )
        if updated is not None:
            return True
    raise ImagePinPersistenceError(
        f"Draft image policy CAS conflicted repeatedly: bot_id={bot_id}"
    )


class PublishImagePolicyResolver:
    """Shared persisted resolver used by publish flows and caller containers."""

    def __init__(
        self,
        *,
        publish_repository: Any,
        common_config_service: CommonConfigService | None,
        max_cas_attempts: int = 3,
    ) -> None:
        self._publish_repository = publish_repository
        self._common_config_service = common_config_service
        self._max_cas_attempts = max_cas_attempts

    def resolve(
        self,
        publish_record: BotPublishRecord,
        *,
        device_provider: str,
    ) -> ServiceBotImagePin:
        """Return only an explicit policy or a legacy decision persisted by CAS.

        ``device_provider`` is the caller's already-resolved container token (the
        same value that selects the build producer and the provider behavior). It
        is required rather than re-derived here so the image policy and the
        deployed container can never disagree.
        """
        for _attempt in range(self._max_cas_attempts):
            latest = self._publish_repository.get_by_id(publish_record.id)
            if latest is None:
                raise ImagePinPersistenceError(
                    f"Publish record disappeared while resolving image policy: {publish_record.id}"
                )
            # The teclaw container owns its own image; the ARCA policy — including
            # the lazy legacy snapshot below — never applies to it.
            if device_provider == TECLAW_DEVICE_PROVIDER:
                publish_record.ext = latest.ext
                return ServiceBotImagePin(ImagePolicyState.LEGACY, None)

            if has_explicit_image_policy(latest.ext):
                publish_record.ext = latest.ext
                return image_policy_from_ext(latest)

            image = resolve_current_arca_image(
                self._common_config_service, env=latest.env
            )
            if image is None:
                publish_record.ext = latest.ext
                return ServiceBotImagePin(ImagePolicyState.LEGACY, None)

            pinned_ext = apply_image_pin_to_ext(latest.ext, image)
            updated = self._publish_repository.compare_and_set_ext(
                publish_id=latest.id,
                expected_ext=latest.ext,
                ext=pinned_ext,
            )
            if updated is not None:
                publish_record.ext = updated.ext
                logger.info(
                    "[arca_image_pin] snapshotted legacy publish image: "
                    "publish_id=%s env=%s image=%s",
                    updated.id,
                    updated.env,
                    image,
                )
                return image_policy_from_ext(updated)

        raise ImagePinPersistenceError(
            f"Publish image policy CAS conflicted repeatedly: publish_id={publish_record.id}"
        )


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
