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
SERVICE_BOT_RUNTIME_KIND_KEY = "sbot_runtime_kind"
RUNTIME_KIND_ARKA = "arka"
RUNTIME_KIND_TECLAW = "teclaw"
_RUNTIME_KINDS = {RUNTIME_KIND_ARKA, RUNTIME_KIND_TECLAW}



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


def runtime_kind_from_provider(device_provider: str | None) -> str | None:
    if device_provider == RUNTIME_KIND_TECLAW:
        return RUNTIME_KIND_TECLAW
    if device_provider in {"arca", "baas"}:
        return RUNTIME_KIND_ARKA
    return None


def apply_runtime_kind_to_ext(
    ext: dict[str, Any] | None, runtime_kind: str | None
) -> dict[str, Any] | None:
    updated = dict(ext or {})
    if runtime_kind in _RUNTIME_KINDS:
        updated[SERVICE_BOT_RUNTIME_KIND_KEY] = runtime_kind
    return updated or None


def resolve_publish_runtime_kind(
    publish_record: BotPublishRecord,
    *,
    binding_repository: Any | None = None,
) -> str:
    """Resolve runtime only from immutable publish-owned facts.

    New records carry ``sbot_runtime_kind``. Historical TeClaw records are
    recognized from their config artifact or stage bindings; the final fallback
    is ARKA because ARKA predates the external-runtime publish format.
    """
    ext = publish_record.ext or {}
    explicit = ext.get(SERVICE_BOT_RUNTIME_KIND_KEY)
    if explicit in _RUNTIME_KINDS:
        return explicit

    artifact = ext.get("config_artifact")
    if isinstance(artifact, dict) and artifact.get("engine_type") == RUNTIME_KIND_TECLAW:
        return RUNTIME_KIND_TECLAW

    binding_ids = (ext.get("binding") or {}).values() if isinstance(ext.get("binding"), dict) else ()
    if binding_repository is not None:
        for binding_id in binding_ids:
            try:
                resolved_binding_id = int(binding_id)
            except (TypeError, ValueError):
                continue
            binding = binding_repository.get_by_id(resolved_binding_id)
            kind = runtime_kind_from_provider(getattr(binding, "device_provider", None))
            if kind is not None:
                return kind

    logger.warning(
        "[arka_image_pin] publish runtime kind missing; using legacy ARKA fallback: publish_id=%s",
        publish_record.id,
    )
    return RUNTIME_KIND_ARKA


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


def persist_default_image_policy(
    *,
    bot_repository: Any,
    publish_repository: Any,
    bot_id: str,
    owner_id: str,
    env: str,
    max_cas_attempts: int = 3,
) -> None:
    """Idempotently persist DEFAULT to the current Bot and Draft after success."""
    bot = bot_repository.get_by_id_and_owner(bot_id, owner_id)
    if not isinstance(bot, dict):
        raise ImagePinPersistenceError(f"Bot not found while persisting image policy: {bot_id}")
    updated_bot_ext = apply_default_image_to_ext(bot.get("ext"))
    if not bot_repository.update_by_owner(bot_id, owner_id, {"ext": updated_bot_ext}):
        raise ImagePinPersistenceError(f"Bot image policy update conflicted: {bot_id}")

    for _attempt in range(max_cas_attempts):
        draft = publish_repository.get_draft_by_publish_bot_id(
            publish_bot_id=bot_id, env=env
        )
        if draft is None:
            return
        draft_ext = copy_image_policy_to_ext(updated_bot_ext, draft.ext) or {}
        if draft_ext == (draft.ext or {}):
            return
        updated = publish_repository.compare_and_set_ext(
            publish_id=draft.id, expected_ext=draft.ext, ext=draft_ext
        )
        if updated is not None:
            return
    raise ImagePinPersistenceError(
        f"Draft image policy CAS conflicted repeatedly: bot_id={bot_id}"
    )


class PublishImagePolicyResolver:
    """Shared persisted resolver used by publish flows and caller containers."""

    def __init__(
        self,
        *,
        publish_repository: Any,
        binding_repository: Any,
        common_config_service: CommonConfigService | None,
        max_cas_attempts: int = 3,
    ) -> None:
        self._publish_repository = publish_repository
        self._binding_repository = binding_repository
        self._common_config_service = common_config_service
        self._max_cas_attempts = max_cas_attempts

    def resolve(self, publish_record: BotPublishRecord) -> ServiceBotImagePin:
        """Return only an explicit policy or a legacy decision persisted by CAS."""
        for _attempt in range(self._max_cas_attempts):
            latest = self._publish_repository.get_by_id(publish_record.id)
            if latest is None:
                raise ImagePinPersistenceError(
                    f"Publish record disappeared while resolving image policy: {publish_record.id}"
                )
            if resolve_publish_runtime_kind(
                latest, binding_repository=self._binding_repository
            ) == RUNTIME_KIND_TECLAW:
                publish_record.ext = latest.ext
                return ServiceBotImagePin(ImagePolicyState.LEGACY, None)

            if has_explicit_image_policy(latest.ext):
                publish_record.ext = latest.ext
                return resolve_publish_image_pin(latest)

            image = resolve_current_arka_image(
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
                    "[arka_image_pin] snapshotted legacy publish image: "
                    "publish_id=%s env=%s image=%s",
                    updated.id,
                    updated.env,
                    image,
                )
                return resolve_publish_image_pin(updated)

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
